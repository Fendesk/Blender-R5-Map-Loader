"""Movers / Shaders: things that move in game, switched on after the map is loaded (they cost time on every
frame), and the material side of the smooth LOD transitions.

Movers (Geometry Nodes instancing). Static props never move in game: what turns is driven by the entities.
  - Rotators: script_mover_lightweight / script_rotator entities (axis, speed in degrees per second) and what
    they move: their linked brush models, linked props, and the props placed on those (the koi sign of E-District,
    the four big fans of Fan Town). Every part of a rotator turns around the SAME pivot and axis.
  - Rotors found by name (fan blades, turbine blades, engine / teleporter rings): the axis is the model's
    rotational symmetry axis (measured on the mesh, not guessed); models without one do not turn.
  - Groups found by name (the rotating building of the Olympus farms): one pivot for the whole group.
  - Tridents hover.
Every spinning part uses the same phase, so the parts of one machine stay aligned. Per-prop motion is written as
point attributes of the instancers (r5_mv_*), read by the "APX_Mover" modifier placed after the instancer:
Blender re-evaluates only these objects when the frame changes.

Animated shaders: water, lava, screens / holograms, energy (Olympus storm dome, electric rings), smoke / fire /
clouds scroll their textures, screens flicker, foliage and cloth (banners, flags) sway in the wind (vertex
displacement). One shader group, "R5_AnimClock", holds the clock: its values are drivers (frame / fps x speeds),
so changing a speed never recompiles the shaders. Enabling adds a few nodes (tagged "R5ANIM") to the materials,
disabling removes them.

LOD transitions: the material mask "R5_LODFade" reads the "r5_fade" instance attribute written by the
instancer (gn_instancer "Fade Width"): a prop inside a cross-fade band is drawn in both LODs, each on its share
of a fixed noise pattern, so it changes LOD smoothly over several frames instead of popping.
"""

import json
import math
import re

import bpy
import numpy as np
from mathutils import Euler, Matrix, Vector

from . import gn_instancer, library

# ==========================================================================
# Movers
# ==========================================================================
MOVER_GROUP = "APX_Mover"
MOVER_MOD = "APX_Mover"
MESH_GROUP = "APX_MoverMesh"
MOVER_VERSION = 2
ATTRS = (("r5_mv_axis", "FLOAT_VECTOR"), ("r5_mv_pivot", "FLOAT_VECTOR"), ("r5_mv_rate", "FLOAT"),
         ("r5_mv_bob", "FLOAT"), ("r5_mv_amp", "FLOAT"), ("r5_mv_phase", "FLOAT"))
MEMBER_TOL = 1.8           # m: a prop this close to a rotator part (brush origin, linked prop) turns with it

# rotors found by name: (regex, revolutions per second). The axis comes from the mesh symmetry.
SPIN_RULES = (
    (r"fan_blade|imc_fan_large_0\d$|smallfan|fan_ceiling\d*_large$|ac_unit.*_fan_\d+$", 0.8),
    (r"turbine_blade|windmill_blade|wind_turbine_(blade|rotor)|propeller|(^|_)rotor(_|$)", 0.2),
    (r"gravity_engine(_energy)?_ring$|teleporter_(inner|outer)_ring_(decals?_)?\d+(_decals)?$|energy_ring", 0.05),
)
# groups found by name: the parts within `radius` m of each other turn together around a vertical axis through
# the group's base (or its centre): (regex, rev/s, radius, base regex)
GROUP_RULES = (
    (r"farms_building_rotating_(base|blade|top|platform|ramps)", 0.02, 60.0, r"rotating_base"),
)
# hovering: (regex, cycles per second, metres)
BOB_RULES = (
    (r"hovercraft|hover_?bike|(^|_)drone(_|$)", 0.35, 0.05),
)
_SPIN = [(re.compile(rx), r) for rx, r in SPIN_RULES]
_GROUP = [(re.compile(rx), r, rad, re.compile(b)) for rx, r, rad, b in GROUP_RULES]
_BOB = [(re.compile(rx), f, a) for rx, f, a in BOB_RULES]


def mover_rule(key):
    """('spin', rate) / ('group', rate, radius, base_re) / ('bob', freq, amp) / None, from the model name."""
    low = key.lower()
    for rx, rate, rad, base in _GROUP:
        if rx.search(low):
            return ("group", rate, rad, base)
    for rx, rate in _SPIN:
        if rx.search(low):
            return ("spin", rate)
    for rx, f, a in _BOB:
        if rx.search(low):
            return ("bob", f, a)
    return None


def rotor_axis(key):
    """(axis, centre) of the model's rotational symmetry in model space, or None. Tested: 3 to 8-fold symmetry
    around the X, Y and Z axes through the centre of the bounds; the best one must map the mesh onto itself
    within 0.6% of its size. Cached on the model collection."""
    from mathutils.kdtree import KDTree
    c = bpy.data.collections.get(key)
    if c is not None and "apx_rotor" in c:
        v = list(c["apx_rotor"])
        return (tuple(v[:3]), tuple(v[3:6])) if v else None
    lods = library.get_model_lods(key)
    res = None
    if lods:
        cos = []
        for o in lods[0].all_objects:
            if o.type == "MESH" and len(o.data.vertices):
                co = np.empty(len(o.data.vertices) * 3, np.float32)
                o.data.vertices.foreach_get("co", co)
                cos.append(co.reshape(-1, 3))
        if cos:
            pts = np.concatenate(cos).astype(np.float64)
            rng = np.random.default_rng(1)
            if len(pts) > 40000:
                pts = pts[rng.choice(len(pts), 40000, replace=False)]
            lo, hi = pts.min(0), pts.max(0)
            size = float(np.linalg.norm(hi - lo)) or 1.0
            centre = (lo + hi) / 2
            kd = KDTree(len(pts))
            for i, p in enumerate(pts):
                kd.insert(p, i)
            kd.balance()
            probe = pts[rng.choice(len(pts), min(400, len(pts)), replace=False)] - centre
            best = None
            for ax in range(3):
                axis = Vector([1.0 if i == ax else 0.0 for i in range(3)])
                for n in (3, 4, 5, 6, 8):
                    rm = np.array(Matrix.Rotation(2 * math.pi / n, 3, axis))
                    q = probe @ rm.T + centre
                    d = np.median([kd.find(p)[2] for p in q]) / size
                    if best is None or d < best[0]:
                        best = (d, ax)
            if best is not None and best[0] < 0.006:
                axis = [0.0, 0.0, 0.0]
                axis[best[1]] = 1.0
                res = (tuple(axis), tuple(float(v) for v in centre))
    if c is not None:
        c["apx_rotor"] = list(res[0]) + list(res[1]) if res else []
    return res


def _point_frames(o):
    """Positions (N,3), rotation matrices (N,3,3) and scales (N) of an instancer's points (world space)."""
    me = o.data
    n = len(me.vertices)
    co = np.empty(n * 3, np.float32)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3).astype(np.float64)
    rot = np.zeros((n, 3), np.float32)
    scl = np.ones(n, np.float32)
    if "apx_rot" in me.attributes:
        tmp = np.empty(n * 3, np.float32)
        me.attributes["apx_rot"].data.foreach_get("vector", tmp)
        rot = tmp.reshape(-1, 3)
    if "apx_scale" in me.attributes:
        me.attributes["apx_scale"].data.foreach_get("value", scl)
    mats = np.array([np.array(Euler(r, "XYZ").to_matrix()) for r in rot]).reshape(-1, 3, 3)
    mw = np.array(o.matrix_world)
    co = co @ mw[:3, :3].T + mw[:3, 3]
    return co, mats, scl.astype(np.float64)


def _rotators(scene):
    try:
        return json.loads(scene.get("apx_rotators", "[]"))
    except (TypeError, ValueError):
        return []


def _plan(scene):
    """{instancer object: {attribute: array}} for every prop that moves, and the rotator brush objects."""
    rots = _rotators(scene)
    members = [(np.array(r["members"], np.float64).reshape(-1, 3), r) for r in rots if r.get("members")]
    objs = [o for o in gn_instancer.instancer_objects(scene) if not o.get("apx_sky") and len(o.data.vertices)]
    plans = {}
    group_pts = {}           # group rule index -> [(object, point index, position, is_base)]

    def plan_of(o, n):
        p = plans.get(o)
        if p is None:
            p = plans[o] = {"r5_mv_axis": np.zeros((n, 3)), "r5_mv_pivot": np.zeros((n, 3)),
                            "r5_mv_rate": np.zeros(n), "r5_mv_bob": np.zeros(n), "r5_mv_amp": np.zeros(n),
                            "r5_mv_phase": np.zeros(n)}
        return p

    for o in objs:
        key = o["apx_gn"]
        rule = mover_rule(key)
        if rule is None and not members:
            continue
        pos, mats, scl = _point_frames(o)
        n = len(pos)
        done = np.zeros(n, bool)
        # 1) rotators of the map
        for mpos, r in members:
            d = np.linalg.norm(pos[:, None, :] - mpos[None, :, :], axis=2).min(1)
            sel = (d < MEMBER_TOL) & ~done
            if sel.any():
                p = plan_of(o, n)
                p["r5_mv_axis"][sel] = r["axis"]
                p["r5_mv_pivot"][sel] = r["pivot"]
                p["r5_mv_rate"][sel] = r["rate"]
                done |= sel
        if rule is None or done.all():
            continue
        kind = rule[0]
        free = ~done
        if kind == "spin":
            ax = rotor_axis(key)
            if ax is None:
                continue
            axis_l, centre_l = np.array(ax[0]), np.array(ax[1])
            p = plan_of(o, n)
            wa = mats @ axis_l
            p["r5_mv_axis"][free] = wa[free]
            p["r5_mv_pivot"][free] = (pos + (mats @ centre_l) * scl[:, None])[free]
            p["r5_mv_rate"][free] = rule[1]
        elif kind == "bob":
            p = plan_of(o, n)
            p["r5_mv_bob"][free] = rule[1]
            p["r5_mv_amp"][free] = rule[2]
            p["r5_mv_phase"][free] = (np.floor(pos[free, 0] * 0.37 + pos[free, 1] * 0.73) % 97) / 97.0
        elif kind == "group":
            gi = next(i for i, g in enumerate(_GROUP) if g[0].search(key.lower()))
            is_base = bool(rule[3].search(key.lower()))
            lst = group_pts.setdefault(gi, [])
            for i in np.nonzero(free)[0]:
                lst.append((o, n, int(i), pos[i], is_base))
    # groups: parts closer than the rule radius share one vertical axis
    for gi, lst in group_pts.items():
        _rx, rate, rad, _b = _GROUP[gi]
        P = np.array([t[3] for t in lst])
        label = -np.ones(len(lst), int)
        for i in range(len(lst)):
            if label[i] >= 0:
                continue
            label[i] = i
            stack = [i]
            while stack:
                j = stack.pop()
                near = np.nonzero((np.linalg.norm(P[:, :2] - P[j, :2], axis=1) < rad) & (label < 0))[0]
                label[near] = i
                stack += list(near)
        for g in set(label):
            idx = np.nonzero(label == g)[0]
            bases = [k for k in idx if lst[k][4]]
            piv = P[bases].mean(0) if bases else P[idx].mean(0)
            for k in idx:
                o, n, i, _p, _b = lst[k]
                p = plan_of(o, n)
                p["r5_mv_axis"][i] = (0.0, 0.0, 1.0)
                p["r5_mv_pivot"][i] = piv
                p["r5_mv_rate"][i] = rate
    return plans


def _mover_group():
    ng = bpy.data.node_groups.get(MOVER_GROUP)
    if ng is not None and ng.get("apx_ver") == MOVER_VERSION:
        return ng
    if ng is not None:
        bpy.data.node_groups.remove(ng)
    ng = bpy.data.node_groups.new(MOVER_GROUP, "GeometryNodeTree")
    try:
        _build_mover(ng)
    except Exception:
        bpy.data.node_groups.remove(ng)
        raise
    ng["apx_ver"] = MOVER_VERSION
    return ng


def _build_mover(ng):
    """Instances: rotate around r5_mv_axis through r5_mv_pivot (world) at r5_mv_rate rev/s, bob by r5_mv_amp m
    at r5_mv_bob Hz. Speed: global multiplier."""
    sk = gn_instancer._sock
    sk(ng, "Geometry", "NodeSocketGeometry")
    sk(ng, "Geometry", "NodeSocketGeometry", "OUTPUT")
    sk(ng, "Speed", "NodeSocketFloat", default=1.0)
    N, L = ng.nodes, ng.links
    gi = N.new("NodeGroupInput"); gi.location = (-1400, 0)
    go = N.new("NodeGroupOutput"); go.location = (1300, 0)
    G = gi.outputs

    def attr(name, kind, loc):
        a = N.new("GeometryNodeInputNamedAttribute"); a.data_type = kind; a.location = loc
        a.inputs["Name"].default_value = name
        return a.outputs[0]

    def m(op, a, b=None, loc=(0, 0)):
        n = N.new("ShaderNodeMath"); n.operation = op; n.location = loc
        for i, v in ((0, a), (1, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                L.new(v, n.inputs[i])
        return n.outputs[0]

    t = N.new("GeometryNodeInputSceneTime"); t.location = (-1200, 300)
    ts = m("MULTIPLY", t.outputs["Seconds"], G["Speed"], (-1000, 300))
    rate = attr("r5_mv_rate", "FLOAT", (-1200, 100))
    ang = m("MULTIPLY", m("MULTIPLY", ts, rate, (-800, 200)), 2 * math.pi, (-650, 200))
    aa = N.new("FunctionNodeAxisAngleToRotation"); aa.location = (-400, 200)
    L.new(attr("r5_mv_axis", "FLOAT_VECTOR", (-700, 400)), aa.inputs["Axis"])
    L.new(ang, aa.inputs["Angle"])
    spin = N.new("FunctionNodeCompare"); spin.data_type = "FLOAT"; spin.operation = "NOT_EQUAL"
    spin.location = (-400, 0)
    L.new(rate, spin.inputs[0]); spin.inputs[1].default_value = 0.0
    rot = N.new("GeometryNodeRotateInstances"); rot.location = (0, 100)
    L.new(G["Geometry"], rot.inputs["Instances"])
    L.new(spin.outputs[0], rot.inputs["Selection"])
    L.new(aa.outputs[0], rot.inputs["Rotation"])
    L.new(attr("r5_mv_pivot", "FLOAT_VECTOR", (-400, -150)), rot.inputs["Pivot Point"])
    rot.inputs["Local Space"].default_value = False
    # hover
    bobf = attr("r5_mv_bob", "FLOAT", (-1200, -300))
    cyc = m("ADD", m("MULTIPLY", ts, bobf, (-800, -300)), attr("r5_mv_phase", "FLOAT", (-1000, -450)), (-650, -300))
    wave = m("SINE", m("MULTIPLY", cyc, 2 * math.pi, (-500, -300)), None, (-350, -300))
    dz = m("MULTIPLY", wave, attr("r5_mv_amp", "FLOAT", (-500, -450)), (-200, -300))
    off = N.new("ShaderNodeCombineXYZ"); off.location = (0, -300)
    L.new(dz, off.inputs[2])
    tr = N.new("GeometryNodeTranslateInstances"); tr.location = (400, 0)
    L.new(rot.outputs[0], tr.inputs["Instances"])
    L.new(off.outputs[0], tr.inputs["Translation"])
    tr.inputs["Local Space"].default_value = False
    L.new(tr.outputs[0], go.inputs[0])


def _mesh_group():
    """Rotator brush meshes: every vertex turned around the axis through the pivot (Vector Rotate)."""
    ng = bpy.data.node_groups.get(MESH_GROUP)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(MESH_GROUP, "GeometryNodeTree")
    sk = gn_instancer._sock
    sk(ng, "Geometry", "NodeSocketGeometry")
    sk(ng, "Geometry", "NodeSocketGeometry", "OUTPUT")
    sk(ng, "Axis", "NodeSocketVector", default=(0.0, 0.0, 1.0))
    sk(ng, "Pivot", "NodeSocketVector", default=(0.0, 0.0, 0.0))
    sk(ng, "Rate", "NodeSocketFloat", default=0.0)
    sk(ng, "Speed", "NodeSocketFloat", default=1.0)
    N, L = ng.nodes, ng.links
    gi = N.new("NodeGroupInput"); gi.location = (-800, 0)
    go = N.new("NodeGroupOutput"); go.location = (600, 0)
    t = N.new("GeometryNodeInputSceneTime"); t.location = (-800, 300)
    a = N.new("ShaderNodeMath"); a.operation = "MULTIPLY"; L.new(t.outputs["Seconds"], a.inputs[0])
    L.new(gi.outputs["Speed"], a.inputs[1])
    b = N.new("ShaderNodeMath"); b.operation = "MULTIPLY"; L.new(a.outputs[0], b.inputs[0])
    L.new(gi.outputs["Rate"], b.inputs[1])
    c = N.new("ShaderNodeMath"); c.operation = "MULTIPLY"; L.new(b.outputs[0], c.inputs[0])
    c.inputs[1].default_value = 2 * math.pi
    pos = N.new("GeometryNodeInputPosition"); pos.location = (-400, -200)
    vr = N.new("ShaderNodeVectorRotate"); vr.rotation_type = "AXIS_ANGLE"; vr.location = (0, -100)
    L.new(pos.outputs[0], vr.inputs["Vector"])
    L.new(gi.outputs["Pivot"], vr.inputs["Center"])
    L.new(gi.outputs["Axis"], vr.inputs["Axis"])
    L.new(c.outputs[0], vr.inputs["Angle"])
    sp = N.new("GeometryNodeSetPosition"); sp.location = (300, 0)
    L.new(gi.outputs["Geometry"], sp.inputs["Geometry"])
    L.new(vr.outputs[0], sp.inputs["Position"])
    L.new(sp.outputs[0], go.inputs[0])
    return ng


def _mover_objects(scene):
    return [o for o in scene.objects if o.modifiers.get(MOVER_MOD)]


def movers_enabled(scene):
    return bool(_mover_objects(scene))


def _set_attrs(me, plan):
    for name, kind in ATTRS:
        a = me.attributes.get(name)
        if a is not None and (a.data_type != kind or a.domain != "POINT"):
            me.attributes.remove(a)
            a = None
        if a is None:
            a = me.attributes.new(name, kind, "POINT")
        v = plan[name].astype(np.float32).ravel()
        a.data.foreach_set("vector" if kind == "FLOAT_VECTOR" else "value", v)


def _clear_attrs(me):
    for name, _k in ATTRS:
        a = me.attributes.get(name)
        if a is not None:
            me.attributes.remove(a)


def enable_movers(scene):
    """Write the motion of every moving prop and add the mover modifiers. Returns (objects, moving props)."""
    s = scene.r5_settings
    plans = _plan(scene)
    ng = None
    n_obj = n_props = 0
    for o, plan in plans.items():
        moving = (plan["r5_mv_rate"] != 0) | (plan["r5_mv_amp"] != 0)
        if not moving.any():
            continue
        ng = ng or _mover_group()
        _set_attrs(o.data, plan)
        mod = o.modifiers.get(MOVER_MOD) or o.modifiers.new(MOVER_MOD, "NODES")
        mod.node_group = ng
        gn_instancer.mod_set(mod, gn_instancer._ids(ng)["Speed"], float(s.movers_speed))
        o.update_tag()
        n_obj += 1
        n_props += int(moving.sum())
    # rotator brush meshes (when the export has them)
    rots = _rotators(scene)
    for o in scene.objects:
        nb = o.get("apx_rotor_brush")
        if nb is None or o.type != "MESH":
            continue
        r = next((r for r in rots if nb in r.get("brushes", [])), None)
        if r is None:
            continue
        mg = _mesh_group()
        mod = o.modifiers.get(MOVER_MOD) or o.modifiers.new(MOVER_MOD, "NODES")
        mod.node_group = mg
        ids = gn_instancer._ids(mg)
        _mod_set_vec(mod, ids["Axis"], tuple(r["axis"]))
        _mod_set_vec(mod, ids["Pivot"], tuple(r["pivot"]))
        gn_instancer.mod_set(mod, ids["Rate"], float(r["rate"]))
        gn_instancer.mod_set(mod, ids["Speed"], float(s.movers_speed))
        n_obj += 1
        n_props += 1
    return n_obj, n_props


def _mod_set_vec(mod, ident, vec):
    props = getattr(mod, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        it = getattr(props.inputs, ident, None)
        if it is not None:
            it.value = vec
        return
    mod[ident] = vec


def disable_movers(scene):
    n = 0
    for o in _mover_objects(scene):
        o.modifiers.remove(o.modifiers[MOVER_MOD])
        if o.type == "MESH" and "apx_gn" in o:
            _clear_attrs(o.data)
        n += 1
    return n


def update_movers_speed(scene):
    v = float(scene.r5_settings.movers_speed)
    for o in _mover_objects(scene):
        mod = o.modifiers[MOVER_MOD]
        if mod.node_group is None:
            continue
        ident = gn_instancer._ids(mod.node_group).get("Speed")
        if ident and gn_instancer.mod_set(mod, ident, v):
            o.update_tag()


def mover_candidates(scene):
    """Rough count of the props that can move (for the panel): name rules + rotator parts."""
    n = len(_rotators(scene))
    for o in gn_instancer.instancer_objects(scene):
        if not o.get("apx_sky") and mover_rule(o["apx_gn"]):
            n += len(o.data.vertices)
    return n

# ==========================================================================
# Animated shaders
# ==========================================================================
CLOCK = "R5_AnimClock"
TAG = "R5ANIM"
# clock outputs: name -> speed property (multiplied by the global speed)
CLOCK_SPEEDS = (("water", "speed_water"), ("lava", "speed_lava"), ("screens", "speed_screens"),
                ("energy", "speed_energy"), ("effects", "speed_effects"), ("wind", "wind_speed"))

_WATER_RE = re.compile(r"water|caustic|fountain")
_NOT_WATER_RE = re.compile(r"waterpipe|water_?dispenser|waterproof|water_tower|water_tank|puddle|underwater")
_FALL_RE = re.compile(r"waterfall|_falls?(_|$)|drips|spout")
_LAVA_RE = re.compile(r"lava_(river|flow|fall|pool|stream)|magma|molten")
_SCREEN_RE = re.compile(r"screen|monitor|display|jumbotron|digiscreen|sign_lit|(^|_)holo|hologram|(^|_)tv(_|$)")
_NOT_SCREEN_RE = re.compile(r"frame|stand|plastic|_off(_|$)|fridge|food_display")
_SCROLLING_SCREEN_RE = re.compile(r"anim|scroll|holo")
_EFFECT_RE = re.compile(r"(^|_)(smoke|steam|mist|flames?|fire|clouds?|vapou?r)(_|$)|candle_flame|erupt")
_NOT_EFFECT_RE = re.compile(r"live_fire|fire_hydrant|fire_ex|cloud_city|cloudcity")
_WIND_RE = re.compile(r"(^|_)(leaf|leaves|foliage|grass|grasses|fern|ferns|bush|bushes|shrub|plant|plants|tree|trees|"
                      r"branch|branches|ivy|vines?|flowers?|weeds?|needles?|pine|palm|bamboo|reeds?|hedge|bonsai|"
                      r"bark|trunk|moss_clump|card|cards)(_|\d|$)")
_FLUTTER_RE = re.compile(r"leaf|leaves|foliage|grass|fern|bush|shrub|plant|ivy|vine|flower|weed|needle|bamboo|reed|"
                         r"hedge|card")


def classify(mat):
    """Animation kind of a material: water, waterfall, lava, screen, screen_scroll, energy, effect, wind, None."""
    from .castfile import GROUP_KEY
    low = mat.name.lower()
    if mat.get("r5_energy") or mat.get("r5_vfx"):
        return "energy"
    if mat.get("r5_glow"):
        return "screen_scroll" if _SCREEN_RE.search(low) else "energy"
    g = mat.get(GROUP_KEY)
    if g == "particle":
        return "effect"
    if g:
        return None
    if _LAVA_RE.search(low) and "static" not in low:
        return "lava"
    if _WATER_RE.search(low) and not _NOT_WATER_RE.search(low):
        return "waterfall" if _FALL_RE.search(low) else "water"
    if _SCREEN_RE.search(low) and not _NOT_SCREEN_RE.search(low):
        return "screen_scroll" if _SCROLLING_SCREEN_RE.search(low) else "screen"
    if _EFFECT_RE.search(low) and not _NOT_EFFECT_RE.search(low):
        return "effect"
    if _WIND_RE.search(low):
        return "wind"
    return None


def _drive(sock, scene, expr, variables):
    """Driver on a node socket value. variables: [(name, data_path on the scene)]."""
    try:
        sock.driver_remove("default_value")
    except (TypeError, RuntimeError):
        pass
    fc = sock.driver_add("default_value")
    drv = fc.driver
    drv.type = "SCRIPTED"
    for name, path in variables:
        v = drv.variables.new()
        v.name = name
        v.type = "SINGLE_PROP"
        t = v.targets[0]
        t.id_type = "SCENE"
        t.id = scene
        t.data_path = path
    drv.expression = expr       # simple expression: evaluated without Python (no auto-run needed)


def _clock(scene):
    ng = bpy.data.node_groups.get(CLOCK)
    if ng is not None and ng.get("apx_scene") == scene.name:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(CLOCK, "ShaderNodeTree")
        for name, _p in CLOCK_SPEEDS:
            ng.interface.new_socket(name, in_out="OUTPUT", socket_type="NodeSocketFloat")
        ng.interface.new_socket("wind_amp", in_out="OUTPUT", socket_type="NodeSocketFloat")
        ng.interface.new_socket("flicker", in_out="OUTPUT", socket_type="NodeSocketFloat")
    N, L = ng.nodes, ng.links
    N.clear()
    out = N.new("NodeGroupOutput"); out.location = (600, 0)
    for i, (name, prop) in enumerate(CLOCK_SPEEDS):
        v = N.new("ShaderNodeValue"); v.name = v.label = name; v.location = (0, -i * 120)
        _drive(v.outputs[0], scene, "frame / fps * g * k",
               [("fps", "render.fps"), ("g", "r5_settings.shader_speed"), ("k", f"r5_settings.{prop}")])
        L.new(v.outputs[0], out.inputs[name])
    amp = N.new("ShaderNodeValue"); amp.name = amp.label = "wind_amp"; amp.location = (0, -800)
    _drive(amp.outputs[0], scene, "k", [("k", "r5_settings.wind_strength")])
    L.new(amp.outputs[0], out.inputs["wind_amp"])
    # screen flicker: 0.82..1 from two sines of the screens clock
    scr = N["screens"].outputs[0]
    s1 = N.new("ShaderNodeMath"); s1.operation = "MULTIPLY"; s1.inputs[1].default_value = 23.0
    s2 = N.new("ShaderNodeMath"); s2.operation = "MULTIPLY"; s2.inputs[1].default_value = 7.3
    L.new(scr, s1.inputs[0]); L.new(scr, s2.inputs[0])
    a = N.new("ShaderNodeMath"); a.operation = "SINE"; L.new(s1.outputs[0], a.inputs[0])
    b = N.new("ShaderNodeMath"); b.operation = "SINE"; L.new(s2.outputs[0], b.inputs[0])
    m = N.new("ShaderNodeMath"); m.operation = "MULTIPLY"; L.new(a.outputs[0], m.inputs[0]); L.new(b.outputs[0], m.inputs[1])
    f = N.new("ShaderNodeMath"); f.operation = "MULTIPLY_ADD"; L.new(m.outputs[0], f.inputs[0])
    f.inputs[1].default_value = 0.09
    f.inputs[2].default_value = 0.91
    L.new(f.outputs[0], out.inputs["flicker"])
    for i, n in enumerate((s1, s2, a, b, m, f)):
        n.location = (200 + (i // 2) * 120, -900 - (i % 2) * 120)
    ng["apx_scene"] = scene.name
    return ng


def _new(nt, kind, name, loc):
    n = nt.nodes.new(kind)
    n.name = f"{TAG}_{name}"
    n.label = ""
    n.location = loc
    return n


def _output(nt):
    outs = [n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"]
    for n in outs:
        if n.is_active_output:
            return n
    return outs[0] if outs else None


# (du, dv) per second at speed 1, in UV units
_SCROLL = {"water": (0.012, 0.025), "waterfall": (0.0, 0.35), "lava": (0.004, 0.012), "screen_scroll": (0.0, 0.12),
           "energy": (0.015, 0.03), "effect": (0.0, 0.06)}
_CLOCK_OF = {"water": "water", "waterfall": "water", "lava": "lava", "screen": "screens", "screen_scroll": "screens",
             "energy": "energy", "effect": "effects", "wind": "wind", "cloth": "wind"}
# models whose cloth waves in game (vertex-animated banners and flags); their own materials get the cloth flutter
_NOT_CLOTH_MODEL_RE = re.compile(r"flag_?base|flag_?pole|_pole$|connection|holder|bracket")


def _cloth_materials():
    """Materials used ONLY by banner / flag models (a material shared with other props must not wave)."""
    cloth, other = set(), set()
    lib = bpy.data.collections.get(library.LIB_COLLECTION)
    if lib is None:
        return set()
    for model in lib.children:
        low = model.name.lower()
        named = re.search(r"banner|(^|_)flags?(_|$)|fan_cloth", low) is not None
        wind = low.endswith("_wind") and not _WIND_RE.search(low[:-5])      # "_wind" trees are foliage
        is_cloth = (named or wind) and not _NOT_CLOTH_MODEL_RE.search(low)
        for o in model.all_objects:
            if o.type == "MESH":
                for m in o.data.materials:
                    if m is not None:
                        (cloth if is_cloth else other).add(m.name)
    return cloth - other


def _inject(mat, kind, clock):
    nt = mat.node_tree
    if nt is None or any(n.name.startswith(TAG) for n in nt.nodes):
        return False
    cg = _new(nt, "ShaderNodeGroup", "clock", (-1700, 900))
    cg.node_tree = clock
    t = cg.outputs[_CLOCK_OF[kind]]
    L = nt.links
    done = False
    if kind in _SCROLL:
        du, dv = _SCROLL[kind]
        if kind == "effect" and "cloud" in mat.name.lower():
            du, dv = 0.004, 0.0
        sc = _new(nt, "ShaderNodeVectorMath", "offset", (-1500, 900))
        sc.operation = "SCALE"
        sc.inputs[0].default_value = (du, dv, 0.0)
        L.new(t, sc.inputs["Scale"])
        uv = None
        for img in [n for n in nt.nodes if n.type == "TEX_IMAGE"]:
            vin = img.inputs["Vector"]
            add = _new(nt, "ShaderNodeVectorMath", f"add_{img.name}", (img.location.x - 200, img.location.y))
            add.operation = "ADD"
            if vin.is_linked:
                L.new(vin.links[0].from_socket, add.inputs[0])
            else:
                if uv is None:
                    uv = _new(nt, "ShaderNodeUVMap", "uv", (-1500, 700))
                    uv.uv_map = "UVMap"
                L.new(uv.outputs[0], add.inputs[0])
            L.new(sc.outputs[0], add.inputs[1])
            L.new(add.outputs[0], vin)
            done = True
    if kind in ("screen", "screen_scroll"):
        # screens and holograms flicker a little
        for n in [n for n in nt.nodes if n.type in ("BSDF_PRINCIPLED", "EMISSION")]:
            sock = n.inputs.get("Emission Strength") if n.type == "BSDF_PRINCIPLED" else n.inputs.get("Strength")
            if sock is None or sock.is_linked:
                continue
            mul = _new(nt, "ShaderNodeMath", f"flicker_{n.name}", (n.location.x - 200, n.location.y - 400))
            mul.operation = "MULTIPLY"
            mul.inputs[0].default_value = sock.default_value
            L.new(cg.outputs["flicker"], mul.inputs[1])
            L.new(mul.outputs[0], sock)
            done = True
    if kind in ("wind", "cloth"):
        done = _inject_wind(mat, nt, cg, cloth=kind == "cloth") or done
    if not done:
        for n in [n for n in nt.nodes if n.name.startswith(TAG)]:
            nt.nodes.remove(n)
        return False
    mat["r5_anim"] = kind
    return True


def _inject_wind(mat, nt, cg, cloth=False):
    """Foliage sway: world-space vertex displacement growing with the height in the model (the base stays put,
    so leaves stay attached to their branches), plus a fast flutter on leaves / grass.
    cloth: banners and flags ripple along their normal instead (waves running across the cloth)."""
    out = _output(nt)
    if out is None or out.inputs["Displacement"].is_linked:
        return False
    L = nt.links
    x0, y0 = out.location.x - 1400, out.location.y - 600
    if cloth:
        return _inject_cloth(mat, nt, cg, out, x0, y0)

    def m(op, a, b=None, name="m", loc=(0, 0)):
        n = _new(nt, "ShaderNodeMath", name, loc)
        n.operation = op
        for i, v in ((0, a), (1, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                L.new(v, n.inputs[i])
        return n.outputs[0]

    tc = _new(nt, "ShaderNodeTexCoord", "tc", (x0, y0))
    sep_o = _new(nt, "ShaderNodeSeparateXYZ", "sep_o", (x0 + 180, y0))
    L.new(tc.outputs["Object"], sep_o.inputs[0])
    geo = _new(nt, "ShaderNodeNewGeometry", "geo", (x0, y0 - 250))
    sep_w = _new(nt, "ShaderNodeSeparateXYZ", "sep_w", (x0 + 180, y0 - 250))
    L.new(geo.outputs["Position"], sep_w.inputs[0])
    h = m("MAXIMUM", sep_o.outputs[2], 0.0, "h", (x0 + 360, y0))
    h = m("MINIMUM", h, 10.0, "hc", (x0 + 480, y0))
    amp = m("MULTIPLY", h, cg.outputs["wind_amp"], "amp", (x0 + 600, y0))
    amp = m("MULTIPLY", amp, 0.012, "amp2", (x0 + 720, y0))
    ph = m("MULTIPLY", sep_w.outputs[0], 0.07, "phx", (x0 + 360, y0 - 250))
    ph = m("ADD", ph, m("MULTIPLY", sep_w.outputs[1], 0.05, "phy", (x0 + 360, y0 - 350)), "ph", (x0 + 480, y0 - 250))
    t = cg.outputs["wind"]
    ax = m("SINE", m("ADD", m("MULTIPLY", t, 2.2, "tx", (x0 + 600, y0 - 150)), ph, "ax0", (x0 + 720, y0 - 150)),
           None, "ax1", (x0 + 840, y0 - 150))
    ay = m("SINE", m("ADD", m("MULTIPLY", t, 1.7, "ty", (x0 + 600, y0 - 250)),
                     m("MULTIPLY", ph, 1.3, "ph13", (x0 + 600, y0 - 350)), "ay0", (x0 + 720, y0 - 250)),
           None, "ay1", (x0 + 840, y0 - 250))
    dx = m("MULTIPLY", ax, amp, "dx", (x0 + 960, y0 - 100))
    dy = m("MULTIPLY", m("MULTIPLY", ay, amp, "dy0", (x0 + 960, y0 - 200)), 0.7, "dy", (x0 + 1080, y0 - 200))
    comb = _new(nt, "ShaderNodeCombineXYZ", "disp", (x0 + 1200, y0 - 150))
    L.new(dx, comb.inputs[0])
    L.new(dy, comb.inputs[1])
    if _FLUTTER_RE.search(mat.name.lower()):
        fl = m("ADD", m("MULTIPLY", t, 9.0, "ft", (x0 + 600, y0 - 450)),
               m("MULTIPLY", sep_w.outputs[2], 4.0, "fz", (x0 + 600, y0 - 550)), "f0", (x0 + 720, y0 - 450))
        fl = m("ADD", fl, m("MULTIPLY", ph, 60.0, "fp", (x0 + 720, y0 - 550)), "f1", (x0 + 840, y0 - 450))
        fl = m("MULTIPLY", m("SINE", fl, None, "f2", (x0 + 960, y0 - 450)), cg.outputs["wind_amp"], "f3",
               (x0 + 1080, y0 - 450))
        L.new(m("MULTIPLY", fl, 0.015, "f4", (x0 + 1150, y0 - 450)), comb.inputs[2])
    vd = _new(nt, "ShaderNodeVectorDisplacement", "vdisp", (x0 + 1350, y0 - 150))
    vd.space = "WORLD"
    vd.inputs["Midlevel"].default_value = 0.0
    vd.inputs["Scale"].default_value = 1.0
    L.new(comb.outputs[0], vd.inputs["Vector"])
    L.new(vd.outputs[0], out.inputs["Displacement"])
    if "r5_anim_disp" not in mat:
        mat["r5_anim_disp"] = mat.displacement_method
    mat.displacement_method = "DISPLACEMENT"
    return True


def _inject_cloth(mat, nt, cg, out, x0, y0):
    L = nt.links

    def m(op, a, b=None, name="m", loc=(0, 0)):
        n = _new(nt, "ShaderNodeMath", name, loc)
        n.operation = op
        for i, v in ((0, a), (1, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                L.new(v, n.inputs[i])
        return n.outputs[0]

    tc = _new(nt, "ShaderNodeTexCoord", "tc", (x0, y0))
    sep = _new(nt, "ShaderNodeSeparateXYZ", "sep_o", (x0 + 180, y0))
    L.new(tc.outputs["Object"], sep.inputs[0])
    geo = _new(nt, "ShaderNodeNewGeometry", "geo", (x0, y0 - 250))
    t = cg.outputs["wind"]
    # two waves travelling across the cloth (object space, metres)
    w1 = m("SINE", m("ADD", m("ADD", m("MULTIPLY", t, 4.0, "c1", (x0 + 360, y0)),
                              m("MULTIPLY", sep.outputs[0], 2.5, "c2", (x0 + 360, y0 - 100)), "c3", (x0 + 480, y0)),
                     m("MULTIPLY", sep.outputs[2], 1.7, "c4", (x0 + 360, y0 - 200)), "c5", (x0 + 600, y0)),
           None, "c6", (x0 + 720, y0))
    w2 = m("SINE", m("ADD", m("MULTIPLY", t, 7.3, "c7", (x0 + 360, y0 - 300)),
                     m("MULTIPLY", sep.outputs[1], 5.0, "c8", (x0 + 360, y0 - 400)), "c9", (x0 + 480, y0 - 300)),
           None, "c10", (x0 + 600, y0 - 300))
    w = m("ADD", m("MULTIPLY", w1, 0.65, "c11", (x0 + 840, y0)), m("MULTIPLY", w2, 0.35, "c12", (x0 + 720, y0 - 300)),
          "c13", (x0 + 960, y0))
    a = m("MULTIPLY", m("MULTIPLY", w, cg.outputs["wind_amp"], "c14", (x0 + 1080, y0)), 0.04, "c15", (x0 + 1200, y0))
    sc = _new(nt, "ShaderNodeVectorMath", "cloth_n", (x0 + 1300, y0 - 150))
    sc.operation = "SCALE"
    L.new(geo.outputs["Normal"], sc.inputs[0])
    L.new(a, sc.inputs["Scale"])
    vd = _new(nt, "ShaderNodeVectorDisplacement", "vdisp", (x0 + 1500, y0 - 150))
    vd.space = "WORLD"
    vd.inputs["Midlevel"].default_value = 0.0
    vd.inputs["Scale"].default_value = 1.0
    L.new(sc.outputs[0], vd.inputs["Vector"])
    L.new(vd.outputs[0], out.inputs["Displacement"])
    if "r5_anim_disp" not in mat:
        mat["r5_anim_disp"] = mat.displacement_method
    mat.displacement_method = "DISPLACEMENT"
    return True


def _remove_tagged(mat, prefix):
    """Remove the nodes added by this module and give back the links they had replaced."""
    nt = mat.node_tree
    if nt is None:
        return False
    tagged = [n for n in nt.nodes if n.name.startswith(prefix)]
    if not tagged:
        return False
    L = nt.links
    for n in tagged:
        # a vector offset / mix inserted in front of existing links: reconnect its original source
        if n.name.startswith(f"{TAG}_add_") and n.inputs[0].is_linked:
            src = n.inputs[0].links[0].from_socket
            if not src.node.name.startswith(prefix):
                for lk in list(n.outputs[0].links):
                    L.new(src, lk.to_socket)
        if n.name == f"{FADE_TAG}_mix" and n.inputs[2].is_linked:
            src = n.inputs[2].links[0].from_socket
            for lk in list(n.outputs[0].links):
                L.new(src, lk.to_socket)
    for n in tagged:
        nt.nodes.remove(n)
    return True


def _materials(scene):
    """Materials of this add-on (map geometry and prop models)."""
    return [m for m in bpy.data.materials if m.get("apx_cast") and m.use_nodes and m.node_tree]


def shaders_enabled():
    ng = bpy.data.node_groups.get(CLOCK)
    return ng is not None and ng.users > 0


def enable_shaders(scene, foliage=True):
    clock = _clock(scene)
    counts = {}
    cloth = _cloth_materials() if foliage else set()
    for mat in _materials(scene):
        kind = "cloth" if mat.name in cloth else classify(mat)
        if kind is None or (kind in ("wind", "cloth") and not foliage):
            continue
        if _inject(mat, kind, clock):
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def disable_shaders(scene):
    n = 0
    for mat in bpy.data.materials:
        if _remove_tagged(mat, TAG):
            n += 1
        if "r5_anim_disp" in mat:
            try:
                mat.displacement_method = mat["r5_anim_disp"]
            except (TypeError, ValueError):
                mat.displacement_method = "BUMP"
            del mat["r5_anim_disp"]
        if "r5_anim" in mat:
            del mat["r5_anim"]
    return n


def shader_candidates(scene):
    counts = {}
    for mat in _materials(scene):
        k = classify(mat)
        if k:
            counts[k] = counts.get(k, 0) + 1
    return counts


# ==========================================================================
# LOD cross-fade (material side)
# ==========================================================================
FADE_GROUP = "R5_LODFade"
FADE_TAG = "R5FADE"


def _fade_group():
    ng = bpy.data.node_groups.get(FADE_GROUP)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(FADE_GROUP, "ShaderNodeTree")
    ng.interface.new_socket("Visible", in_out="OUTPUT", socket_type="NodeSocketFloat")
    N, L = ng.nodes, ng.links
    out = N.new("NodeGroupOutput"); out.location = (900, 0)
    at = N.new("ShaderNodeAttribute"); at.location = (-600, 200)
    at.attribute_type = "INSTANCER"
    at.attribute_name = gn_instancer.FADE_ATTR
    v = at.outputs["Fac"]
    # noise fixed on the model (object coordinates, 2 cm cells): both LODs of a prop read the same value at
    # the same place, and it does not crawl from frame to frame
    tc = N.new("ShaderNodeTexCoord"); tc.location = (-800, -100)
    snap = N.new("ShaderNodeVectorMath"); snap.operation = "SNAP"; snap.location = (-600, -100)
    snap.inputs[1].default_value = (0.02, 0.02, 0.02)
    L.new(tc.outputs["Object"], snap.inputs[0])
    wn = N.new("ShaderNodeTexWhiteNoise"); wn.noise_dimensions = "3D"; wn.location = (-400, -100)
    L.new(snap.outputs[0], wn.inputs["Vector"])
    n = wn.outputs["Value"]

    def m(op, a, b, loc):
        x = N.new("ShaderNodeMath"); x.operation = op; x.location = loc
        for i, val in ((0, a), (1, b)):
            if isinstance(val, (int, float)):
                x.inputs[i].default_value = val
            else:
                L.new(val, x.inputs[i])
        return x.outputs[0]

    a = m("SUBTRACT", 1.0, m("LESS_THAN", n, v, (-200, 200)), (0, 200))         # leaving LOD: noise >= v
    nv = m("MULTIPLY", v, -1.0, (-200, 0))
    b = m("LESS_THAN", n, nv, (0, 0))                                            # arriving LOD: noise < -v
    s = m("LESS_THAN", v, 0.0, (0, -200))                                       # arriving?
    diff = m("SUBTRACT", b, a, (200, 0))
    prod = m("MULTIPLY", s, diff, (400, 0))
    vis = m("ADD", a, prod, (600, 0))
    L.new(vis, out.inputs["Visible"])
    return ng


def lod_fade_ready():
    ng = bpy.data.node_groups.get(FADE_GROUP)
    return ng is not None and ng.users > 0


def _prop_materials():
    lib = bpy.data.collections.get(library.LIB_COLLECTION)
    mats = set()
    if lib is None:
        return mats
    for o in lib.all_objects:
        if o.type == "MESH":
            for m in o.data.materials:
                if m is not None and m.use_nodes and m.node_tree:
                    mats.add(m)
    return mats


def ensure_lod_fade(scene):
    """Add the LOD fade mask to every prop material (once). Returns the number of materials changed."""
    ng = _fade_group()
    n = 0
    for mat in _prop_materials():
        nt = mat.node_tree
        if any(x.name.startswith(FADE_TAG) for x in nt.nodes):
            continue
        out = _output(nt)
        if out is None or not out.inputs["Surface"].is_linked:
            continue
        src = out.inputs["Surface"].links[0].from_socket
        L = nt.links
        g = nt.nodes.new("ShaderNodeGroup"); g.node_tree = ng
        g.name = f"{FADE_TAG}_group"; g.location = (out.location.x - 200, out.location.y + 300)
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        tr.name = f"{FADE_TAG}_tr"; tr.location = (out.location.x - 200, out.location.y + 160)
        mix = nt.nodes.new("ShaderNodeMixShader")
        mix.name = f"{FADE_TAG}_mix"; mix.location = (out.location.x - 20, out.location.y + 200)
        L.new(g.outputs[0], mix.inputs[0])
        L.new(tr.outputs[0], mix.inputs[1])
        L.new(src, mix.inputs[2])
        L.new(mix.outputs[0], out.inputs["Surface"])
        mat["r5_lodfade"] = True
        n += 1
    return n


def remove_lod_fade():
    n = 0
    for mat in bpy.data.materials:
        if _remove_tagged(mat, FADE_TAG):
            n += 1
        if "r5_lodfade" in mat:
            del mat["r5_lodfade"]
    ng = bpy.data.node_groups.get(FADE_GROUP)
    if ng is not None and ng.users == 0:
        bpy.data.node_groups.remove(ng)
    return n


def after_import(scene):
    """New props / materials while something is switched on: give them the same treatment."""
    s = scene.r5_settings
    if movers_enabled(scene):
        enable_movers(scene)
    if shaders_enabled():
        enable_shaders(scene, s.anim_foliage)
    if s.lod_fade:
        ensure_lod_fade(scene)


# ==========================================================================
# Operators
# ==========================================================================
class R5_OT_movers(bpy.types.Operator):
    """Make the moving props move (fans, turbines, rotating rings, banners, balloons, Tridents). Each moving model
    is re-evaluated on every frame: switch it on once the map is loaded"""
    bl_idname = "r5.movers"
    bl_label = "Movers"
    bl_options = {"REGISTER", "UNDO"}

    enable: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        if self.enable:
            if not gn_instancer.instancer_objects(context.scene):
                self.report({"WARNING"}, "Movers need a map imported with Geometry Nodes instancing")
                return {"CANCELLED"}
            n_obj, n_props = enable_movers(context.scene)
            self.report({"INFO"}, f"{n_props} props of {n_obj} models now move (play the timeline)")
        else:
            self.report({"INFO"}, f"{disable_movers(context.scene)} models stopped")
        return {"FINISHED"}


class R5_OT_anim_shaders(bpy.types.Operator):
    """Animate the materials: water, lava, screens / holograms, energy, smoke / fire / clouds and the wind in the
    foliage. Switch it on once the map is loaded (it recompiles the changed materials)"""
    bl_idname = "r5.anim_shaders"
    bl_label = "Animated shaders"
    bl_options = {"REGISTER", "UNDO"}

    enable: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        if self.enable:
            counts = enable_shaders(context.scene, context.scene.r5_settings.anim_foliage)
            txt = ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "no new material"
            self.report({"INFO"}, f"Animated: {txt} (play the timeline)")
        else:
            self.report({"INFO"}, f"{disable_shaders(context.scene)} materials back to still")
        return {"FINISHED"}


CLASSES = (R5_OT_movers, R5_OT_anim_shaders)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
