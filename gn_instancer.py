"""Instanciation par Geometry Nodes : UN objet par modèle unique.

Chaque modèle = un nuage de points (1 point par prop, attributs apx_rot / apx_scale)
+ un modificateur Geometry Nodes partagé qui instancie la bonne collection LOD
selon la distance à un empty "sonde" (APX_LOD_Probe). Le timer déplace juste la
sonde sur la vue -> tout le calcul de LOD se fait dans Blender, en C++.

Idéal pour les grosses maps (des centaines de milliers de props).
"""

import bpy
import numpy as np

GROUP = "APX_Instancer"
GROUP_VERSION = 2
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
    if ng is not None:
        ng.name = GROUP + "_old"
    ng = bpy.data.node_groups.new(GROUP, "GeometryNodeTree")
    ng["apx_ver"] = GROUP_VERSION

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
    _sock(ng, "Use Cull", "NodeSocketBool", default=True)

    N, L = ng.nodes, ng.links
    gi = N.new("NodeGroupInput"); gi.location = (-1400, 0)
    go = N.new("NodeGroupOutput"); go.location = (900, 0)

    def math(op, a=None, b=None, loc=(0, 0)):
        n = N.new("ShaderNodeMath"); n.operation = op; n.location = loc
        for idx, v in ((0, a), (1, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[idx].default_value = v
            else:
                L.new(v, n.inputs[idx])
        return n.outputs[0]

    def compare(op, a, b, loc):
        n = N.new("FunctionNodeCompare"); n.data_type = "FLOAT"; n.operation = op; n.location = loc
        if isinstance(a, (int, float)):
            n.inputs[0].default_value = a
        else:
            L.new(a, n.inputs[0])
        if isinstance(b, (int, float)):
            n.inputs[1].default_value = b
        else:
            L.new(b, n.inputs[1])
        return n.outputs[0]

    def boolean(op, a, b, loc):
        n = N.new("FunctionNodeBooleanMath"); n.operation = op; n.location = loc
        L.new(a, n.inputs[0]); L.new(b, n.inputs[1])
        return n.outputs[0]

    oi = N.new("GeometryNodeObjectInfo"); oi.location = (-1200, 300)
    oi.transform_space = "RELATIVE"
    L.new(gi.outputs["Probe"], oi.inputs["Object"])
    pos = N.new("GeometryNodeInputPosition"); pos.location = (-1200, 100)
    dist = N.new("ShaderNodeVectorMath"); dist.operation = "DISTANCE"; dist.location = (-1000, 200)
    L.new(pos.outputs[0], dist.inputs[0]); L.new(oi.outputs["Location"], dist.inputs[1])
    d = math("MULTIPLY", dist.outputs["Value"], gi.outputs["Dist Scale"], (-850, 200))

    lvl = None
    for i in range(1, MAX_LEVELS):
        c = compare("GREATER_EQUAL", d, gi.outputs[f"D{i}"], (-700, 400 - i * 120))
        lvl = c if lvl is None else math("ADD", lvl, c, (-550, 400 - i * 120))
    top = math("SUBTRACT", gi.outputs["Levels"], 1.0, (-700, -300))
    lvl = math("MINIMUM", lvl, top, (-400, 200))
    forced = math("MINIMUM", gi.outputs["Forced"], top, (-400, -300))
    sw = N.new("GeometryNodeSwitch"); sw.input_type = "FLOAT"; sw.location = (-250, 0)
    L.new(gi.outputs["Use LOD"], sw.inputs[0])
    L.new(forced, sw.inputs[1])
    L.new(lvl, sw.inputs[2])
    level = sw.outputs[0]

    far = compare("GREATER_EQUAL", d, gi.outputs["Cull"], (-400, -500))
    cull_on = boolean("AND", gi.outputs["Use Cull"], gi.outputs["Use LOD"], (-400, -650))
    culled = boolean("AND", far, cull_on, (-250, -550))

    rot = N.new("GeometryNodeInputNamedAttribute"); rot.data_type = "FLOAT_VECTOR"; rot.location = (-250, 500)
    rot.inputs["Name"].default_value = "apx_rot"
    scl = N.new("GeometryNodeInputNamedAttribute"); scl.data_type = "FLOAT"; scl.location = (-250, 350)
    scl.inputs["Name"].default_value = "apx_scale"

    join = N.new("GeometryNodeJoinGeometry"); join.location = (700, 0)
    for i in range(MAX_LEVELS):
        y = 600 - i * 300
        eq = compare("EQUAL", level, float(i), (0, y))
        eq.node.inputs["Epsilon"].default_value = 0.25
        sel = boolean("NIMPLY", eq, culled, (150, y))
        ci = N.new("GeometryNodeCollectionInfo"); ci.location = (150, y - 120)
        ci.transform_space = "ORIGINAL"
        L.new(gi.outputs[f"LOD{i}"], ci.inputs["Collection"])
        iop = N.new("GeometryNodeInstanceOnPoints"); iop.location = (450, y)
        L.new(gi.outputs["Geometry"], iop.inputs["Points"])
        L.new(sel, iop.inputs["Selection"])
        L.new(ci.outputs[0], iop.inputs["Instance"])
        L.new(rot.outputs[0], iop.inputs["Rotation"])
        L.new(scl.outputs[0], iop.inputs["Scale"])
        L.new(iop.outputs[0], join.inputs[0])
    L.new(join.outputs[0], go.inputs[0])
    return ng


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
        p.empty_display_type = "SPHERE"
        p.empty_display_size = 0.5
        p.hide_render = True
    if p.name not in scene.collection.objects:
        scene.collection.objects.link(p)
    return p


def move_probe(scene, eye):
    p = get_probe(scene)
    if (p.location - eye).length > 1e-4:
        p.location = eye


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
    """Pousse les réglages LOD de la scène dans tous les modificateurs."""
    from . import library
    s = scene.apx_settings
    ng = bpy.data.node_groups.get(GROUP)
    if ng is None:
        return 0
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
        cull = s.cull_distance
        if s.cull_small_props and radius < s.small_prop_size:
            cull = min(cull, s.small_prop_distance * scale)
        vals = {
            "Dist Scale": scale,
            "Cull": cull if s.use_culling else 1e12,
            "Use Cull": s.use_culling,
            "Use LOD": s.use_lod and force_level is None,
            "Forced": force_level if force_level is not None else s.forced_lod,
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
