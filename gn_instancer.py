"""Instanciation par Geometry Nodes : UN objet par modèle unique.

Chaque modèle = un nuage de points (1 point par prop, attributs apx_rot / apx_scale)
+ un modificateur Geometry Nodes partagé qui, pour chaque point :
  - choisit le LOD selon la distance à la sonde (APX_LOD_Probe),
  - masque le prop s'il est HORS DU CHAMP DE VISION (frustum culling),
  - masque le prop s'il est TROP PETIT À L'ÉCRAN (petits objets lointains),
  - masque au-delà de la distance de culling (optionnel).

La sonde est un empty que le timer place sur la vue 3D (ou la caméra) :
  position = œil, rotation = orientation de la vue (regarde vers -Z),
  échelle  = (tan(fov_x/2), tan(fov_y/2), mode) avec mode : 1 = frustum + taille écran,
             0.5 = taille écran seulement (rendu), 0 = rien (vue orthographique).
Tout le calcul se fait dans Blender (C++), rien n'est fait par prop en Python.
"""

import bpy
import numpy as np

GROUP = "APX_Instancer"
GROUP_VERSION = 3
MAX_LEVELS = 5
PROBE = "APX_LOD_Probe"


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
        # Mise à jour d'une ancienne version : on remplace le groupe dans tous les modificateurs
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
    _sock(ng, "Frustum Margin", "NodeSocketFloat", default=1.25)
    _sock(ng, "Min Screen", "NodeSocketFloat", default=0.0)

    N, L = ng.nodes, ng.links
    gi = N.new("NodeGroupInput"); gi.location = (-2200, 0)
    go = N.new("NodeGroupOutput"); go.location = (1100, 0)
    G = gi.outputs

    def link(a, b):
        L.new(a, b)

    def math(op, a, b=None, *rest):
        # math(op, a, b, loc) ou math(op, a, b, c, loc) pour MULTIPLY_ADD
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

    # --- sonde --------------------------------------------------------------
    oi = N.new("GeometryNodeObjectInfo"); oi.location = (-2000, 300)
    oi.transform_space = "RELATIVE"
    link(G["Probe"], oi.inputs["Object"])
    pos = N.new("GeometryNodeInputPosition"); pos.location = (-2000, 0)
    vsub = N.new("ShaderNodeVectorMath"); vsub.operation = "SUBTRACT"; vsub.location = (-1800, 150)
    link(pos.outputs[0], vsub.inputs[0]); link(oi.outputs["Location"], vsub.inputs[1])
    vlen = N.new("ShaderNodeVectorMath"); vlen.operation = "LENGTH"; vlen.location = (-1600, 250)
    link(vsub.outputs[0], vlen.inputs[0])
    dist = vlen.outputs["Value"]

    psc = N.new("ShaderNodeSeparateXYZ"); psc.location = (-1800, 450)
    link(oi.outputs["Scale"], psc.inputs[0])
    tanx, tany, mode = psc.outputs[0], psc.outputs[1], psc.outputs[2]

    # Taille du prop = rayon du modèle x échelle du point
    scl = N.new("GeometryNodeInputNamedAttribute"); scl.data_type = "FLOAT"; scl.location = (-2000, -300)
    scl.inputs["Name"].default_value = "apx_scale"
    size = math("MULTIPLY", G["Radius"], scl.outputs[0], (-1800, -300))
    size15 = math("MULTIPLY", size, 1.5, (-1600, -300))

    # --- LOD par distance ---------------------------------------------------
    d = math("MULTIPLY", dist, G["Dist Scale"], (-1400, 250))
    lvl = None
    for i in range(1, MAX_LEVELS):
        c = compare("GREATER_EQUAL", d, G[f"D{i}"], (-1200, 700 - i * 120))
        lvl = c if lvl is None else math("ADD", lvl, c, (-1050, 700 - i * 120))
    top = math("SUBTRACT", G["Levels"], 1.0, (-1200, 50))
    lvl = math("MINIMUM", lvl, top, (-900, 300))
    forced = math("MINIMUM", G["Forced"], top, (-900, 50))
    sw = N.new("GeometryNodeSwitch"); sw.input_type = "FLOAT"; sw.location = (-700, 200)
    link(G["Use LOD"], sw.inputs[0]); link(forced, sw.inputs[1]); link(lvl, sw.inputs[2])
    level = sw.outputs[0]

    # --- champ de vision (frustum) ------------------------------------------
    inv = N.new("FunctionNodeInvertRotation"); inv.location = (-1600, 600)
    link(oi.outputs["Rotation"], inv.inputs[0])
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

    # --- taille à l'écran : rayon / (distance * tan(fov_y/2)) ----------------
    denom = math("MAXIMUM", math("MULTIPLY", dist, tany, (-1000, -450)), 1e-4, (-850, -450))
    screen = math("DIVIDE", size, denom, (-700, -450))
    small = compare("LESS_THAN", screen, G["Min Screen"], (-550, -450))
    sc_on = compare("GREATER_THAN", mode, 0.25, (-550, -600))
    sc_fail = boolean("AND", small, sc_on, (-400, -500))

    # --- culling par distance -----------------------------------------------
    far = compare("GREATER_EQUAL", d, G["Cull"], (-700, -750))
    far_fail = boolean("AND", far, G["Use Cull"], (-550, -750))

    any_fail = boolean("OR", boolean("OR", fr_fail, sc_fail, (-250, -300)), far_fail, (-100, -300))
    culled = boolean("AND", any_fail, G["Use LOD"], (50, -300))   # LOD désactivé = tout visible

    rot = N.new("GeometryNodeInputNamedAttribute"); rot.data_type = "FLOAT_VECTOR"; rot.location = (200, 600)
    rot.inputs["Name"].default_value = "apx_rot"

    join = N.new("GeometryNodeJoinGeometry"); join.location = (900, 0)
    for i in range(MAX_LEVELS):
        y = 500 - i * 300
        eq = compare("EQUAL", level, float(i), (250, y))
        eq.node.inputs["Epsilon"].default_value = 0.25
        sel = boolean("NIMPLY", eq, culled, (400, y))
        ci = N.new("GeometryNodeCollectionInfo"); ci.location = (400, y - 120)
        ci.transform_space = "ORIGINAL"
        link(G[f"LOD{i}"], ci.inputs["Collection"])
        iop = N.new("GeometryNodeInstanceOnPoints"); iop.location = (650, y)
        link(G["Geometry"], iop.inputs["Points"])
        link(sel, iop.inputs["Selection"])
        link(ci.outputs[0], iop.inputs["Instance"])
        link(rot.outputs[0], iop.inputs["Rotation"])
        link(scl.outputs[0], iop.inputs["Scale"])
        link(iop.outputs[0], join.inputs[0])
    link(join.outputs[0], go.inputs[0])


def _ids(ng):
    return {it.name: it.identifier for it in ng.interface.items_tree
            if getattr(it, "item_type", "SOCKET") == "SOCKET" and it.in_out == "INPUT"}


# --------------------------------------------------------------------------
# Sonde
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
    """Place la sonde. mode : 1 = frustum + taille écran, 0.5 = taille écran seule, 0 = aucun."""
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
# Création des objets
# --------------------------------------------------------------------------
def create_instancer(key, lods, placements_xf, collection, scene):
    """placements_xf : liste de (loc(3), euler_xyz(3), scale) en espace Blender."""
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

    obj = bpy.data.objects.new(f"{key}_instances", me)
    obj["apx_gn"] = key
    obj["apx_levels"] = len(lods)
    mod = obj.modifiers.new("APX_Instancer", "NODES")
    mod.node_group = get_group()
    ids = _ids(mod.node_group)
    mod[ids["Probe"]] = get_probe(scene)
    for i in range(MAX_LEVELS):
        mod[ids[f"LOD{i}"]] = lods[i] if i < len(lods) else None
    mod[ids["Levels"]] = min(len(lods), MAX_LEVELS)
    collection.objects.link(obj)
    return obj


def instancer_objects(scene):
    return [o for o in scene.objects if "apx_gn" in o and o.type == "MESH"]


def apply_settings(scene, force_level=None):
    """Pousse les réglages LOD/culling de la scène dans tous les modificateurs."""
    from . import library
    s = scene.apx_settings
    ng = bpy.data.node_groups.get(GROUP)
    if ng is None:
        return 0
    if ng.get("apx_ver") != GROUP_VERSION:
        ng = get_group()
    ids = _ids(ng)
    dists = sorted(s.lod_distances)
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
            "Min Screen": (s.min_screen_pct / 100.0) if s.cull_small_props else 0.0,
        }
        for i in range(1, MAX_LEVELS):
            vals[f"D{i}"] = dists[i - 1] if i - 1 < len(dists) else 1e12
        changed = False
        for k, v in vals.items():
            ident = ids[k]
            if mod.get(ident) != v:
                mod[ident] = v
                changed = True
        if changed:
            o.update_tag()
            n += 1
    return n
