"""Geometry Nodes instancing: ONE object per unique model.

Each model = a point cloud (one point per prop, attributes apx_rot / apx_scale) plus a shared
Geometry Nodes modifier that, for every point:
  - picks the LOD from the distance to the probe (APX_LOD_Probe),
  - hides the prop when it is OUTSIDE THE FIELD OF VIEW (frustum culling),
  - hides the prop when it is TOO SMALL ON SCREEN (small far-away objects),
  - hides the prop beyond the culling distance (optional),
  - hides the prop OUTSIDE THE ACTIVE REGION (APX_Region disc mesh, see regions.py).

The probe is an empty that the timer moves onto the 3D view (or the camera):
  location = eye, rotation = view orientation (looking down -Z),
  scale    = (tan(fov_x/2), tan(fov_y/2), mode) with mode: 1 = frustum + screen size,
             0.5 = screen size only (render), 0 = nothing (orthographic view).
Everything is computed by Blender (C++), nothing per prop in Python.
"""

import bpy
import numpy as np

GROUP = "APX_Instancer"
GROUP_VERSION = 8          # 5: region = disc mesh (several POIs); 6: LOD cross-fade ("Fade Width", r5_fade)
                           # 7: final render values ("R ..." sockets, Is Viewport / Active Camera)
                           # 8: "Keep Size": props larger than this are never hidden by their size on screen
MAX_LEVELS = 5
PROBE = "APX_LOD_Probe"
FADE_ATTR = "r5_fade"      # instance attribute read by the LOD fade shader mask (motion.py)


# --------------------------------------------------------------------------
# Node group
# --------------------------------------------------------------------------
def _sock(ng, name, kind, in_out="INPUT", default=None):
    s = ng.interface.new_socket(name, in_out=in_out, socket_type=kind)
    if default is not None and hasattr(s, "default_value"):
        s.default_value = default
    return s


def get_group():
    ng = bpy.data.node_groups.get(GROUP)
    if ng is not None and ng.get("apx_ver") == GROUP_VERSION:
        return ng
    old = ng
    ng = bpy.data.node_groups.new(GROUP + "_new", "GeometryNodeTree")
    ng["apx_ver"] = GROUP_VERSION
    _build(ng)
    if old is not None:
        # Upgrade from an older version: swap the group in every modifier
        for o in bpy.data.objects:
            for m in o.modifiers:
                if m.type == "NODES" and m.node_group == old:
                    m.node_group = ng
        bpy.data.node_groups.remove(old)
    ng.name = GROUP
    return ng


def _build(ng):
    _sock(ng, "Geometry", "NodeSocketGeometry")
    _sock(ng, "Geometry", "NodeSocketGeometry", "OUTPUT")
    _sock(ng, "Probe", "NodeSocketObject")
    for i in range(MAX_LEVELS):
        _sock(ng, f"LOD{i}", "NodeSocketCollection")
    _sock(ng, "Levels", "NodeSocketInt", default=1)
    for i in range(1, MAX_LEVELS):
        _sock(ng, f"D{i}", "NodeSocketFloat", default=1e9)
    _sock(ng, "Dist Scale", "NodeSocketFloat", default=1.0)
    _sock(ng, "Cull", "NodeSocketFloat", default=1e9)
    _sock(ng, "Use LOD", "NodeSocketBool", default=True)
    _sock(ng, "Forced", "NodeSocketInt", default=0)
    _sock(ng, "Use Cull", "NodeSocketBool", default=False)
    _sock(ng, "Radius", "NodeSocketFloat", default=1.0)
    _sock(ng, "Use Frustum", "NodeSocketBool", default=True)
    _sock(ng, "Frustum Margin", "NodeSocketFloat", default=1.5)
    _sock(ng, "Min Screen", "NodeSocketFloat", default=0.0)
    _sock(ng, "Region", "NodeSocketObject")
    _sock(ng, "Fade Width", "NodeSocketFloat", default=0.0)     # LOD cross-fade band (fraction of each distance)
    # Final render values (7). They are set beforehand from the main thread: nothing is changed during a render
    # (changing the modifiers from the render handlers crashed animation renders while the interface redrew).
    # R Camera: the probe is the scene's active camera (LOD from the camera on every frame).
    _sock(ng, "R Use LOD", "NodeSocketBool", default=False)
    _sock(ng, "R Forced", "NodeSocketInt", default=0)
    _sock(ng, "R Min Screen", "NodeSocketFloat", default=0.0)
    _sock(ng, "R Fade Width", "NodeSocketFloat", default=0.0)
    _sock(ng, "R Camera", "NodeSocketBool", default=False)
    _sock(ng, "R TanX", "NodeSocketFloat", default=1.0)
    _sock(ng, "R TanY", "NodeSocketFloat", default=1.0)
    _sock(ng, "R Mode", "NodeSocketFloat", default=0.5)
    _sock(ng, "Keep Size", "NodeSocketFloat", default=1e9)    # (8) radius x scale above which the size is ignored

    N, L = ng.nodes, ng.links
    gi = N.new("NodeGroupInput"); gi.location = (-2600, 0)
    go = N.new("NodeGroupOutput"); go.location = (1100, 0)
    G0 = gi.outputs

    def link(a, b):
        L.new(a, b)

    def math(op, a, b=None, *rest):
        # math(op, a, b, loc) or math(op, a, b, c, loc) for MULTIPLY_ADD
        c, loc = (rest if len(rest) == 2 else (None, rest[0] if rest else (0, 0)))
        n = N.new("ShaderNodeMath"); n.operation = op; n.location = loc
        for idx, v in ((0, a), (1, b), (2, c)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[idx].default_value = v
            else:
                link(v, n.inputs[idx])
        return n.outputs[0]

    def compare(op, a, b, loc):
        n = N.new("FunctionNodeCompare"); n.data_type = "FLOAT"; n.operation = op; n.location = loc
        for idx, v in ((0, a), (1, b)):
            if isinstance(v, (int, float)):
                n.inputs[idx].default_value = v
            else:
                link(v, n.inputs[idx])
        return n.outputs[0]

    def boolean(op, a, b, loc):
        n = N.new("FunctionNodeBooleanMath"); n.operation = op; n.location = loc
        link(a, n.inputs[0])
        if b is not None:
            link(b, n.inputs[1])
        return n.outputs[0]

    # --- viewport / final render --------------------------------------------
    isv = N.new("GeometryNodeIsViewport"); isv.location = (-2600, 900)
    rendering = boolean("NOT", isv.outputs[0], None, (-2450, 900))

    def pick(kind, viewport, render, loc):
        """Switch on 'final render': viewport value / render value."""
        n = N.new("GeometryNodeSwitch"); n.input_type = kind; n.location = loc
        link(rendering, n.inputs[0]); link(viewport, n.inputs[1]); link(render, n.inputs[2])
        return n.outputs[0]

    G = {s.name: s for s in G0}
    G["Use LOD"] = pick("BOOLEAN", G0["Use LOD"], G0["R Use LOD"], (-2300, 1000))
    G["Forced"] = pick("INT", G0["Forced"], G0["R Forced"], (-2300, 940))
    G["Min Screen"] = pick("FLOAT", G0["Min Screen"], G0["R Min Screen"], (-2300, 880))
    G["Fade Width"] = pick("FLOAT", G0["Fade Width"], G0["R Fade Width"], (-2300, 820))
    use_cam = boolean("AND", rendering, G0["R Camera"], (-2300, 760))

    # --- probe (the viewport's probe empty, or the active camera at render) -----
    oi = N.new("GeometryNodeObjectInfo"); oi.location = (-2200, 300)
    oi.transform_space = "RELATIVE"
    link(G["Probe"], oi.inputs["Object"])
    cam = N.new("GeometryNodeInputActiveCamera"); cam.location = (-2400, 500)
    oc = N.new("GeometryNodeObjectInfo"); oc.location = (-2200, 550)
    oc.transform_space = "RELATIVE"
    link(cam.outputs[0], oc.inputs["Object"])

    def pick_cam(kind, a, b, loc):
        n = N.new("GeometryNodeSwitch"); n.input_type = kind; n.location = loc
        link(use_cam, n.inputs[0]); link(a, n.inputs[1]); link(b, n.inputs[2])
        return n.outputs[0]

    eye = pick_cam("VECTOR", oi.outputs["Location"], oc.outputs["Location"], (-2000, 350))
    eye_rot = pick_cam("ROTATION", oi.outputs["Rotation"], oc.outputs["Rotation"], (-2000, 600))
    pos = N.new("GeometryNodeInputPosition"); pos.location = (-2000, 0)
    vsub = N.new("ShaderNodeVectorMath"); vsub.operation = "SUBTRACT"; vsub.location = (-1800, 150)
    link(pos.outputs[0], vsub.inputs[0]); link(eye, vsub.inputs[1])
    vlen = N.new("ShaderNodeVectorMath"); vlen.operation = "LENGTH"; vlen.location = (-1600, 250)
    link(vsub.outputs[0], vlen.inputs[0])
    dist = vlen.outputs["Value"]

    psc = N.new("ShaderNodeSeparateXYZ"); psc.location = (-1800, 450)
    link(oi.outputs["Scale"], psc.inputs[0])
    tanx = pick_cam("FLOAT", psc.outputs[0], G0["R TanX"], (-1650, 420))
    tany = pick_cam("FLOAT", psc.outputs[1], G0["R TanY"], (-1650, 480))
    mode = pick_cam("FLOAT", psc.outputs[2], G0["R Mode"], (-1650, 540))

    # Prop size = model radius x point scale
    scl = N.new("GeometryNodeInputNamedAttribute"); scl.data_type = "FLOAT"; scl.location = (-2000, -300)
    scl.inputs["Name"].default_value = "apx_scale"
    size = math("MULTIPLY", G["Radius"], scl.outputs[0], (-1800, -300))
    size15 = math("MULTIPLY", size, 1.5, (-1600, -300))

    # --- LOD by distance -----------------------------------------------------
    # Continuous level: sum over the thresholds of clamp((d - D(1 - w)) / (2 w D)). With w = 0 it is the usual
    # step (d >= D); with w > 0 the integer part is the current LOD and the fraction the progress of the
    # cross-fade to the next one (the prop is then drawn in both LODs, see the shader mask in motion.py).
    d = math("MULTIPLY", dist, G["Dist Scale"], (-1400, 250))
    w = G["Fade Width"]
    one_minus_w = math("SUBTRACT", 1.0, w, (-1400, 1100))
    two_w = math("MULTIPLY", w, 2.0, (-1400, 1200))
    lvl = None
    for i in range(1, MAX_LEVELS):
        y = 700 - i * 120
        start = math("MULTIPLY", G[f"D{i}"], one_minus_w, (-1300, y))
        width = math("MAXIMUM", math("MULTIPLY", G[f"D{i}"], two_w, (-1300, y - 40)), 1e-6, (-1200, y - 40))
        c = math("DIVIDE", math("SUBTRACT", d, start, (-1200, y)), width, (-1100, y))
        c.node.use_clamp = True
        lvl = c if lvl is None else math("ADD", lvl, c, (-1000, y))
    top = math("SUBTRACT", G["Levels"], 1.0, (-1200, 50))
    lvl = math("MINIMUM", lvl, top, (-900, 300))
    forced = math("MINIMUM", G["Forced"], top, (-900, 50))
    sw = N.new("GeometryNodeSwitch"); sw.input_type = "FLOAT"; sw.location = (-700, 200)
    link(G["Use LOD"], sw.inputs[0]); link(forced, sw.inputs[1]); link(lvl, sw.inputs[2])
    level_f = sw.outputs[0]
    level = math("FLOOR", level_f, None, (-550, 200))
    frac = math("SUBTRACT", level_f, level, (-450, 250))
    fading = compare("GREATER_THAN", frac, 1e-3, (-350, 300))

    # --- field of view (frustum) ---------------------------------------------
    inv = N.new("FunctionNodeInvertRotation"); inv.location = (-1600, 600)
    link(eye_rot, inv.inputs[0])
    rv = N.new("FunctionNodeRotateVector"); rv.location = (-1400, 600)
    link(vsub.outputs[0], rv.inputs["Vector"]); link(inv.outputs[0], rv.inputs["Rotation"])
    vl = N.new("ShaderNodeSeparateXYZ"); vl.location = (-1200, 600)
    link(rv.outputs[0], vl.inputs[0])
    fwd = math("MULTIPLY", vl.outputs[2], -1.0, (-1000, 600))
    tx = math("MULTIPLY", tanx, G["Frustum Margin"], (-1000, 800))
    ty = math("MULTIPLY", tany, G["Frustum Margin"], (-1000, 900))
    limx = math("MULTIPLY_ADD", fwd, tx, size15, (-800, 800))
    limy = math("MULTIPLY_ADD", fwd, ty, size15, (-800, 900))
    ax = math("ABSOLUTE", vl.outputs[0], None, (-800, 650))
    ay = math("ABSOLUTE", vl.outputs[1], None, (-800, 700))
    inx = compare("LESS_EQUAL", ax, limx, (-600, 800))
    iny = compare("LESS_EQUAL", ay, limy, (-600, 900))
    negs = math("MULTIPLY", size15, -1.0, (-800, 550))
    inz = compare("GREATER_EQUAL", fwd, negs, (-600, 600))
    infr = boolean("AND", boolean("AND", inx, iny, (-450, 850)), inz, (-300, 800))
    fr_on = boolean("AND", G["Use Frustum"], compare("GREATER_THAN", mode, 0.75, (-600, 1000)), (-450, 1000))
    fr_fail = boolean("NIMPLY", fr_on, infr, (-150, 900))

    # --- size on screen: radius / (distance * tan(fov_y/2)) ------------------
    denom = math("MAXIMUM", math("MULTIPLY", dist, tany, (-1000, -450)), 1e-4, (-850, -450))
    screen = math("DIVIDE", size, denom, (-700, -450))
    small = compare("LESS_THAN", screen, G["Min Screen"], (-550, -450))
    sc_on = compare("GREATER_THAN", mode, 0.25, (-550, -600))
    sc_fail = boolean("AND", small, sc_on, (-400, -500))
    big = compare("GREATER_EQUAL", size, G["Keep Size"], (-550, -650))
    sc_fail = boolean("NIMPLY", sc_fail, big, (-300, -550))

    # --- distance culling ------------------------------------------------------
    far = compare("GREATER_EQUAL", d, G["Cull"], (-700, -750))
    far_fail = boolean("AND", far, G["Use Cull"], (-550, -750))

    any_fail = boolean("OR", boolean("OR", fr_fail, sc_fail, (-250, -300)), far_fail, (-100, -300))
    culled = boolean("AND", any_fail, G["Use LOD"], (50, -300))   # LOD disabled = everything visible

    # --- active region (XY disc), independent from the LOD switch -------------
    region_fail = _region_fail_nodes(N, L, G["Region"], pos.outputs[0], (-700, -1000))
    culled = boolean("OR", culled, region_fail, (150, -400))

    rot = N.new("GeometryNodeInputNamedAttribute"); rot.data_type = "FLOAT_VECTOR"; rot.location = (200, 600)
    rot.inputs["Name"].default_value = "apx_rot"

    join = N.new("GeometryNodeJoinGeometry"); join.location = (1100, 0)
    for i in range(MAX_LEVELS):
        y = 500 - i * 300
        eq = compare("EQUAL", level, float(i), (250, y))
        eq.node.inputs["Epsilon"].default_value = 0.25
        nxt = compare("EQUAL", level, float(i - 1), (250, y - 60))
        nxt.node.inputs["Epsilon"].default_value = 0.25
        incoming = boolean("AND", nxt, fading, (330, y - 60))
        shown = boolean("OR", eq, incoming, (400, y - 30))
        sel = boolean("NIMPLY", shown, culled, (400, y))
        # "r5_fade" on the instances: 0 = opaque, +f = leaving LOD (drawn where noise >= f), -f = arriving LOD
        # (drawn where noise < f): the two LODs share the pixels, so the prop never looks see-through
        sign = math("MULTIPLY_ADD", eq, 2.0, -1.0, (330, y + 60))
        fade_v = math("MULTIPLY", sign, frac, (420, y + 60))
        store = N.new("GeometryNodeStoreNamedAttribute"); store.location = (500, y + 120)
        store.data_type = "FLOAT"
        store.domain = "POINT"
        store.inputs["Name"].default_value = FADE_ATTR
        link(G["Geometry"], store.inputs["Geometry"])
        link(fade_v, store.inputs["Value"])
        ci = N.new("GeometryNodeCollectionInfo"); ci.location = (400, y - 120)
        ci.transform_space = "ORIGINAL"
        link(G[f"LOD{i}"], ci.inputs["Collection"])
        iop = N.new("GeometryNodeInstanceOnPoints"); iop.location = (750, y)
        link(store.outputs["Geometry"], iop.inputs["Points"])
        link(sel, iop.inputs["Selection"])
        link(ci.outputs[0], iop.inputs["Instance"])
        link(rot.outputs[0], iop.inputs["Rotation"])
        link(scl.outputs[0], iop.inputs["Scale"])
        link(iop.outputs[0], join.inputs[0])
    link(join.outputs[0], go.inputs[0])


def _region_fail_nodes(N, L, region_socket, position, loc):
    """True when the point is outside the active region.
    Region object (regions.py): a mesh with one flat disc per shown POI, empty = whole map. A vertical ray
    through the point tells whether it is inside one of the discs (any number of POIs, any radius)."""
    x, y = loc
    oi = N.new("GeometryNodeObjectInfo"); oi.location = (x - 600, y)
    oi.transform_space = "RELATIVE"
    L.new(region_socket, oi.inputs["Object"])
    size = N.new("GeometryNodeAttributeDomainSize"); size.location = (x - 300, y - 250)
    L.new(oi.outputs["Geometry"], size.inputs["Geometry"])
    # float compare: the first two inputs are A / B in every version (5.3 dropped the per-type sockets)
    on = N.new("FunctionNodeCompare"); on.data_type = "FLOAT"; on.operation = "GREATER_THAN"
    on.location = (x - 50, y - 250)
    L.new(size.outputs["Face Count"], on.inputs[0]); on.inputs[1].default_value = 0.5
    sep = N.new("ShaderNodeSeparateXYZ"); sep.location = (x - 400, y + 250)
    L.new(position, sep.inputs[0])
    top = N.new("ShaderNodeCombineXYZ"); top.location = (x - 200, y + 250)
    L.new(sep.outputs[0], top.inputs[0]); L.new(sep.outputs[1], top.inputs[1])
    top.inputs[2].default_value = 1.0e6
    ray = N.new("GeometryNodeRaycast"); ray.location = (x, y + 100)
    L.new(oi.outputs["Geometry"], ray.inputs["Target Geometry"])
    L.new(top.outputs[0], ray.inputs["Source Position"])
    ray.inputs["Ray Direction"].default_value = (0.0, 0.0, -1.0)
    ray.inputs["Ray Length"].default_value = 2.0e6
    miss = N.new("FunctionNodeBooleanMath"); miss.operation = "NOT"; miss.location = (x + 250, y + 100)
    L.new(ray.outputs["Is Hit"], miss.inputs[0])
    both = N.new("FunctionNodeBooleanMath"); both.operation = "AND"; both.location = (x + 450, y)
    L.new(miss.outputs[0], both.inputs[0]); L.new(on.outputs[0], both.inputs[1])
    return both.outputs[0]


def _ids(ng):
    return {it.name: it.identifier for it in ng.interface.items_tree
            if getattr(it, "item_type", "SOCKET") == "SOCKET" and it.in_out == "INPUT"}


_MISSING = object()


def mod_get(mod, ident, default=None):
    """Value of a Geometry Nodes modifier input.
    Blender <= 5.2: mod["Socket_N"] (ID property). Blender 5.3+: mod.properties.inputs.Socket_N.value."""
    props = getattr(mod, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        it = getattr(props.inputs, ident, None)
        return getattr(it, "value", default) if it is not None else default
    try:
        return mod[ident]
    except (KeyError, TypeError):
        return default


def mod_set(mod, ident, value):
    """Set a Geometry Nodes modifier input (see mod_get). Returns True when the value changed."""
    props = getattr(mod, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        it = getattr(props.inputs, ident, None)
        if it is None:
            return False
        if it.value != value:
            it.value = value
            return True
        return False
    if mod.get(ident, _MISSING) != value:
        mod[ident] = value
        return True
    return False


# --------------------------------------------------------------------------
# Probe
# --------------------------------------------------------------------------
def get_probe(scene):
    p = bpy.data.objects.get(PROBE)
    if p is None:
        p = bpy.data.objects.new(PROBE, None)
        p.empty_display_type = "SINGLE_ARROW"
        p.empty_display_size = 0.0
        p.hide_render = True
        p.hide_select = True
        p.rotation_mode = "QUATERNION"
        p.scale = (1.0, 1.0, 0.0)
    if p.name not in scene.collection.objects:
        scene.collection.objects.link(p)
    return p


def set_probe(scene, eye, quat=None, tanx=None, tany=None, mode=None):
    """Move the probe. mode: 1 = frustum + screen size, 0.5 = screen size only, 0 = none."""
    p = get_probe(scene)
    if p.rotation_mode != "QUATERNION":
        p.rotation_mode = "QUATERNION"
    if (p.location - eye).length > 1e-5:
        p.location = eye
    if quat is not None and p.rotation_quaternion.rotation_difference(quat).angle > 1e-4:
        p.rotation_quaternion = quat
    if tanx is not None:
        sc = (tanx, tany, mode)
        if any(abs(a - b) > 1e-5 for a, b in zip(p.scale, sc)):
            p.scale = sc


def move_probe(scene, eye):
    set_probe(scene, eye)


# --------------------------------------------------------------------------
# Object creation
# --------------------------------------------------------------------------
def create_instancer(key, lods, placements_xf, collection, scene, sky=False):
    """placements_xf: list of (loc(3), euler_xyz(3), scale) in Blender space.
    sky: 3D skybox props (always LOD0, never culled nor hidden by the regions, see apply_settings)."""
    from .regions import get_region
    n = len(placements_xf)
    loc = np.array([p[0] for p in placements_xf], dtype=np.float32).reshape(-1, 3)
    rot = np.array([p[1] for p in placements_xf], dtype=np.float32).reshape(-1, 3)
    scl = np.array([p[2] for p in placements_xf], dtype=np.float32)

    me = bpy.data.meshes.new(f"{key}_points")
    me.vertices.add(n)
    me.vertices.foreach_set("co", loc.ravel())
    a = me.attributes.new("apx_rot", "FLOAT_VECTOR", "POINT")
    a.data.foreach_set("vector", rot.ravel())
    b = me.attributes.new("apx_scale", "FLOAT", "POINT")
    b.data.foreach_set("value", scl)
    me.update()

    obj = bpy.data.objects.new(f"{key}_{'sky' if sky else 'instances'}", me)
    obj["apx_gn"] = key
    obj["apx_levels"] = len(lods)
    if sky:
        obj["apx_sky"] = True
    mod = obj.modifiers.new("APX_Instancer", "NODES")
    mod.node_group = get_group()
    ids = _ids(mod.node_group)
    mod_set(mod, ids["Probe"], get_probe(scene))
    if not sky:
        mod_set(mod, ids["Region"], get_region(scene))
    else:
        mod_set(mod, ids["Use LOD"], False)
        mod_set(mod, ids["Forced"], 0)
    for i in range(MAX_LEVELS):
        mod_set(mod, ids[f"LOD{i}"], lods[i] if i < len(lods) else None)
    mod_set(mod, ids["Levels"], min(len(lods), MAX_LEVELS))
    link_model_materials(obj, lods)
    collection.objects.link(obj)
    return obj


def link_model_materials(obj, lods=None):
    """Give the instancer (a point cloud: one object for every copy of a model) the materials of its model, so
    that selecting it shows them in the Material tab. They are the model's own materials: editing one changes
    every copy. The points have no faces, so these slots do not change what is drawn."""
    if lods is None:
        mod = next((m for m in obj.modifiers if m.type == "NODES" and m.node_group
                    and m.node_group.name.startswith(GROUP)), None)
        if mod is None:
            return 0
        ids = _ids(mod.node_group)
        lods = []
        for i in range(MAX_LEVELS):
            lods.append(mod_get(mod, ids[f"LOD{i}"]) if f"LOD{i}" in ids else None)
    if not lods or lods[0] is None:
        return 0
    me = obj.data
    have = {m.name for m in me.materials if m}
    n = 0
    for o in lods[0].all_objects:
        if o.type != "MESH":
            continue
        for m in o.data.materials:
            if m is not None and m.name not in have:
                me.materials.append(m)
                have.add(m.name)
                n += 1
    return n


def instancer_objects(scene):
    return [o for o in scene.objects if "apx_gn" in o and o.type == "MESH"]


def viewport_fade(s):
    return s.lod_fade_width if (getattr(s, "lod_fade", False) and getattr(s, "lod_fade_viewport", False)) else 0.0


def render_values(scene, viewport):
    """The "R ..." inputs: what the final render uses (Render panel, 'At render time'). viewport: the viewport
    values of the same modifier ("Use LOD", "Forced", "Min Screen", "Fade Width") for 'Keep the viewport state'.
    Computed from the main thread whenever a setting or the camera changes, never during the render."""
    s = scene.r5_settings
    out = {"R Use LOD": False, "R Forced": 0, "R Min Screen": 0.0, "R Fade Width": 0.0, "R Camera": False,
           "R TanX": 1.0, "R TanY": 1.0, "R Mode": 0.5}
    if not s.use_lod:
        out["R Forced"] = s.forced_lod
    elif s.render_mode == "KEEP":
        out.update({"R Use LOD": viewport["Use LOD"], "R Forced": viewport["Forced"],
                    "R Min Screen": viewport["Min Screen"], "R Fade Width": viewport["Fade Width"]})
    elif s.render_mode == "CAMERA" and scene.camera is not None:
        from . import lod_system, motion
        v = lod_system.camera_view(scene, frustum=s.render_frustum)
        out.update({"R Use LOD": True, "R Camera": True, "R Min Screen": s.render_min_screen_pct / 100.0,
                    "R Fade Width": float(s.lod_fade_width if (s.lod_fade and motion.lod_fade_ready()) else 0.0),
                    "R TanX": float(v.tanx), "R TanY": float(v.tany), "R Mode": float(v.mode)})
    return out


def apply_settings(scene, force_level=None, min_screen=None, fade=None):
    """Push the scene's LOD / culling settings into every modifier (viewport and final render values).
    fade: LOD cross-fade band (None = the viewport setting, see viewport_fade)."""
    from . import library
    from .regions import get_region
    s = scene.r5_settings
    ng = bpy.data.node_groups.get(GROUP)
    if ng is None:
        return 0
    if ng.get("apx_ver") != GROUP_VERSION:
        ng = get_group()
    ids = _ids(ng)
    dists = sorted(s.lod_distances)
    region = get_region(scene)
    if min_screen is None:
        min_screen = (s.min_screen_pct / 100.0) if s.cull_small_props else 0.0
    rvals = None
    n = 0
    for o in instancer_objects(scene):
        mod = o.modifiers.get("APX_Instancer")
        if mod is None:
            continue
        radius = library.model_radius(o["apx_gn"])
        factor = 1.0
        if s.lod_size_scaling:
            factor = min(max(radius / max(s.lod_reference_size, 1e-3), 0.25), 20.0)
        scale = 1.0 / (factor * max(s.lod_bias, 1e-3))
        vals = {
            "Dist Scale": scale,
            "Cull": s.cull_distance * scale if s.use_culling else 1e12,
            "Use Cull": s.use_culling,
            "Use LOD": s.use_lod and force_level is None,
            "Forced": force_level if force_level is not None else s.forced_lod,
            "Radius": radius,
            "Use Frustum": s.use_frustum,
            "Frustum Margin": s.frustum_margin,
            "Min Screen": min_screen,
            "Fade Width": float(viewport_fade(s) if fade is None else fade),
            "Keep Size": s.cull_keep_radius if s.cull_keep_radius > 0 else 1e9,
        }
        for i in range(1, MAX_LEVELS):
            vals[f"D{i}"] = dists[i - 1] if i - 1 < len(dists) else 1e12
        if rvals is None:
            rvals = render_values(scene, vals)
        vals.update(rvals)
        sky = bool(o.get("apx_sky"))
        if sky:
            vals.update({"Use LOD": False, "Forced": 0, "Fade Width": 0.0,
                         "R Use LOD": False, "R Forced": 0, "R Fade Width": 0.0, "R Camera": False})
        changed = False
        for k, v in vals.items():
            changed |= mod_set(mod, ids[k], v)
        if not sky:
            changed |= mod_set(mod, ids["Region"], region)
        if changed:
            o.update_tag()
            n += 1
    if n:
        from . import lod_system
        lod_system.hidden_dirty()
    return n
