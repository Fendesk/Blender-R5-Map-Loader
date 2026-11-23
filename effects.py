"""Map effects (sparks, electricity, steam, fire, fog, dust, snow, insects...), falling leaves and a water plane.

What the exports give: the info_particle_system entities of the map (entities/<map>_fx.ent...): an effect NAME
(P_sparks_dir_MD_LOOP, P_env_steam_bunker_256_lit, env_ground_fog_256...), a position, an orientation and a
scale. The effects themselves (particle systems of the game's effect paks) are not in the exports: each name is
sorted into a kind and drawn by a procedural Geometry Nodes particle system. The particles are computed from the
scene time (looping, no simulation cache): every frame is direct, scrubbing and renders work, and nothing is
evaluated while the timeline does not move. The size in the name (_64, _256, _1k: game units) is the area of the
area effects (fog, dust, leaves, snow).

  - Effects: one point cloud object per kind (R5_FX_<kind>), the effect entities as points.
  - Falling leaves: the leaf / petal effects of the map, plus leaves falling from every tree of the map
    (R5_FX_tree_leaves: one point per tree, at its canopy, tinted with the colour of its foliage).
  - Water plane: a large animated water surface around the map (R5_Water_Plane), at the map's sea level.
"""

import json
import math
import re

import bpy
import numpy as np
from bpy.props import (BoolProperty, CollectionProperty, FloatProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)
from mathutils import Euler, Vector

from . import library

ROOT = "R5_Effects"
GROUP_VERSION = 1
UNIT = 0.0254

# (id, label, name regex, node group, shading, colour, parameters)
#   group: SPRITE (camera-facing quads), TUMBLE (flat quads turning: leaves, butterflies), ARC (electric arcs)
#   shading: EMIT (additive glow), LIT (soft lit puffs), LEAF (cut-out leaf shape, lit)
#   dir: UP (rise), FWD (the entity's forward axis), NONE
#   area: default half size of the emission box (m) when the name has no size; area_names: the name size is used
KINDS = (
    ("electric", "Electricity", r"elec|zap|lightning|tesla|shock|(^|_)arcs?(_|$)|_nrg(_|$)|phase_nrg|ground_phase",
     "ARC", "EMIT", (0.35, 0.72, 1.0),
     dict(count=3, size=1.2, flicker=12.0, on=0.55, thickness=0.006, strength=6.0)),
    ("sparks", "Sparks & fireworks", r"spark|weld|firework|burst_spark",
     "SPRITE", "EMIT", (1.0, 0.55, 0.15),
     dict(count=22, life=0.9, speed=3.0, rise=1.5, gravity=-9.8, spread=1.6, size=0.035, grow=-0.6, strength=6.0,
          dir="FWD", area=0.05)),
    ("fire", "Fire, lava & embers", r"fire|flame|ember|burn|torch|fireball|lava|magma|heat",
     "SPRITE", "EMIT", (1.0, 0.42, 0.08),
     dict(count=16, life=1.3, rise=1.4, spread=0.35, sway=0.15, size=0.3, grow=-0.6, strength=4.0, area=0.3,
          area_names=True)),
    ("water", "Water spray, drips & bubbles", r"water|splash|ripple|drip|rain|spray|foam|fountain|gurgle|bubs|bubble",
     "SPRITE", "LIT", (0.85, 0.92, 1.0),
     dict(count=14, life=1.1, rise=2.0, gravity=-9.8, spread=1.0, size=0.035, opacity=0.7, area=0.2)),
    ("smoke", "Steam & smoke", r"steam|smoke|vent|exhaust|vapou?r|chimney|humid|leak|jet|duct|fan_suck|geyser|sulfu?er|"
                               r"cooling",
     "SPRITE", "LIT", (0.86, 0.86, 0.86),
     dict(count=12, life=4.0, rise=1.1, spread=0.3, sway=0.25, size=0.5, grow=2.5, opacity=0.22, area=0.15)),
    ("snow", "Snow", r"snow(?!.*noflake)|flake",
     "SPRITE", "LIT", (1.0, 1.0, 1.0),
     dict(count=40, life=9.0, rise=-0.7, sway=0.6, size=0.025, opacity=0.9, area=3.0, area_names=True)),
    ("leaves", "Falling leaves & petals", r"lea(f|ves)|petal|flower|cherry|autumn|blossom|seeds?(_|$)",
     "TUMBLE", "LEAF", (0.55, 0.42, 0.12),
     dict(count=16, life=8.0, rise=-0.6, sway=0.8, spin=2.0, size=0.07, opacity=1.0, area=2.0, area_names=True)),
    ("bugs", "Insects & butterflies", r"bugs?(_|$)|butterfl|insect|moth|flies|firefl|bird",
     "TUMBLE", "LIT", (0.95, 0.75, 0.3),
     dict(count=4, life=1e4, orbit=1.5, orbit_speed=1.3, sway=0.3, spin=8.0, size=0.05, opacity=1.0, steady=1.0,
          area=0.8, area_names=True)),
    ("lights", "Blinking lights", r"blink|beacon|strobe|warn_light|light_tower|signal|flash",
     "SPRITE", "EMIT", (1.0, 0.15, 0.05),
     dict(count=1, life=1e4, blink=1.0, size=0.25, strength=8.0, steady=1.0, area=0.0)),
    ("fog", "Fog & mist", r"fog|mist|haze|cloud|atmo|noflake",
     "SPRITE", "LIT", (0.8, 0.82, 0.86),
     dict(count=6, life=14.0, rise=0.05, sway=0.3, size=3.0, grow=0.5, opacity=0.12, area=3.0, area_names=True,
          flat=True)),
    ("dust", "Dust, wind & pollen", r"dust|wind|debris|sand|ash(_|$)|motes|particulate|pollen|rift|array_grnd|spore",
     "SPRITE", "LIT", (0.75, 0.7, 0.6),
     dict(count=25, life=6.0, speed=0.8, rise=0.1, sway=0.5, size=0.02, opacity=0.6, area=2.0, area_names=True,
          dir="WIND")),
    ("glow", "Glows, beams & screens light", r"glow|holo|spot_?light|portal|ring|beam|flare|lamp|light|lens|screen",
     "SPRITE", "EMIT", (1.0, 0.92, 0.8),
     dict(count=1, life=1e4, size=0.6, strength=3.0, steady=1.0, area=0.0)),
)
KIND = {k[0]: k for k in KINDS}
_KIND_RX = [(k[0], re.compile(k[2])) for k in KINDS]
_FWD_RX = re.compile(r"(^|_)dir(_|$)|jet|leak|duct|exhaust|spray|_45deg|vent_side")
_SKIP_RX = re.compile(r"rat_circle|(^|_)rats?(_|$)|editor|dev_|_test(_|$)")
_SIZE_RX = re.compile(r"(?:^|_)(\d{2,4})(k?)(?=_|$)")
_MULT = ((r"(^|_)(xl|xxl|huge)(_|$)", 2.2), (r"(^|_)(lg|lrg|large|big)(_|$)", 1.6), (r"(^|_)(md|med)(_|$)", 1.0),
         (r"(^|_)(sm|sml|small|s)(_|$)", 0.6))
_COLORS = (("magenta", (1.0, 0.15, 0.8)), ("pink", (1.0, 0.45, 0.7)), ("purple", (0.6, 0.25, 1.0)),
           ("violet", (0.6, 0.25, 1.0)), ("cyan", (0.2, 0.9, 1.0)), ("blue", (0.25, 0.5, 1.0)),
           ("green", (0.3, 1.0, 0.35)), ("yellow", (1.0, 0.85, 0.2)), ("gold", (1.0, 0.7, 0.2)),
           ("orange", (1.0, 0.5, 0.1)), ("red", (1.0, 0.12, 0.06)), ("white", (1.0, 1.0, 1.0)),
           ("autumn", (0.85, 0.4, 0.1)), ("cherry", (1.0, 0.6, 0.75)), ("phase", (0.75, 0.35, 1.0)))
TREE_RX = re.compile(r"(^|_)(trees?|palms?|cypress|cherry_blossom$|bamboo_tree|coconut|pine|oak|birch|maple|willow|"
                     r"cedar|redwood|sakura)(_|\d|$)")
NOT_TREE_RX = re.compile(r"root|ground|debris|stump|dead|(^|_)logs?(_|$)|trunk|tendril|sign|street|bark|branch_|"
                         r"burnt|bare|cut|fallen|_base|planter|pot(_|$)")


def classify(name):
    low = name.lower()
    if _SKIP_RX.search(low):
        return None
    for kid, rx in _KIND_RX:
        if rx.search(low):
            return kid
    return None


def _name_color(low, default):
    for word, c in _COLORS:
        if word in low:
            return c
    return default


def _name_area(low):
    m = _SIZE_RX.search(low)
    if not m:
        return None
    n = float(m.group(1)) * (1024.0 if m.group(2) else 1.0)
    if m.group(2) and n > 8192:
        n /= 1024.0
    return n * UNIT * 0.5 if 16 <= n <= 16384 else None


def _name_mult(low):
    for rx, f in _MULT:
        if re.search(rx, low):
            return f
    return 1.0


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def _kind_changed(self, context):
    apply_kind(context.scene, self)


def _effects_toggled(self, context):
    scene = context.scene
    if self.effects:
        build_effects(scene)
    else:
        clear(scene, effects=True, leaves=False, water=False)


def _leaves_toggled(self, context):
    scene = context.scene
    if self.leaves:
        build_leaves(scene)
    else:
        clear(scene, effects=False, leaves=True, water=False)


def _leaves_changed(self, context):
    o = bpy.data.objects.get("R5_FX_tree_leaves")
    if o is not None:
        _set_inputs(o, {"Count": int(self.leaves_per_tree), "Size": float(self.leaves_size)})
    k = next((k for k in self.kinds if k.cid == "leaves"), None)
    if k is not None:
        apply_kind(context.scene, k)


def _water_toggled(self, context):
    scene = context.scene
    if self.water:
        build_water(scene)
    else:
        clear(scene, effects=False, leaves=False, water=True)


def _water_changed(self, context):
    update_water(context.scene)


class R5_FxKind(bpy.types.PropertyGroup):
    cid: StringProperty()
    label: StringProperty()
    found: IntProperty(default=0)
    enabled: BoolProperty(name="Show", default=True, update=_kind_changed,
                          description="Show the effects of this kind (viewport and render)")
    amount: FloatProperty(name="Amount", default=1.0, min=0.0, soft_max=4.0, update=_kind_changed,
                          description="Number of particles of each effect (x the default)")
    size: FloatProperty(name="Size", default=1.0, min=0.01, soft_max=5.0, update=_kind_changed,
                        description="Size of the particles (x the default)")
    strength: FloatProperty(name="Strength", default=1.0, min=0.0, soft_max=5.0, update=_kind_changed,
                            description="Glow of the glowing effects, opacity of the others (x the default)")
    color: FloatVectorProperty(name="Colour", subtype="COLOR", size=3, min=0.0, max=1.0, default=(1, 1, 1),
                               update=_kind_changed,
                               description="Multiplies the colour of every effect of this kind (white = their own)")


class R5_FxSettings(bpy.types.PropertyGroup):
    effects: BoolProperty(name="Map effects", default=True, update=_effects_toggled,
                          description="Sparks, electricity, steam, fire, fog, dust, snow, insects... where the map "
                                      "places its effects. They move when the timeline plays")
    leaves: BoolProperty(name="Falling leaves", default=False, update=_leaves_toggled,
                         description="Leaves fall from every tree of the map (and the leaf / petal effects of the "
                                     "map). They move when the timeline plays")
    leaves_per_tree: IntProperty(name="Leaves per tree", default=4, min=1, max=50, update=_leaves_changed,
                                 description="Leaves in the air at any time under each tree")
    leaves_size: FloatProperty(name="Leaf size", default=1.0, min=0.1, soft_max=4.0, update=_leaves_changed)
    kinds: CollectionProperty(type=R5_FxKind)
    water: BoolProperty(name="Water plane", default=False, update=_water_toggled,
                        description="A large animated water surface all around the map, at its sea level")
    water_height: FloatProperty(name="Height", default=0.0, subtype="DISTANCE", update=_water_changed,
                                description="Height of the water surface (Detect: the level of the map's own water)")
    water_size: FloatProperty(name="Size", default=40.0, min=1.0, soft_max=200.0, unit="NONE", update=_water_changed,
                              description="Width of the water plane (km)")
    water_color: FloatVectorProperty(name="Colour", subtype="COLOR", size=3, min=0.0, max=1.0,
                                     default=(0.02, 0.055, 0.06), update=_water_changed)
    water_waves: FloatProperty(name="Waves", default=1.0, min=0.0, soft_max=4.0, update=_water_changed,
                               description="Height of the small waves (bump)")


# --------------------------------------------------------------------------
# Data read at import (operators._scan_entities / _store_entities)
# --------------------------------------------------------------------------
def entity_record(kv, origin):
    """[name, x, y, z, pitch, yaw, roll, scale] (game units) of an info_particle_system entity, or None."""
    name = kv.get("effect_name", "")
    if not name or kv.get("in_skybox", "0") == "1" or kv.get("start_active", "1") == "0":
        return None
    try:
        ang = [float(v) for v in kv.get("angles", "0 0 0").split()[:3]] + [0.0, 0.0, 0.0]
        sc = float(kv.get("scale", "1") or 1.0)
    except ValueError:
        ang, sc = [0.0, 0.0, 0.0], 1.0
    return [name, origin[0], origin[1], origin[2], ang[0], ang[1], ang[2], sc if sc > 0 else 1.0]


def store(scene, records, unit, offset, area=None):
    """Effect entities in Blender coordinates, kept in the scene ("apx_fx") for build_effects."""
    out = []
    for name, x, y, z, p, yw, r, sc in records:
        if area is not None:
            cx, cy, ar = area
            if math.hypot(x - cx, y - cy) > ar + 800.0:
                continue
        out.append([name, x * unit - offset.x, y * unit - offset.y, z * unit - offset.z, p, yw, r, sc])
    if out:
        scene["apx_fx"] = json.dumps(out)
    elif "apx_fx" in scene:
        del scene["apx_fx"]
    return len(out)


def records(scene):
    try:
        return json.loads(scene.get("apx_fx", "[]"))
    except ValueError:
        return []


def scan(scene):
    """Fill the kinds list with the effect counts of the map (settings of the kinds already listed are kept)."""
    fx = scene.r5_fx
    counts = {}
    for rec in records(scene):
        k = classify(rec[0])
        if k:
            counts[k] = counts.get(k, 0) + 1
    have = {k.cid: k for k in fx.kinds}
    for kid, label, *_ in KINDS:
        k = have.get(kid)
        if k is None:
            k = fx.kinds.add()
            k.cid, k.label = kid, label
        k.found = counts.get(kid, 0)
    return counts


def unknown_effects(scene):
    out = {}
    for rec in records(scene):
        if classify(rec[0]) is None:
            out[rec[0]] = out.get(rec[0], 0) + 1
    return out


# --------------------------------------------------------------------------
# Geometry Nodes
# --------------------------------------------------------------------------
def _iface(ng, name, kind, default=None, in_out="INPUT"):
    s = ng.interface.new_socket(name, in_out=in_out, socket_type=kind)
    if default is not None and hasattr(s, "default_value"):
        s.default_value = default
    return s


class _B:
    """Small node-building helper."""

    def __init__(self, ng):
        self.ng, self.N, self.L = ng, ng.nodes, ng.links
        self.x = 0

    def node(self, kind, loc=None, **attrs):
        n = self.N.new(kind)
        for k, v in attrs.items():
            setattr(n, k, v)
        if loc is None:
            self.x += 1
            loc = ((self.x % 12) * 180 - 1800, -(self.x // 12) * 220)
        n.location = loc
        return n

    def link(self, a, b):
        if isinstance(a, (int, float)):
            b.default_value = a
        elif isinstance(a, (tuple, list)):
            b.default_value = a
        else:
            self.L.new(a, b)

    def math(self, op, a, b=None, c=None, clamp=False):
        n = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        for i, v in enumerate((a, b, c)):
            if v is not None:
                self.link(v, n.inputs[i])
        return n.outputs[0]

    def vmath(self, op, a, b=None, c=None, scale=None):
        n = self.node("ShaderNodeVectorMath", operation=op)
        for i, v in enumerate((a, b, c)):
            if v is not None:
                self.link(v, n.inputs[i])
        if scale is not None:
            self.link(scale, n.inputs[3])
        out = n.outputs[1] if op in ("LENGTH", "DOT_PRODUCT", "DISTANCE") else n.outputs[0]
        return out

    def xyz(self, x, y, z):
        n = self.node("ShaderNodeCombineXYZ")
        for i, v in enumerate((x, y, z)):
            self.link(v, n.inputs[i])
        return n.outputs[0]

    def sep(self, v):
        n = self.node("ShaderNodeSeparateXYZ")
        self.link(v, n.inputs[0])
        return n.outputs[0], n.outputs[1], n.outputs[2]

    def attr(self, name, dtype):
        n = self.node("GeometryNodeInputNamedAttribute", data_type=dtype)
        n.inputs["Name"].default_value = name
        return n.outputs[0]

    def noise(self, idx, k, seed):
        """Random colour (3 values in 0..1) and value from (index, k, seed)."""
        n = self.node("ShaderNodeTexWhiteNoise", noise_dimensions="3D")
        self.link(self.xyz(idx, k, seed), n.inputs["Vector"])
        return n.outputs["Color"], n.outputs["Value"]


def _quad(b):
    grid = b.node("GeometryNodeMeshGrid")
    grid.inputs["Size X"].default_value = 1.0
    grid.inputs["Size Y"].default_value = 1.0
    grid.inputs["Vertices X"].default_value = 2
    grid.inputs["Vertices Y"].default_value = 2
    st = b.node("GeometryNodeStoreNamedAttribute", data_type="FLOAT_VECTOR", domain="CORNER")
    st.inputs["Name"].default_value = "r5_fx_uv"
    b.link(grid.outputs["Mesh"], st.inputs["Geometry"])
    b.link(grid.outputs["UV Map"], st.inputs["Value"])
    return st.outputs[0]


def _clip_group():
    """Region clip (regions.py): effects outside the POIs shown are removed."""
    from .regions import get_clip_group
    return get_clip_group()


def particles_group(mode):
    """mode SPRITE: quads facing the active camera; TUMBLE: quads turning in every direction."""
    name = f"R5_FX_{mode.title()}"
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.get("apx_ver") == GROUP_VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.nodes.clear()
    ng.interface.clear()
    ng["apx_ver"] = GROUP_VERSION
    _iface(ng, "Geometry", "NodeSocketGeometry")
    _iface(ng, "Material", "NodeSocketMaterial")
    for nm, kind, d in (("Count", "NodeSocketInt", 8), ("Life", "NodeSocketFloat", 2.0),
                        ("Gravity", "NodeSocketFloat", 0.0), ("Spread", "NodeSocketFloat", 0.0),
                        ("Sway", "NodeSocketFloat", 0.0), ("Orbit", "NodeSocketFloat", 0.0),
                        ("Orbit Speed", "NodeSocketFloat", 1.0), ("Blink", "NodeSocketFloat", 0.0),
                        ("Spin", "NodeSocketFloat", 0.0), ("Size", "NodeSocketFloat", 1.0),
                        ("Grow", "NodeSocketFloat", 0.0), ("Steady", "NodeSocketFloat", 0.0),
                        ("Seed", "NodeSocketFloat", 0.0)):
        _iface(ng, nm, kind, d)
    _iface(ng, "Geometry", "NodeSocketGeometry", in_out="OUTPUT")
    b = _B(ng)
    gi = b.node("NodeGroupInput", (-2400, 0))
    go = b.node("NodeGroupOutput", (1600, 0))
    G = gi.outputs
    dup = b.node("GeometryNodeDuplicateElements", domain="POINT")
    b.link(G["Geometry"], dup.inputs["Geometry"])
    b.link(G["Count"], dup.inputs["Amount"])
    idx = b.node("GeometryNodeInputIndex").outputs[0]
    t = b.node("GeometryNodeInputSceneTime").outputs["Seconds"]
    r1, _ = b.noise(idx, 1.37, G["Seed"])
    r2, _ = b.noise(idx, 2.71, G["Seed"])
    r3, _ = b.noise(idx, 4.13, G["Seed"])
    r2x, _r2y, _r2z = b.sep(r2)
    r3x, r3y, r3z = b.sep(r3)
    life = b.math("MULTIPLY", b.math("MULTIPLY", G["Life"], b.attr("r5_life", "FLOAT")),
                  b.math("MULTIPLY_ADD", r3x, 0.5, 0.75))
    phase = b.math("FRACT", b.math("ADD", b.math("DIVIDE", t, life), r3y))
    age = b.math("MULTIPLY", phase, life)
    # spawn point in the emission box, velocity, gravity
    spawn = b.vmath("MULTIPLY", b.vmath("MULTIPLY_ADD", r1, (2.0, 2.0, 2.0), (-1.0, -1.0, -1.0)),
                    b.attr("r5_box", "FLOAT_VECTOR"))
    jitter = b.vmath("SCALE", b.vmath("MULTIPLY_ADD", r2, (2.0, 2.0, 2.0), (-1.0, -1.0, -1.0)), scale=G["Spread"])
    vel = b.vmath("ADD", b.attr("r5_dir", "FLOAT_VECTOR"), jitter)
    fall = b.math("MULTIPLY", b.math("MULTIPLY", G["Gravity"], 0.5), b.math("MULTIPLY", age, age))
    move = b.vmath("ADD", b.vmath("SCALE", vel, scale=age), b.xyz(0.0, 0.0, fall))
    # sway and orbit
    tau = 6.2832
    sx = b.math("SINE", b.math("MULTIPLY_ADD", t, 1.3, b.math("MULTIPLY", r3z, tau)))
    sy = b.math("COSINE", b.math("MULTIPLY_ADD", t, 1.1, b.math("MULTIPLY", r3x, tau)))
    sway = b.vmath("SCALE", b.xyz(sx, sy, 0.0), scale=G["Sway"])
    ang = b.math("MULTIPLY_ADD", t, G["Orbit Speed"], b.math("MULTIPLY", r3z, tau))
    oz = b.math("MULTIPLY", b.math("SINE", b.math("MULTIPLY", ang, 3.0)), 0.3)
    orbit = b.vmath("SCALE", b.xyz(b.math("COSINE", ang), b.math("SINE", ang), oz), scale=G["Orbit"])
    offset = b.vmath("ADD", b.vmath("ADD", spawn, move), b.vmath("ADD", sway, orbit))
    sp = b.node("GeometryNodeSetPosition")
    b.link(dup.outputs["Geometry"], sp.inputs["Geometry"])
    b.link(offset, sp.inputs["Offset"])
    # fade in / out (steady effects: always visible), blink
    fin = b.math("DIVIDE", phase, 0.12, clamp=True)
    fout = b.math("DIVIDE", b.math("SUBTRACT", 1.0, phase), 0.35, clamp=True)
    alpha = b.math("MAXIMUM", b.math("MULTIPLY", fin, fout), G["Steady"])
    st = b.node("GeometryNodeStoreNamedAttribute", data_type="FLOAT", domain="POINT")
    st.inputs["Name"].default_value = "r5_fx_alpha"
    b.link(sp.outputs[0], st.inputs["Geometry"])
    b.link(alpha, st.inputs["Value"])
    on = b.math("LESS_THAN", b.math("FRACT", b.math("MULTIPLY_ADD", t, G["Blink"], r3z)), 0.5)
    noblink = b.math("LESS_THAN", G["Blink"], 0.001)
    blink = b.math("MAXIMUM", on, noblink)
    size = b.math("MULTIPLY", b.attr("r5_size", "FLOAT"), G["Size"])
    size = b.math("MULTIPLY", size, b.math("MULTIPLY_ADD", r2x, 0.8, 0.6))
    size = b.math("MULTIPLY", size, b.math("MAXIMUM", b.math("MULTIPLY_ADD", G["Grow"], phase, 1.0), 0.05))
    size = b.math("MULTIPLY", size, blink)
    # orientation
    if mode == "SPRITE":
        cam = b.node("GeometryNodeInputActiveCamera")
        oi = b.node("GeometryNodeObjectInfo", transform_space="RELATIVE")
        b.link(cam.outputs[0], oi.inputs["Object"])
        pos = b.node("GeometryNodeInputPosition").outputs[0]
        al = b.node("FunctionNodeAlignRotationToVector", axis="Z")
        b.link(b.vmath("SUBTRACT", oi.outputs["Location"], pos), al.inputs["Vector"])
        rot = al.outputs[0]
    else:
        spin = b.vmath("SCALE", r2, scale=b.math("MULTIPLY", G["Spin"], t))
        rot = b.vmath("MULTIPLY_ADD", r1, (tau, tau, tau), spin)
    quad = _quad(b)
    mat = b.node("GeometryNodeSetMaterial")
    b.link(quad, mat.inputs["Geometry"])
    b.link(G["Material"], mat.inputs["Material"])
    iop = b.node("GeometryNodeInstanceOnPoints", (1200, 0))
    b.link(st.outputs[0], iop.inputs["Points"])
    b.link(mat.outputs[0], iop.inputs["Instance"])
    b.link(rot, iop.inputs["Rotation"])
    b.link(size, iop.inputs["Scale"])
    b.link(iop.outputs[0], go.inputs[0])
    return ng


def arcs_group():
    """Electric arcs: jagged lines from each point in random directions, re-drawn many times per second."""
    name = "R5_FX_Arcs"
    ng = bpy.data.node_groups.get(name)
    if ng is not None and ng.get("apx_ver") == GROUP_VERSION:
        return ng
    if ng is None:
        ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.nodes.clear()
    ng.interface.clear()
    ng["apx_ver"] = GROUP_VERSION
    _iface(ng, "Geometry", "NodeSocketGeometry")
    _iface(ng, "Material", "NodeSocketMaterial")
    for nm, kind, d in (("Count", "NodeSocketInt", 3), ("Size", "NodeSocketFloat", 1.0),
                        ("Flicker", "NodeSocketFloat", 12.0), ("On", "NodeSocketFloat", 0.55),
                        ("Thickness", "NodeSocketFloat", 0.012), ("Seed", "NodeSocketFloat", 0.0)):
        _iface(ng, nm, kind, d)
    _iface(ng, "Geometry", "NodeSocketGeometry", in_out="OUTPUT")
    b = _B(ng)
    gi = b.node("NodeGroupInput", (-2400, 0))
    go = b.node("NodeGroupOutput", (1800, 0))
    G = gi.outputs
    dup = b.node("GeometryNodeDuplicateElements", domain="POINT")
    b.link(G["Geometry"], dup.inputs["Geometry"])
    b.link(G["Count"], dup.inputs["Amount"])
    idx = b.node("GeometryNodeInputIndex").outputs[0]
    t = b.node("GeometryNodeInputSceneTime").outputs["Seconds"]
    step = b.math("ADD", b.math("FLOOR", b.math("MULTIPLY", t, G["Flicker"])), G["Seed"])
    r1, _ = b.noise(idx, 1.37, step)
    r2, _ = b.noise(idx, 2.71, step)
    r2x, _y, _z = b.sep(r2)
    dirv = b.vmath("NORMALIZE", b.vmath("MULTIPLY_ADD", r1, (2.0, 2.0, 2.0), (-1.0, -1.0, -1.0)))
    length = b.math("MULTIPLY", b.math("MULTIPLY", b.attr("r5_size", "FLOAT"), G["Size"]),
                    b.math("MULTIPLY_ADD", r2x, 0.6, 0.4))
    al = b.node("FunctionNodeAlignRotationToVector", axis="X")
    b.link(dirv, al.inputs["Vector"])
    line = b.node("GeometryNodeCurvePrimitiveLine")
    line.inputs["Start"].default_value = (0.0, 0.0, 0.0)
    line.inputs["End"].default_value = (1.0, 0.0, 0.0)
    rs = b.node("GeometryNodeResampleCurve")
    b.link(line.outputs[0], rs.inputs["Curve"])
    cnt = rs.inputs.get("Count")
    if cnt is not None:
        cnt.default_value = 10
    iop = b.node("GeometryNodeInstanceOnPoints")
    b.link(dup.outputs["Geometry"], iop.inputs["Points"])
    b.link(rs.outputs[0], iop.inputs["Instance"])
    b.link(al.outputs[0], iop.inputs["Rotation"])
    b.link(length, iop.inputs["Scale"])
    real = b.node("GeometryNodeRealizeInstances")
    b.link(iop.outputs[0], real.inputs[0])
    # jagged: random offsets along each arc, none at its ends
    pidx = b.node("GeometryNodeInputIndex").outputs[0]
    rj, _ = b.noise(pidx, 5.3, step)
    fac = b.node("GeometryNodeSplineParameter").outputs["Factor"]
    env = b.math("SINE", b.math("MULTIPLY", fac, math.pi))
    amp = b.math("MULTIPLY", b.math("MULTIPLY", env, 0.22),
                 b.math("MULTIPLY", b.attr("r5_size", "FLOAT"), G["Size"]))
    jit = b.vmath("SCALE", b.vmath("MULTIPLY_ADD", rj, (2.0, 2.0, 2.0), (-1.0, -1.0, -1.0)), scale=amp)
    sp = b.node("GeometryNodeSetPosition")
    b.link(real.outputs[0], sp.inputs["Geometry"])
    b.link(jit, sp.inputs["Offset"])
    # each arc is shown only part of the time
    cidx = b.node("GeometryNodeInputIndex").outputs[0]
    _c, rv = b.noise(cidx, 7.7, step)
    dele = b.node("GeometryNodeDeleteGeometry", domain="CURVE")
    b.link(sp.outputs[0], dele.inputs["Geometry"])
    b.link(b.math("GREATER_THAN", rv, G["On"]), dele.inputs["Selection"])
    circ = b.node("GeometryNodeCurvePrimitiveCircle", mode="RADIUS")
    circ.inputs["Resolution"].default_value = 4
    b.link(G["Thickness"], circ.inputs["Radius"])
    c2m = b.node("GeometryNodeCurveToMesh")
    b.link(dele.outputs[0], c2m.inputs["Curve"])
    b.link(circ.outputs[0], c2m.inputs["Profile Curve"])
    mat = b.node("GeometryNodeSetMaterial", (1500, 0))
    b.link(c2m.outputs[0], mat.inputs["Geometry"])
    b.link(G["Material"], mat.inputs["Material"])
    b.link(mat.outputs[0], go.inputs[0])
    return ng


def _set_inputs(obj, values):
    from .gn_instancer import mod_set
    mod = obj.modifiers.get("R5_FX")
    if mod is None or mod.node_group is None:
        return
    ids = {it.name: it.identifier for it in mod.node_group.interface.items_tree
           if getattr(it, "item_type", "SOCKET") == "SOCKET" and it.in_out == "INPUT"}
    for k, v in values.items():
        if k in ids:
            mod_set(mod, ids[k], v)


# --------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------
def _render_method(mat, method):
    from .castfile import _set_render_method
    _set_render_method(mat, method)


def fx_material(name, shading, arc=False):
    mat = bpy.data.materials.get(name)
    if mat is not None and mat.get("r5_fx_ver") == GROUP_VERSION:
        return mat
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links
    out = N.new("ShaderNodeOutputMaterial"); out.location = (900, 0)
    col_n = N.new("ShaderNodeRGB"); col_n.name = col_n.label = "R5_Color"; col_n.location = (-600, 300)
    str_n = N.new("ShaderNodeValue"); str_n.name = str_n.label = "R5_Strength"; str_n.location = (-600, -300)
    src = "GEOMETRY" if arc else "INSTANCER"
    tint = N.new("ShaderNodeAttribute"); tint.attribute_type = src; tint.attribute_name = "r5_tint"
    tint.location = (-600, 100)
    mul = N.new("ShaderNodeVectorMath"); mul.operation = "MULTIPLY"; mul.location = (-350, 200)
    L.new(col_n.outputs[0], mul.inputs[0]); L.new(tint.outputs["Vector"], mul.inputs[1])
    color = mul.outputs[0]
    if arc:
        fac = str_n.outputs[0]
    else:
        uv = N.new("ShaderNodeAttribute"); uv.attribute_type = "GEOMETRY"; uv.attribute_name = "r5_fx_uv"
        uv.location = (-1000, -100)
        al = N.new("ShaderNodeAttribute"); al.attribute_type = "INSTANCER"; al.attribute_name = "r5_fx_alpha"
        al.location = (-600, -500)
        c = N.new("ShaderNodeVectorMath"); c.operation = "MULTIPLY_ADD"; c.location = (-800, -100)
        c.inputs[1].default_value = (2.0, 2.0, 0.0); c.inputs[2].default_value = (-1.0, -1.0, 0.0)
        L.new(uv.outputs["Vector"], c.inputs[0])
        if shading == "LEAF":
            sq = N.new("ShaderNodeVectorMath"); sq.operation = "MULTIPLY"; sq.location = (-650, -100)
            sq.inputs[1].default_value = (1.0, 1.9, 0.0)
            L.new(c.outputs[0], sq.inputs[0])
            ln = N.new("ShaderNodeVectorMath"); ln.operation = "LENGTH"; ln.location = (-500, -100)
            L.new(sq.outputs[0], ln.inputs[0])
            mask = N.new("ShaderNodeMath"); mask.operation = "LESS_THAN"; mask.location = (-350, -100)
            L.new(ln.outputs[1], mask.inputs[0]); mask.inputs[1].default_value = 1.0
        else:
            ln = N.new("ShaderNodeVectorMath"); ln.operation = "LENGTH"; ln.location = (-650, -100)
            L.new(c.outputs[0], ln.inputs[0])
            inv = N.new("ShaderNodeMath"); inv.operation = "SUBTRACT"; inv.use_clamp = True; inv.location = (-500, -100)
            inv.inputs[0].default_value = 1.0
            L.new(ln.outputs[1], inv.inputs[1])
            mask = N.new("ShaderNodeMath"); mask.operation = "POWER"; mask.location = (-350, -100)
            L.new(inv.outputs[0], mask.inputs[0]); mask.inputs[1].default_value = 2.0 if shading == "EMIT" else 1.5
        a = N.new("ShaderNodeMath"); a.operation = "MULTIPLY"; a.location = (-200, -200)
        L.new(mask.outputs[0], a.inputs[0]); L.new(al.outputs["Fac"], a.inputs[1])
        f = N.new("ShaderNodeMath"); f.operation = "MULTIPLY"; f.location = (0, -250)
        L.new(a.outputs[0], f.inputs[0]); L.new(str_n.outputs[0], f.inputs[1])
        fac = f.outputs[0]
    tr = N.new("ShaderNodeBsdfTransparent"); tr.location = (400, 200)
    if shading == "EMIT":
        em = N.new("ShaderNodeEmission"); em.location = (400, 0)
        L.new(color, em.inputs["Color"]); L.new(fac, em.inputs["Strength"])
        add = N.new("ShaderNodeAddShader"); add.location = (650, 0)
        L.new(tr.outputs[0], add.inputs[0]); L.new(em.outputs[0], add.inputs[1])
        L.new(add.outputs[0], out.inputs["Surface"])
        _render_method(mat, "BLENDED")
    else:
        bs = N.new("ShaderNodeBsdfPrincipled"); bs.location = (300, -100)
        L.new(color, bs.inputs["Base Color"])
        bs.inputs["Roughness"].default_value = 0.85
        mix = N.new("ShaderNodeMixShader"); mix.location = (650, 0)
        fc = N.new("ShaderNodeMath"); fc.operation = "MINIMUM"; fc.location = (450, 150)
        L.new(fac, fc.inputs[0]); fc.inputs[1].default_value = 1.0
        L.new(fc.outputs[0], mix.inputs["Fac"])
        L.new(tr.outputs[0], mix.inputs[1]); L.new(bs.outputs[0], mix.inputs[2])
        L.new(mix.outputs[0], out.inputs["Surface"])
        _render_method(mat, "DITHERED" if shading == "LEAF" else "BLENDED")
    mat.use_backface_culling = False
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True
    mat["r5_fx_ver"] = GROUP_VERSION
    return mat


def _set_material_values(mat, color, strength):
    if mat is None or not mat.use_nodes:
        return
    n = mat.node_tree.nodes.get("R5_Color")
    if n is not None:
        n.outputs[0].default_value = (*color, 1.0)
    n = mat.node_tree.nodes.get("R5_Strength")
    if n is not None:
        n.outputs[0].default_value = strength


# --------------------------------------------------------------------------
# Objects
# --------------------------------------------------------------------------
def _root(scene):
    col = bpy.data.collections.get(ROOT)
    if col is None:
        col = bpy.data.collections.new(ROOT)
    if col.name not in scene.collection.children:
        scene.collection.children.link(col)
    return col


def _points_object(scene, name, pts, attrs):
    old = bpy.data.objects.get(name)
    if old is not None:
        me_old = old.data
        bpy.data.objects.remove(old, do_unlink=True)
        if me_old is not None and me_old.users == 0:
            bpy.data.meshes.remove(me_old)
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(pts))
    me.vertices.foreach_set("co", np.asarray(pts, np.float32).ravel())
    for an, (kind, vals) in attrs.items():
        a = me.attributes.new(an, kind, "POINT")
        a.data.foreach_set("vector" if kind == "FLOAT_VECTOR" else "value", np.asarray(vals, np.float32).ravel())
    me.update()
    o = bpy.data.objects.new(name, me)
    o["apx_fx"] = True
    for f in ("visible_shadow", "visible_diffuse", "visible_glossy", "visible_transmission",
              "visible_volume_scatter"):
        setattr(o, f, False)          # light effects: seen by the camera only (cheaper, and no dark patches)
    _root(scene).objects.link(o)
    from .regions import get_region, CLIP_MOD
    from .gn_instancer import mod_set
    clip = o.modifiers.new(CLIP_MOD, "NODES")
    clip.node_group = _clip_group()
    ident = next(it.identifier for it in clip.node_group.interface.items_tree
                 if getattr(it, "item_type", "SOCKET") == "SOCKET" and it.in_out == "INPUT" and it.name == "Region")
    mod_set(clip, ident, get_region(scene))
    return o


def _kind_values(k, kd):
    """Modifier inputs of a kind from its defaults and its settings."""
    p = kd[6]
    if kd[3] == "ARC":
        return {"Count": max(0, round(p["count"] * k.amount)), "Size": k.size, "Flicker": p["flicker"],
                "On": p["on"], "Thickness": p["thickness"] * k.size}
    return {"Count": max(0, round(p["count"] * k.amount)), "Life": p.get("life", 2.0),
            "Gravity": p.get("gravity", 0.0), "Spread": p.get("spread", 0.0), "Sway": p.get("sway", 0.0),
            "Orbit": p.get("orbit", 0.0), "Orbit Speed": p.get("orbit_speed", 1.0), "Blink": p.get("blink", 0.0),
            "Spin": p.get("spin", 0.0), "Size": k.size, "Grow": p.get("grow", 0.0), "Steady": p.get("steady", 0.0)}


def _kind_strength(k, kd):
    p = kd[6]
    return (p.get("strength", 1.0) if kd[4] == "EMIT" else p.get("opacity", 1.0)) * k.strength


def apply_kind(scene, k):
    kd = KIND.get(k.cid)
    if kd is None:
        return
    fx = scene.r5_fx
    names = [f"R5_FX_{k.cid}"] + (["R5_FX_tree_leaves"] if k.cid == "leaves" else [])
    for name in names:
        o = bpy.data.objects.get(name)
        if o is None:
            continue
        show = k.enabled and (fx.leaves if k.cid == "leaves" else fx.effects)
        o.hide_viewport = o.hide_render = not show
        vals = _kind_values(k, kd)
        if name == "R5_FX_tree_leaves":
            vals["Count"] = int(fx.leaves_per_tree)
            vals["Size"] = float(fx.leaves_size) * k.size
        _set_inputs(o, vals)
    _set_material_values(bpy.data.materials.get(f"R5_FX_{k.cid}"), tuple(k.color), _kind_strength(k, kd))


def _record_point(rec, kd):
    name, x, y, z, pitch, yaw, roll, sc = rec
    low = name.lower()
    p = kd[6]
    rot = Euler((math.radians(roll), math.radians(pitch), math.radians(yaw)), "XYZ").to_matrix()
    mult = _name_mult(low) * sc
    area = _name_area(low) if p.get("area_names") else None
    half = area if area is not None else p.get("area", 0.0) * mult
    box = (half, half, half * (0.15 if p.get("flat") else 0.4))
    mode = "FWD" if (p.get("dir") == "FWD" or _FWD_RX.search(low)) else p.get("dir", "UP")
    if mode == "FWD":
        d = rot @ Vector((1.0, 0.0, 0.0))
        v = d * max(p.get("speed", 0.0), p.get("rise", 0.0), 1.0)
    elif mode == "WIND":
        v = Vector((0.8, 0.25, 0.0)) * p.get("speed", 0.5) + Vector((0, 0, p.get("rise", 0.0)))
    else:
        v = Vector((0.0, 0.0, p.get("rise", 0.0)))
    size = p.get("size", 0.1) * (mult if area is None or kd[0] in ("fog",) else 1.0)
    if kd[0] == "fog" and area is not None:
        size = max(size, area * 0.6)
    return (x, y, z), box, tuple(v), size, _name_color(low, (1.0, 1.0, 1.0))


def _by_kind(scene):
    by_kind = {}
    for rec in records(scene):
        kid = classify(rec[0])
        if kid:
            by_kind.setdefault(kid, []).append(rec)
    return by_kind


def _add_modifier(o, group, mat, seed):
    from .gn_instancer import mod_set
    mod = o.modifiers.new("R5_FX", "NODES")
    mod.node_group = group
    ident = next(it.identifier for it in group.interface.items_tree
                 if getattr(it, "item_type", "SOCKET") == "SOCKET" and it.in_out == "INPUT" and it.name == "Material")
    mod_set(mod, ident, mat)
    _set_inputs(o, {"Seed": float(seed)})
    return mod


def _build_kind(scene, k, items):
    kd = KIND[k.cid]
    pts, boxes, dirs, sizes, tints = [], [], [], [], []
    for rec in items:
        pos, box, v, size, tint = _record_point(rec, kd)
        pts.append(pos); boxes.append(box); dirs.append(v); sizes.append(size); tints.append(tint)
    attrs = {"r5_box": ("FLOAT_VECTOR", boxes), "r5_dir": ("FLOAT_VECTOR", dirs), "r5_size": ("FLOAT", sizes),
             "r5_tint": ("FLOAT_VECTOR", tints), "r5_life": ("FLOAT", [1.0] * len(pts))}
    o = _points_object(scene, f"R5_FX_{k.cid}", pts, attrs)
    group = arcs_group() if kd[3] == "ARC" else particles_group(kd[3])
    _add_modifier(o, group, fx_material(f"R5_FX_{k.cid}", kd[4], arc=kd[3] == "ARC"), len(k.cid))
    apply_kind(scene, k)
    return o


def build_effects(scene, report=print):
    """(Re)create the effect objects from the effect entities stored at import (the leaf / petal effects are
    part of the falling leaves, build_leaves)."""
    fx = scene.r5_fx
    clear(scene, effects=True, leaves=False, water=False)
    scan(scene)
    by_kind = _by_kind(scene)
    n = 0
    for k in fx.kinds:
        items = by_kind.get(k.cid)
        if k.cid == "leaves" or k.cid not in KIND or not items:
            continue
        _build_kind(scene, k, items)
        n += len(items)
    if n:
        report(f"Effects: {n} placed ({', '.join(f'{len(v)} {k}' for k, v in sorted(by_kind.items()) if k != 'leaves')})")
    return n


# ---- falling leaves from the trees ----
def _foliage_color(key):
    """Mean colour of the leaves of a tree model (its foliage albedo), darkened like lit leaves."""
    from .castfile import _FOLIAGE_RE
    for c in library.get_model_lods(key)[:1]:
        for o in c.all_objects:
            if o.type != "MESH":
                continue
            for m in o.data.materials:
                if m is None or not m.use_nodes or not _FOLIAGE_RE.search(m.name.lower()):
                    continue
                img = next((n.image for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image is not None
                            and n.label in ("albedo", "")), None)
                if img is None:
                    continue
                if "r5_mean" not in img:
                    try:
                        w, h = img.size
                        px = np.empty(w * h * 4, np.float32)
                        img.pixels.foreach_get(px)
                        px = px.reshape(-1, 4)[:: max(1, (w * h) // 40000)]
                        keep = px[px[:, 3] > 0.5] if (px[:, 3] > 0.5).any() else px
                        img["r5_mean"] = [float(v) for v in keep[:, :3].mean(0)]
                    except (RuntimeError, ValueError):
                        img["r5_mean"] = [0.3, 0.35, 0.12]
                return tuple(img["r5_mean"])
    return (0.3, 0.33, 0.12)


def tree_points(scene):
    """Canopies of every placed tree: (centre, half box, fall height) in Blender units."""
    pts, boxes, lives, tints = [], [], [], []
    for o in scene.objects:
        key = o.get("apx_gn")
        if not key or o.get("apx_sky") or o.type != "MESH":
            continue
        low = key.lower()
        if not TREE_RX.search(low) or NOT_TREE_RX.search(low):
            continue
        b = library.model_bounds(key)
        if b is None:
            continue
        lo, hi = b
        h = float(hi[2] - lo[2])
        if h < 2.0:
            continue
        me = o.data
        n = len(me.vertices)
        if not n:
            continue
        co = np.empty(n * 3, np.float32)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3) @ np.array(o.matrix_world.to_3x3()).T + np.array(o.matrix_world.translation)
        sc = np.ones(n, np.float32)
        a = me.attributes.get("apx_scale")
        if a is not None:
            a.data.foreach_get("value", sc)
        tint = _foliage_color(key)
        rx, ry = float(hi[0] - lo[0]) * 0.3, float(hi[1] - lo[1]) * 0.3
        for p, s in zip(co, sc):
            cz = (float(lo[2]) + 0.62 * h) * s
            pts.append((float(p[0]), float(p[1]), float(p[2]) + cz))
            boxes.append((rx * s, ry * s, 0.15 * h * s))
            lives.append(max(cz, 1.0))
            tints.append(tint)
    return pts, boxes, lives, tints


def build_leaves(scene, report=print):
    fx = scene.r5_fx
    clear(scene, effects=False, leaves=True, water=False)
    scan(scene)
    k = next(k for k in fx.kinds if k.cid == "leaves")
    leaf_fx = _by_kind(scene).get("leaves")
    if leaf_fx:
        _build_kind(scene, k, leaf_fx)                   # the leaf / petal effects of the map
    pts, boxes, heights, tints = tree_points(scene)
    if not pts:
        report(f"Falling leaves: {len(leaf_fx or ())} leaf effects, no tree found in the map")
        return 0
    kd = KIND["leaves"]
    fall = -kd[6]["rise"]
    life = kd[6]["life"]
    attrs = {"r5_box": ("FLOAT_VECTOR", boxes),
             "r5_dir": ("FLOAT_VECTOR", [(0.0, 0.0, -fall)] * len(pts)),
             "r5_size": ("FLOAT", [kd[6]["size"]] * len(pts)),
             "r5_tint": ("FLOAT_VECTOR", tints),
             # a leaf lives as long as it takes to reach the ground
             "r5_life": ("FLOAT", [h / fall / life for h in heights])}
    o = _points_object(scene, "R5_FX_tree_leaves", pts, attrs)
    _add_modifier(o, particles_group("TUMBLE"), fx_material("R5_FX_leaves", "LEAF"), 3)
    apply_kind(scene, k)
    report(f"Falling leaves: {len(pts)} trees, {len(pts) * fx.leaves_per_tree} leaves in the air"
           f"{f', {len(leaf_fx)} leaf effects' if leaf_fx else ''}")
    return len(pts)


# ---- water plane ----
WATER = "R5_Water_Plane"


def detect_water_level(scene):
    """Height of the map's own water: the largest flat surface using a water material, else None."""
    bins = {}
    for o in scene.objects:
        # map geometry only: the prop instancers are point clouds (their slots only show the model's materials)
        if o.type != "MESH" or o.get("apx_gn") or o.get("apx_fx") or o.name == WATER:
            continue
        me = o.data
        water = [i for i, m in enumerate(me.materials) if m is not None and m.get("r5_water")]
        n = len(me.polygons)
        if not water or not n:
            continue
        mi = np.empty(n, np.int32); me.polygons.foreach_get("material_index", mi)
        ar = np.empty(n, np.float32); me.polygons.foreach_get("area", ar)
        ce = np.empty(n * 3, np.float32); me.polygons.foreach_get("center", ce)
        no = np.empty(n * 3, np.float32); me.polygons.foreach_get("normal", no)
        mw = np.array(o.matrix_world)
        ce = ce.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
        nz = (no.reshape(-1, 3) @ mw[:3, :3].T)[:, 2]
        sel = np.isin(mi, water) & (np.abs(nz) > 0.95 * np.linalg.norm(mw[:3, :3], axis=0).max())
        for z, a in zip(np.round(ce[sel, 2] * 2.0) / 2.0, ar[sel]):
            bins[float(z)] = bins.get(float(z), 0.0) + float(a)
    if not bins:
        return None
    z, area = max(bins.items(), key=lambda kv: kv[1])          # the largest flat water surface
    return z if area > 50.0 else None


def _map_center(scene):
    lo, hi = None, None
    for c in scene.collection.children:
        if not c.name.startswith(("APX_World_", "APX_Map_")):
            continue
        for o in c.all_objects:
            if o.type != "MESH":
                continue
            bb = np.array([o.matrix_world @ Vector(v) for v in o.bound_box])
            lo = bb.min(0) if lo is None else np.minimum(lo, bb.min(0))
            hi = bb.max(0) if hi is None else np.maximum(hi, bb.max(0))
    if lo is None:
        return 0.0, 0.0
    return float(lo[0] + hi[0]) / 2, float(lo[1] + hi[1]) / 2


WATER_MAT_VERSION = 3
# Wave layers (metres): long swell stretched along its crests, chop, ripples. Each drifts with the time.
_WAVES = ((60.0, 150.0, 0.6, 0.05, 2.0), (12.0, 18.0, 0.2, 0.22, 3.0), (2.2, 2.6, 0.04, 0.7, 2.0))


def water_material():
    """Deep water: dark glossy surface whose waves are a bump of three layers of 4D noise (metres, world
    position), animated by the timeline. Seen from the map, the swell shows in the reflections of the sky."""
    name = "R5_Water"
    mat = bpy.data.materials.get(name)
    if mat is not None and mat.get("r5_water_ver") == WATER_MAT_VERSION and mat.node_tree:
        return mat
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    if nt.animation_data:
        nt.animation_data_clear()
    nt.nodes.clear()
    N, L = nt.nodes, nt.links
    out = N.new("ShaderNodeOutputMaterial"); out.location = (900, 0)
    bs = N.new("ShaderNodeBsdfPrincipled"); bs.location = (500, 0)
    bs.inputs["Roughness"].default_value = 0.035
    if "IOR" in bs.inputs:
        bs.inputs["IOR"].default_value = 1.33
    col = N.new("ShaderNodeRGB"); col.name = col.label = "R5_Color"; col.location = (-200, 450)
    geo = N.new("ShaderNodeNewGeometry"); geo.location = (-1400, 0)
    t = N.new("ShaderNodeValue"); t.name = t.label = "R5_Water_Time"; t.location = (-1400, -400)
    total = None
    for i, (sx, sy, amp, speed, detail) in enumerate(_WAVES):
        y = 300 - 330 * i
        sc = N.new("ShaderNodeVectorMath"); sc.operation = "MULTIPLY"; sc.location = (-1100, y)
        sc.inputs[1].default_value = (1.0 / sx, 1.0 / sy, 1.0 / max(sx, sy))
        L.new(geo.outputs["Position"], sc.inputs[0])
        rot = N.new("ShaderNodeVectorRotate"); rot.location = (-900, y)     # each layer goes its own way
        rot.rotation_type = "Z_AXIS"
        rot.inputs["Angle"].default_value = (0.35, 1.2, 2.3)[i]
        L.new(sc.outputs[0], rot.inputs["Vector"])
        nz = N.new("ShaderNodeTexNoise"); nz.location = (-700, y); nz.name = f"R5_Waves_{i}"
        nz.noise_dimensions = "4D"
        nz.inputs["Scale"].default_value = 1.0
        nz.inputs["Detail"].default_value = detail
        nz.inputs["Roughness"].default_value = 0.5
        L.new(rot.outputs[0], nz.inputs["Vector"])
        w = N.new("ShaderNodeMath"); w.operation = "MULTIPLY"; w.location = (-900, y - 160)
        w.inputs[1].default_value = speed
        L.new(t.outputs[0], w.inputs[0])
        L.new(w.outputs[0], nz.inputs["W"])
        h = N.new("ShaderNodeMath"); h.operation = "MULTIPLY_ADD"; h.location = (-500, y)
        h.inputs[1].default_value = 2.0 * amp
        h.inputs[2].default_value = -amp
        L.new(nz.outputs["Fac"], h.inputs[0])
        if total is None:
            total = h.outputs[0]
        else:
            add = N.new("ShaderNodeMath"); add.operation = "ADD"; add.location = (-300, y)
            L.new(total, add.inputs[0]); L.new(h.outputs[0], add.inputs[1])
            total = add.outputs[0]
    # troughs darker, crests lighter (light through the thin crests): the waves show even under a flat sky
    mr = N.new("ShaderNodeMapRange"); mr.location = (0, 450); mr.name = "R5_Crest"
    mr.inputs["From Min"].default_value = -0.5
    mr.inputs["From Max"].default_value = 0.5
    L.new(total, mr.inputs["Value"])
    dark = N.new("ShaderNodeVectorMath"); dark.operation = "SCALE"; dark.location = (0, 650)
    dark.inputs["Scale"].default_value = 0.55
    light = N.new("ShaderNodeVectorMath"); light.operation = "SCALE"; light.location = (0, 800)
    light.inputs["Scale"].default_value = 2.2
    L.new(col.outputs[0], dark.inputs[0]); L.new(col.outputs[0], light.inputs[0])
    mix = N.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.location = (250, 500)
    L.new(mr.outputs["Result"], mix.inputs[0])
    L.new(dark.outputs[0], [i for i in mix.inputs if i.identifier == "A_Color"][0])
    L.new(light.outputs[0], [i for i in mix.inputs if i.identifier == "B_Color"][0])
    L.new([o for o in mix.outputs if o.identifier == "Result_Color"][0], bs.inputs["Base Color"])
    bump = N.new("ShaderNodeBump"); bump.location = (200, -250); bump.name = "R5_Bump"
    bump.inputs["Strength"].default_value = 1.0
    bump.inputs["Distance"].default_value = 1.0
    L.new(total, bump.inputs["Height"])
    L.new(bump.outputs[0], bs.inputs["Normal"])
    L.new(bs.outputs[0], out.inputs["Surface"])
    mat["r5_water_ver"] = WATER_MAT_VERSION
    return mat


def _drive_waves(mat, scene):
    from .motion import _drive
    t = mat.node_tree.nodes.get("R5_Water_Time")
    ad = mat.node_tree.animation_data
    if t is not None and not (ad and ad.drivers):
        _drive(t.outputs[0], scene, "frame / fps", [("fps", "render.fps")])


def build_water(scene, report=print):
    fx = scene.r5_fx
    o = bpy.data.objects.get(WATER)
    if o is None:
        me = bpy.data.meshes.new(WATER)
        o = bpy.data.objects.new(WATER, me)
        _root(scene).objects.link(o)
        if not o.get("r5_height_set"):
            lvl = detect_water_level(scene)
            if lvl is not None:
                fx["water_height"] = lvl        # no update callback loop: the object is placed below
                report(f"Water plane at the map's water level: {lvl:.1f} m")
            o["r5_height_set"] = True
        cx, cy = _map_center(scene)
        o["r5_center"] = (cx, cy)
    update_water(scene)
    return o


def update_water(scene):
    fx = scene.r5_fx
    o = bpy.data.objects.get(WATER)
    if o is None:
        return
    cx, cy = o.get("r5_center", (0.0, 0.0))
    o.location = (cx, cy, fx.water_height)
    # real size in the mesh (object scale 1): the bump of the waves is in metres, a 40 km object scale would
    # flatten it
    o.scale = (1.0, 1.0, 1.0)
    h = fx.water_size * 500.0
    me = o.data
    if tuple(round(v.co.x, 2) for v in me.vertices[:1]) != (round(-h, 2),) or len(me.vertices) != 4:
        me.clear_geometry()
        me.from_pydata([(-h, -h, 0), (h, -h, 0), (h, h, 0), (-h, h, 0)], [], [(0, 1, 2, 3)])
        uv = me.uv_layers.new(name="UVMap")
        for i, c in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
            uv.data[i].uv = c
        me.update()
    mat = water_material()              # (a plane of 0.20.0: material with the new waves)
    if not me.materials:
        me.materials.append(mat)
    elif me.materials[0] != mat:
        me.materials[0] = mat
    _drive_waves(mat, scene)
    if mat.use_nodes:
        n = mat.node_tree.nodes.get("R5_Color")
        if n is not None:
            n.outputs[0].default_value = (*fx.water_color, 1.0)
        b = mat.node_tree.nodes.get("R5_Bump")
        if b is not None:
            b.inputs["Distance"].default_value = fx.water_waves


# ---- removal ----
def clear(scene, effects=True, leaves=True, water=True):
    for o in list(bpy.data.objects):
        n = o.name
        leaf = n in ("R5_FX_tree_leaves", "R5_FX_leaves")
        if (effects and n.startswith("R5_FX_") and not leaf) or (leaves and leaf) or (water and n == WATER):
            me = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
    col = bpy.data.collections.get(ROOT)
    if col is not None and not col.objects and not col.children:
        bpy.data.collections.remove(col)


def after_import(scene, report=print):
    """Called at the end of a map import: effects and the options already switched on."""
    fx = scene.r5_fx
    scan(scene)
    if fx.effects:
        build_effects(scene, report)
    if fx.leaves:
        build_leaves(scene, report)
    if fx.water and bpy.data.objects.get(WATER) is None:
        build_water(scene, report)


# --------------------------------------------------------------------------
# Operators / panel
# --------------------------------------------------------------------------
class R5_OT_fx_rebuild(bpy.types.Operator):
    """Create the effects again (after a new import, or to apply the default look again)"""
    bl_idname = "r5.fx_rebuild"
    bl_label = "Rebuild effects"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        fx = context.scene.r5_fx
        rep = lambda m: self.report({"INFO"}, m)  # noqa: E731
        if fx.effects:
            build_effects(context.scene, rep)
        if fx.leaves:
            build_leaves(context.scene, rep)
        return {"FINISHED"}


class R5_OT_water_detect(bpy.types.Operator):
    """Put the water plane at the level of the map's own water (sea, lake, river)"""
    bl_idname = "r5.water_detect"
    bl_label = "Detect"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        lvl = detect_water_level(context.scene)
        if lvl is None:
            self.report({"WARNING"}, "No flat water surface in the map geometry")
            return {"CANCELLED"}
        context.scene.r5_fx.water_height = lvl
        self.report({"INFO"}, f"Water level: {lvl:.1f} m")
        return {"FINISHED"}


class R5_PT_effects(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "R5 Map Loader"
    bl_label = "Effects & water"
    bl_idname = "R5_PT_effects"
    bl_order = 6                 # below City lights, above Animation (ui.py)
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .ui import SIDEBAR
        from . import progress
        return SIDEBAR[0] and not progress.active() and hasattr(context.scene, "r5_fx")

    def draw_header(self, context):
        self.layout.label(icon="PARTICLES")

    def draw(self, context):
        fx = context.scene.r5_fx
        col = self.layout.column()
        n = len(records(context.scene))
        row = col.row(align=True)
        row.prop(fx, "effects", toggle=False)
        row.label(text=f"{n} in the map" if n else "none loaded")
        row.operator("r5.fx_rebuild", text="", icon="FILE_REFRESH")
        col.prop(fx, "leaves")
        if fx.leaves:
            row = col.row(align=True)
            row.prop(fx, "leaves_per_tree")
            row.prop(fx, "leaves_size")
        col.separator()
        col.prop(fx, "water")
        if fx.water:
            row = col.row(align=True)
            row.prop(fx, "water_height")
            row.operator("r5.water_detect", text="", icon="VIEWZOOM")
            row = col.row(align=True)
            row.prop(fx, "water_size", text="Size (km)")
            row.prop(fx, "water_color", text="")
            col.prop(fx, "water_waves")
        col.label(text="Effects move when the timeline plays", icon="INFO")


class R5_PT_effects_kinds(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "R5 Map Loader"
    bl_label = "Effect types"
    bl_idname = "R5_PT_effects_kinds"
    bl_parent_id = "R5_PT_effects"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return hasattr(context.scene, "r5_fx") and len(context.scene.r5_fx.kinds) > 0

    def draw(self, context):
        col = self.layout.column()
        for k in context.scene.r5_fx.kinds:
            if not k.found and k.cid != "leaves":
                continue
            box = col.box()
            row = box.row(align=True)
            row.prop(k, "enabled", text="", icon="HIDE_OFF" if k.enabled else "HIDE_ON", emboss=False)
            row.label(text=f"{k.label}: {k.found}")
            row.prop(k, "color", text="")
            if k.enabled:
                row = box.row(align=True)
                row.prop(k, "amount")
                row.prop(k, "size")
                row.prop(k, "strength")
        unk = unknown_effects(context.scene)
        if unk:
            col.label(text=f"Not drawn: {sum(unk.values())} effects of {len(unk)} other names", icon="INFO")


CLASSES = (R5_FxKind, R5_FxSettings, R5_OT_fx_rebuild, R5_OT_water_detect, R5_PT_effects, R5_PT_effects_kinds)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.r5_fx = PointerProperty(type=R5_FxSettings)


def unregister():
    if hasattr(bpy.types.Scene, "r5_fx"):
        del bpy.types.Scene.r5_fx
    for c in reversed(CLASSES):
        if getattr(c, "is_registered", False):
            bpy.utils.unregister_class(c)
