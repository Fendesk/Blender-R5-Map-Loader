"""City lights: real lamps for the light sources of the map (street lamps, signs, floodlights...).

The props only carry an emissive texture: it glows but lights nothing around it. "Find light sources" sorts the
placed models into types (by model name, plus every model with an emissive material), then "Create lights"
puts a point light at the emissive part of each placed prop. Every type has its own settings, applied live:
on/off, power, colour (or each model's emissive colour), radius, shadows, and the glow of the emissive
textures themselves.
"""

import math
import re

import bpy
import numpy as np
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from mathutils import Euler, Matrix, Vector

from . import library

ROOT = "R5_City_Lights"

# (id, label, model-name regex, default power W, default colour, default radius m, default light type)
CATEGORIES = (
    ("street", "Street lamps", r"light_?pole|lightpole|lamp_?post|lamppost|street_?(light|lamp)|parking_?(post|light)|"
                               r"light_parking|(^|_)city_light|walkway_light|traffic_light|road_?light|(^|_)light_fc$",
     150.0, (1.0, 0.82, 0.6), 0.3, "POINT"),
    ("flood", "Floodlights & work lights", r"flood|halogen|search_?light|light_tower|tower_light|construction_light|"
                                           r"work(ing)?_(floor_)?light|spot_?light|stadium",
     200.0, (1.0, 0.95, 0.85), 0.4, "POINT"),
    ("sign", "Signs & screens", r"sign|neon|screen|billboard|monitor|display|holo|tv_led|(^|_)led(_|$)|marquee|"
                                r"scrolling|_lit(_|$)|advert|vending|flexscreen",
     50.0, (1.0, 1.0, 1.0), 0.2, "POINT"),
    ("interior", "Interior & wall lights", r"ceiling_?light|light_ceiling|desk_lamp|office_lamp|flou?res+en|"
                                           r"light_?strip|string_lights|tube_light|light_?panel|chandelier|pendant|"
                                           r"bulb|sconce|wall_light|light_wall|interior_light|lobby_light|"
                                           r"cafeteria_light|buffet_light|floor_?light|ambient_light|stasis_light|"
                                           r"relic_light|lootbin_light|podium_lights|_lights?(_|$)|warning_light|"
                                           r"exterior_light|rockcluster_light|lantern",
     250.0, (1.0, 0.9, 0.75), 0.15, "POINT"),
    ("other", "Other glowing props", r"$^", 300.0, (1.0, 0.9, 0.8), 0.2, "POINT"),
)
_RX = {c[0]: re.compile(c[2]) for c in CATEGORIES}
# settings kept when the light types are scanned again
_KEEP = ("enabled", "power", "color", "use_model_color", "radius", "shadows", "emission", "max_lights",
         "light_type", "spot_angle", "spot_blend", "area_shape")
# light sources that can stand without a glowing texture (the light goes at the top of the model)
_NO_EMISSIVE_OK = {"street", "flood"}
_EXCLUDE = re.compile(r"godray|god_ray|lightshaft|light_shaft|atmosphere|volumetric|light_beam|sunbeam|"
                      r"light_cone|lightcone|dust_motes|switch|lightning|_off(_|$)|no_lights?|(^|_)off_")


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def _cat_changed(self, context):
    apply_category(context.scene, self)


def _cat_rebuild(self, context):
    # colour mode change: the light datablocks are recoloured, the objects stay
    apply_category(context.scene, self)


class R5_LightCat(bpy.types.PropertyGroup):
    cid: StringProperty()
    label: StringProperty()
    models: IntProperty(default=0)
    props: IntProperty(default=0)
    created: IntProperty(default=0)
    enabled: BoolProperty(name="Lights", default=True, update=_cat_changed,
                          description="Show the lights of this type (viewport and render)")
    power: FloatProperty(name="Power", default=500.0, min=0.0, soft_max=20000.0, unit="POWER",
                         update=_cat_changed)
    color: FloatVectorProperty(name="Colour", subtype="COLOR", size=3, min=0.0, max=1.0, default=(1, 0.9, 0.8),
                               update=_cat_changed)
    use_model_color: BoolProperty(name="Colour of each model", default=True, update=_cat_rebuild,
                                  description="Tint each light with the average colour of its prop's emissive "
                                              "texture (otherwise the colour above)")
    light_type: EnumProperty(
        name="Type", update=_cat_changed,
        items=[("POINT", "Point", "Shines in every direction", "LIGHT_POINT", 0),
               ("SPOT", "Spot", "Cone of light, pointing out of the glowing part (down for street lamps)",
                "LIGHT_SPOT", 1),
               ("AREA", "Area", "Flat panel facing out of the glowing part: soft light (signs, panels, tubes)",
                "LIGHT_AREA", 2)],
        default="POINT")
    spot_angle: FloatProperty(name="Angle", default=110.0, min=1.0, max=180.0, update=_cat_changed,
                              description="Spot: opening of the cone. Area: spread of the light (180 = all)")
    spot_blend: FloatProperty(name="Blend", default=0.4, min=0.0, max=1.0, subtype="FACTOR", update=_cat_changed,
                              description="Spot: softness of the edge of the cone")
    area_shape: EnumProperty(name="Shape", update=_cat_changed, default="DISK",
                             items=[("SQUARE", "Square", ""), ("DISK", "Disk", ""), ("RECTANGLE", "Rectangle", ""),
                                    ("ELLIPSE", "Ellipse", "")])
    radius: FloatProperty(name="Radius", default=0.2, min=0.0, soft_max=5.0, unit="LENGTH", update=_cat_changed,
                          description="Size of the light: bigger = softer shadows and highlights (area: half its "
                                      "width)")
    shadows: BoolProperty(name="Shadows", default=False, update=_cat_changed,
                          description="Cast shadows. Off by default: the light sits inside the lamp head, whose "
                                      "own mesh would otherwise block it (and it is much faster with many lights)")
    emission: FloatProperty(name="Glow", default=1.0, min=0.0, soft_max=50.0, update=_cat_changed,
                            description="Strength of the emissive textures of these props (the glowing parts "
                                        "themselves)")
    max_lights: IntProperty(name="Max lights", default=3000, min=0, soft_max=20000,
                            description="At most this many lights of this type (the props closest to the 3D cursor "
                                        "first). Used by Create lights")


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------
def _is_emissive(mat):
    if mat is None or not mat.use_nodes or mat.get("r5_additive"):
        return False
    return any(n.type == "TEX_IMAGE" and n.label == "emissive" and n.image and n.outputs[0].is_linked
               for n in mat.node_tree.nodes)


def _emissive_color(mat):
    node = next((n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.label == "emissive" and n.image), None)
    if node is None:
        return None
    key = "r5_em_rgb"
    if key in node.image:
        return tuple(node.image[key])
    rgb = None
    small = None
    try:
        small = node.image.copy()
        small.scale(16, 16)
        px = np.empty(16 * 16 * 4, np.float32)
        small.pixels.foreach_get(px)
        p = px.reshape(-1, 4)[:, :3]
        bright = p[p.max(1) > 0.25]                 # the lit pixels only
        if len(bright):
            c = bright.mean(0)
            rgb = tuple(float(v) / max(float(c.max()), 1e-6) for v in c)
    except (RuntimeError, ValueError):
        rgb = None
    finally:
        if small is not None:
            bpy.data.images.remove(small)
    if rgb:
        node.image[key] = rgb
    return rgb


def _model_lod0_objects(key):
    lods = library.get_model_lods(key)
    if not lods:
        return []
    return [o for o in lods[0].all_objects if o.type == "MESH"]


_EM_RES = 256          # the emissive texture is sampled at this size to find the glowing faces
MAX_PER_MODEL = 12     # lights per prop at most (the biggest glowing parts)


def _emissive_pixels(mat):
    """Luminance of the emissive texture, (H, W) floats, cached per image."""
    node = next((n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE" and n.label == "emissive" and n.image), None)
    if node is None:
        return None
    cache = _emissive_pixels.cache
    key = node.image.name
    if key in cache:
        return cache[key]
    lum = None
    small = None
    try:
        small = node.image.copy()
        small.scale(_EM_RES, _EM_RES)
        px = np.empty(_EM_RES * _EM_RES * 4, np.float32)
        small.pixels.foreach_get(px)
        px = px.reshape(_EM_RES, _EM_RES, 4)
        lum = px[..., :3].max(2)
    except (RuntimeError, ValueError):
        lum = None
    finally:
        if small is not None:
            bpy.data.images.remove(small)
    cache[key] = lum
    return lum


_emissive_pixels.cache = {}


def _clusters(points, weights, cell):
    """Group points closer than ~cell (grid cells that touch): list of index arrays."""
    keys = np.floor(points / cell).astype(np.int64)
    cells = {}
    for i, k in enumerate(map(tuple, keys)):
        cells.setdefault(k, []).append(i)
    seen, out = set(), []
    for start in cells:
        if start in seen:
            continue
        stack, members = [start], []
        seen.add(start)
        while stack:
            c = stack.pop()
            members += cells[c]
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        nb = (c[0] + dx, c[1] + dy, c[2] + dz)
                        if nb in cells and nb not in seen:
                            seen.add(nb)
                            stack.append(nb)
        out.append(np.array(members))
    return out


def _light_points(key):
    """Where a model glows: [(model-space point, direction)], its emissive materials and an emissive colour.

    Only the faces whose emissive texture is actually bright count (one material often covers the whole lamp:
    the glowing parts are a region of its texture). They are grouped into separate parts (the four heads of a
    parking lamp...), one light each, pointing along the part's normal. Lamp-like models without an emissive
    texture get one light near the top, pointing down."""
    objs = _model_lod0_objects(key)
    parts, mats, cols = [], [], []           # parts: (area, point, normal)
    for o in objs:
        me = o.data
        em_slots = {i for i, ms in enumerate(o.material_slots) if _is_emissive(ms.material)}
        if not em_slots or not me.polygons:
            continue
        n = len(me.polygons)
        mi = np.empty(n, np.int32)
        me.polygons.foreach_get("material_index", mi)
        cen = np.empty(n * 3, np.float32)
        me.polygons.foreach_get("center", cen)
        cen = cen.reshape(-1, 3)
        nrm = np.empty(n * 3, np.float32)
        me.polygons.foreach_get("normal", nrm)
        nrm = nrm.reshape(-1, 3)
        area = np.empty(n, np.float32)
        me.polygons.foreach_get("area", area)
        # UV centre of every face
        uvl = me.uv_layers.active
        fuv = None
        if uvl is not None and len(uvl.data) == len(me.loops):
            luv = np.empty(len(me.loops) * 2, np.float32)
            uvl.data.foreach_get("uv", luv)
            luv = luv.reshape(-1, 2)
            ls = np.empty(n, np.int32)
            lt = np.empty(n, np.int32)
            me.polygons.foreach_get("loop_start", ls)
            me.polygons.foreach_get("loop_total", lt)
            fuv = np.add.reduceat(luv, ls, axis=0) / lt[:, None]
        # vertex colours: "_vc" lamps, neon tubes and holograms are white textures tinted per vertex in game
        fcol = None
        ca = me.color_attributes.get("Col")
        if ca is not None and ca.domain == "CORNER" and len(ca.data) == len(me.loops):
            lc = np.empty(len(me.loops) * 4, np.float32)
            ca.data.foreach_get("color", lc)
            lc = lc.reshape(-1, 4)[:, :3]
            ls0 = np.empty(n, np.int32)
            lt0 = np.empty(n, np.int32)
            me.polygons.foreach_get("loop_start", ls0)
            me.polygons.foreach_get("loop_total", lt0)
            fcol = np.add.reduceat(lc, ls0, axis=0) / lt0[:, None]
        mw = o.matrix_world
        rot = mw.to_3x3()
        for si in em_slots:
            sel = mi == si
            if not sel.any():
                continue
            mat = o.material_slots[si].material
            lum = _emissive_pixels(mat)
            if lum is not None and fuv is not None:
                u = (fuv[sel, 0] % 1.0) * (_EM_RES - 1)
                v = (fuv[sel, 1] % 1.0) * (_EM_RES - 1)
                val = lum[v.astype(np.int64), u.astype(np.int64)]
                thr = max(0.12, 0.35 * float(np.percentile(val, 99)) if len(val) else 0.12)
                glow = np.zeros(n, bool)
                glow[np.where(sel)[0][val > thr]] = True
            else:
                glow = sel
            if not glow.any() or area[glow].sum() <= 0:
                continue
            idx = np.where(glow)[0]
            size = float(np.ptp(cen[idx], axis=0).max()) if len(idx) > 1 else 0.0
            cell = max(0.15, min(0.6, size / 8.0 if size else 0.3))
            tex_col = np.array(_emissive_color(mat) or (1.0, 1.0, 1.0))
            tint = fcol is not None and (mat.get("r5_vctint") or mat.get("r5_glow"))
            for grp in _clusters(cen[idx], area[idx], cell):
                g = idx[grp]
                a = area[g]
                if a.sum() <= 0:
                    continue
                p = (cen[g] * a[:, None]).sum(0) / a.sum()
                d = (nrm[g] * a[:, None]).sum(0)
                rgb = tex_col
                if tint:
                    rgb = rgb * ((fcol[g] * a[:, None]).sum(0) / a.sum())
                m = float(rgb.max())
                rgb = tuple(float(v) / m for v in rgb) if m > 1e-4 else (1.0, 1.0, 1.0)
                parts.append((float(a.sum()), mw @ Vector(p), (rot @ Vector(d)).normalized() if np.linalg.norm(d) > 1e-6
                              else Vector((0, 0, -1)), rgb))
            mats.append(mat)
            cols.append(tuple(tex_col))
    pts = []
    if parts:
        parts.sort(key=lambda t: -t[0])
        biggest = parts[0][0]
        # tiny glowing specks (indicator LEDs, texture noise) do not get a light of their own
        pts = [(p, d, rgb) for a, p, d, rgb in parts if a >= biggest * 0.05][:MAX_PER_MODEL]
    elif objs:
        # lamp model without an emissive texture: near the top of its bounds, pointing down
        corners = [o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
        if corners:
            top = max(v.z for v in corners)
            cx = sum(v.x for v in corners) / len(corners)
            cy = sum(v.y for v in corners) / len(corners)
            pts.append((Vector((cx, cy, top - 0.15)), Vector((0, 0, -1)), None))
    color = tuple(np.mean(np.array(cols), 0)) if cols else None
    return pts, mats, color


def _instances():
    """{model key: [4x4 world matrices]} for GN instancers and instance empties."""
    out = {}
    for o in bpy.data.objects:
        if o.get("apx_sky"):
            continue                    # 3D skybox: scenery tens of km away
        key = o.get("apx_gn")
        if key and o.type == "MESH":
            me = o.data
            n = len(me.vertices)
            if not n:
                continue
            co = np.empty(n * 3, np.float32)
            me.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3)
            ra = me.attributes.get("apx_rot")
            sa = me.attributes.get("apx_scale")
            rot = np.zeros((n, 3), np.float32)
            scl = np.ones(n, np.float32)
            if ra is not None:
                tmp = np.empty(n * 3, np.float32)
                ra.data.foreach_get("vector", tmp)
                rot = tmp.reshape(-1, 3)
            if sa is not None:
                sa.data.foreach_get("value", scl)
            mw = o.matrix_world
            lst = out.setdefault(key, [])
            for i in range(n):
                lst.append(mw @ Matrix.LocRotScale(Vector(co[i]), Euler(rot[i], "XYZ"), Vector((scl[i],) * 3)))
            continue
        key = o.get("apx_model")
        if key and o.type == "EMPTY":
            out.setdefault(key, []).append(o.matrix_world.copy())
    return out


def categorize(key, has_emissive):
    key = key.lower()
    if _EXCLUDE.search(key):
        return None
    for cid, _label, _rx, *_ in CATEGORIES:
        if cid != "other" and _RX[cid].search(key):
            return cid if (has_emissive or cid in _NO_EMISSIVE_OK) else None
    return "other" if has_emissive else None


def scan(scene):
    """Sort the placed models into light types. Returns {cid: {key: (points, materials, colour, n_props)}}."""
    from .regions import get_discs, outside_mask
    inst = _instances()
    discs = get_discs(scene)
    found = {}
    for key, matrices in inst.items():
        if len(discs):
            # POIs shown (Regions / POI): only the props inside them get lights
            pos = np.array([tuple(mw.translation) for mw in matrices], dtype=np.float32)
            out = outside_mask(pos, discs)
            matrices = [mw for mw, o in zip(matrices, out) if not o]
            if not matrices:
                continue
        objs = _model_lod0_objects(key)
        has_em = any(_is_emissive(ms.material) for o in objs for ms in o.material_slots)
        cid = categorize(key, has_em)
        if cid is None:
            continue
        pts, ems, col = _light_points(key)
        if not pts:
            continue
        found.setdefault(cid, {})[key] = (pts, ems, col, matrices)
    s = scene.r5_settings
    keep = {}
    for c in s.light_cats:
        keep[c.cid] = {k: (tuple(getattr(c, k)) if k == "color" else getattr(c, k)) for k in _KEEP}
    s.light_cats.clear()
    for cid, label, _rx, power, color, radius, ltype in CATEGORIES:
        models = found.get(cid, {})
        if not models:
            continue
        c = s.light_cats.add()
        c.cid, c.label = cid, label
        c.models = len(models)
        c.props = sum(len(v[3]) for v in models.values())
        if cid in keep:
            for k, v in keep[cid].items():
                setattr(c, k, v)
        else:
            c.power, c.color, c.radius, c.light_type = power, color, radius, ltype
            c.enabled = cid != "other"
        existing = bpy.data.collections.get(f"{ROOT}_{cid}")
        c.created = len(existing.objects) if existing is not None else 0
    return found


# --------------------------------------------------------------------------
# Building
# --------------------------------------------------------------------------
def _root(scene):
    col = bpy.data.collections.get(ROOT)
    if col is None:
        col = bpy.data.collections.new(ROOT)
    if col.name not in scene.collection.children:
        scene.collection.children.link(col)
    return col


def _cat_collection(scene, cid):
    root = _root(scene)
    name = f"{ROOT}_{cid}"
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    if col.name not in root.children:
        root.children.link(col)
    return col


def clear(scene, cid=None):
    for c in list(bpy.data.collections):
        if not c.name.startswith(ROOT + "_") or (cid and c.name != f"{ROOT}_{cid}"):
            continue
        datas = set()
        for o in list(c.objects):
            if o.type == "LIGHT":
                datas.add(o.data)
            bpy.data.objects.remove(o, do_unlink=True)
        for d in datas:
            if d.users == 0:
                bpy.data.lights.remove(d)
        bpy.data.collections.remove(c)
    for c in scene.r5_settings.light_cats:
        if cid is None or c.cid == cid:
            c.created = 0


MAP_LIGHT_RADIUS = 3.0     # m: a map light entity this close to a glowing part gives it its colour


def _map_lights(scene):
    """KD-tree and colours of the map's own light entities (light / light_spot of the env .ent), stored at import."""
    from mathutils.kdtree import KDTree
    flat = scene.get("apx_map_lights")
    if not flat:
        return None
    a = np.array(flat[:], dtype=np.float32).reshape(-1, 6)
    kd = KDTree(len(a))
    for i, p in enumerate(a[:, :3]):
        kd.insert(Vector(p), i)
    kd.balance()
    return kd, a[:, 3:]


def _nearest_map_light(ml, wp):
    if ml is None:
        return None
    kd, cols = ml
    _co, i, dist = kd.find(wp)
    if i is None or dist > MAP_LIGHT_RADIUS:
        return None
    return tuple(float(v) for v in cols[i])


def build(scene, found=None, report=print):
    """Create the point lights of every type (existing ones are replaced)."""
    s = scene.r5_settings
    if found is None:
        found = scan(scene)
    clear(scene)
    cursor = scene.cursor.location.copy()
    map_lights = _map_lights(scene)
    total = 0
    for c in s.light_cats:
        models = found.get(c.cid, {})
        col = _cat_collection(scene, c.cid)
        items = []          # (distance to cursor, world point, light data)
        for key, (pts, mats, colr, inst) in models.items():
            datas = {}      # one light datablock per colour of this model

            def light_data(rgb):
                q = tuple(round(v * 16) / 16 for v in rgb)
                ld = datas.get(q)
                if ld is None:
                    ld = datas[q] = bpy.data.lights.new(f"R5L_{c.cid}_{key}", c.light_type)
                    ld["r5_cat"] = c.cid
                    ld["r5_model_rgb"] = q
                return ld
            for m in mats:
                m["r5_light_cat"] = c.cid
            for mw in inst:
                rot = mw.to_3x3()
                for p, d, prgb in pts:
                    wp = mw @ p
                    wd = rot @ d
                    rgb = _nearest_map_light(map_lights, wp) or prgb or colr or (1.0, 1.0, 1.0)
                    items.append(((wp - cursor).length_squared, wp, wd, light_data(rgb), key))
        items.sort(key=lambda t: t[0])
        items = items[: c.max_lights]
        items.sort(key=lambda t: t[4])               # name order: fast object creation
        for _d, wp, wd, ld, key in items:
            o = bpy.data.objects.new(f"R5L_{key}", ld)
            # spot / area lights shine along their local -Z: towards the outside of the glowing part
            if wd.length > 1e-6:
                wd.normalize()
                o.location = wp + wd * 0.03          # just in front of the glowing surface
                o.rotation_euler = wd.to_track_quat("-Z", "Y").to_euler()
            else:
                o.location = wp
            col.objects.link(o)
        # drop light datablocks that got no object (beyond the limit)
        for ld in [d for d in bpy.data.lights if d.get("r5_cat") == c.cid and d.users == 0]:
            bpy.data.lights.remove(ld)
        c.created = len(items)
        total += len(items)
        apply_category(scene, c)
    report(f"City lights: {total} lights created")
    return total


def apply_category(scene, c):
    """Push the settings of a light type to its lights and to the emissive materials of its props."""
    for ld in bpy.data.lights:
        if ld.get("r5_cat") != c.cid:
            continue
        if ld.type != c.light_type:
            name = ld.name
            ld.type = c.light_type
            ld = bpy.data.lights[name]          # the type change gives the datablock a new Python class
        ld.energy = c.power
        ld.use_shadow = c.shadows
        if ld.type == "AREA":
            ld.shape = c.area_shape
            ld.size = max(c.radius * 2.0, 0.01)
            ld.size_y = max(c.radius * 2.0, 0.01)
            if hasattr(ld, "spread"):
                ld.spread = math.radians(c.spot_angle) if c.spot_angle < 180 else math.pi
        else:
            ld.shadow_soft_size = c.radius
        if ld.type == "SPOT":
            ld.spot_size = math.radians(c.spot_angle)
            ld.spot_blend = c.spot_blend
        if c.use_model_color and "r5_model_rgb" in ld:
            ld.color = tuple(ld["r5_model_rgb"])[:3]
        else:
            ld.color = tuple(c.color)
    col = bpy.data.collections.get(f"{ROOT}_{c.cid}")
    if col is not None:
        col.hide_viewport = not c.enabled
        col.hide_render = not c.enabled
    for m in bpy.data.materials:
        if m.get("r5_light_cat") != c.cid or not m.use_nodes:
            continue
        for n in m.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED" and "Emission Strength" in n.inputs:
                n.inputs["Emission Strength"].default_value = c.emission


# --------------------------------------------------------------------------
# Operators / panel
# --------------------------------------------------------------------------
class R5_OT_lights_scan(bpy.types.Operator):
    """Sort the placed props into light types (street lamps, signs, floodlights...)"""
    bl_idname = "r5.lights_scan"
    bl_label = "Find light sources"

    def execute(self, context):
        found = scan(context.scene)
        n = sum(len(v) for v in found.values())
        self.report({"INFO"}, f"{n} light-source models in {len(found)} types")
        return {"FINISHED"}


class R5_OT_lights_build(bpy.types.Operator):
    """Create a light at every glowing part of every light-source prop (replaces the previous lights)"""
    bl_idname = "r5.lights_build"
    bl_label = "Create lights"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        build(context.scene, report=lambda m: self.report({"INFO"}, m))
        return {"FINISHED"}


class R5_OT_lights_clear(bpy.types.Operator):
    """Remove the city lights"""
    bl_idname = "r5.lights_clear"
    bl_label = "Remove lights"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        clear(context.scene)
        return {"FINISHED"}


class R5_PT_lights(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "R5 Map Loader"
    bl_label = "City lights"
    bl_idname = "R5_PT_lights"
    bl_order = 4                 # below Sky (ui.py)
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        if not hasattr(s, "light_cats"):
            col.label(text="Restart Blender to finish the update", icon="ERROR")
            return
        col.operator("r5.lights_scan", icon="VIEWZOOM")
        for c in s.light_cats:
            box = col.box()
            row = box.row(align=True)
            row.prop(c, "enabled", text="", icon="LIGHT_POINT" if c.enabled else "HIDE_ON", emboss=True)
            row.label(text=f"{c.label}: {c.props} props, {c.created} lights")
            if not c.enabled:
                continue
            box.row(align=True).prop(c, "light_type", expand=True)
            if c.light_type == "SPOT":
                row = box.row(align=True)
                row.prop(c, "spot_angle")
                row.prop(c, "spot_blend")
            elif c.light_type == "AREA":
                row = box.row(align=True)
                row.prop(c, "area_shape", text="")
                row.prop(c, "spot_angle", text="Spread")
            box.prop(c, "power")
            row = box.row(align=True)
            row.prop(c, "use_model_color", text="Model colour", toggle=True)
            sub = row.row(align=True)
            sub.enabled = not c.use_model_color
            sub.prop(c, "color", text="")
            row = box.row(align=True)
            row.prop(c, "radius")
            row.prop(c, "shadows", toggle=True)
            row = box.row(align=True)
            row.prop(c, "emission")
            row.prop(c, "max_lights", text="Max")
        if len(s.light_cats):
            row = col.row(align=True)
            row.scale_y = 1.2
            row.operator("r5.lights_build", icon="LIGHT")
            row.operator("r5.lights_clear", text="", icon="TRASH")
            col.label(text="Lights near the 3D cursor first (Max)", icon="INFO")
            from .regions import get_discs
            if len(get_discs(context.scene)):
                col.label(text="Only in the POIs shown (Regions / POI)", icon="SELECT_SUBTRACT")


CLASSES = (R5_OT_lights_scan, R5_OT_lights_build, R5_OT_lights_clear, R5_PT_lights)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        if getattr(c, "is_registered", False):
            bpy.utils.unregister_class(c)
