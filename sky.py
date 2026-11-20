"""Sky / skybox for the map.

What the game data gives us: in R5Reloaded VPKs, the entities that define the sky and the sun
(light_environment, sky_camera...) are in <map>_env.ent, which is LZHAM-compressed, and the BSP
lumps are compressed too. When an uncompressed env .ent is provided, its light_environment is
used for the sun direction and color. Sky sources:
  - PROCEDURAL: Blender's physical sky (Nishita) + a sun lamp,
  - IMAGE: an equirectangular panorama / HDRI,
  - CUBEMAP: 6 face images (e.g. a sky cubemap exported with RSX: name_000..name_005,
    or px/nx/py/ny/pz/nz, right/left/up/down/front/back) converted to a panorama,
  - DOME: the in-game sky dome texture (square image, round sky, zenith in the centre, horizon on
    the circle), mapped live with shader nodes (EEVEE and Cycles).

On top of an image sky: an optional physical atmosphere layer (air / dust / ozone densities of
Blender's Nishita sky, strongest near the horizon) and an optional volumetric fog (world volume).
The sun lamp has its own colour. Value changes are applied live, without rebuilding the world.
"""

import json
import math
import os

import bpy
import numpy as np
from mathutils import Euler, Vector

WORLD = "R5_Sky"
SUN = "R5_Sun"
PANO = "R5_Sky_Panorama"
DEFAULT_SUN_COLOR = (1.0, 0.96, 0.9)

# Mean radiance of the upper hemisphere of Blender's Nishita sky (air = dust = ozone = 1, altitude 500 m),
# measured per sun elevation (degrees). Used to bring the atmosphere layer to the brightness of the image.
_NISHITA_BASE = ((-10, 0.3), (5, 1.51), (15, 2.55), (35, 2.94), (60, 3.14), (85, 3.64), (90, 3.7))


def _nishita_base(elev_rad):
    e = math.degrees(elev_rad)
    pts = _NISHITA_BASE
    if e <= pts[0][0]:
        return pts[0][1]
    for (e0, v0), (e1, v1) in zip(pts, pts[1:]):
        if e <= e1:
            return v0 + (v1 - v0) * (e - e0) / (e1 - e0)
    return pts[-1][1]


def _to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


# --------------------------------------------------------------------------
# Sun
# --------------------------------------------------------------------------
def sun_from_entities(sun_ent):
    """light_environment -> (elevation, azimuth, color) in radians, or None."""
    if not sun_ent:
        return None
    ang = [float(v) for v in sun_ent.get("angles", "0 0 0").split()[:3]] + [0, 0, 0]
    pitch = float(sun_ent.get("pitch", ang[0]))
    yaw = ang[1]
    col = DEFAULT_SUN_COLOR
    lv = sun_ent.get("_light", "").split()
    if len(lv) >= 3:
        try:
            rgb = [float(v) / 255.0 for v in lv[:3]]
            mx = max(rgb) or 1.0
            col = tuple(c / mx for c in rgb)
        except ValueError:
            pass
    # Source: pitch is negative when the light points down
    return math.radians(-pitch if pitch < 0 else pitch), math.radians(yaw + 180.0), col


def _map_sun(s):
    if s.map_sun_json:
        try:
            return sun_from_entities(json.loads(s.map_sun_json))
        except (ValueError, TypeError):
            return None
    return None


def sun_angles(s):
    """(elevation, azimuth) in radians: the map's light_environment when available and wanted."""
    if s.sky_use_map_sun:
        got = _map_sun(s)
        if got:
            return got[0], got[1]
    return math.radians(s.sky_sun_elevation), math.radians(s.sky_sun_rotation)


def default_sun_color(s):
    got = _map_sun(s)
    return tuple(got[2]) if got else DEFAULT_SUN_COLOR


def _sun(scene, s):
    sun = bpy.data.objects.get(SUN)
    if not s.sky_add_sun:
        if sun is not None and sun.name in scene.collection.objects:
            scene.collection.objects.unlink(sun)
        return None
    if sun is None:
        sun = bpy.data.objects.new(SUN, bpy.data.lights.new(SUN, "SUN"))
    if sun.name not in scene.collection.objects:
        scene.collection.objects.link(sun)
    elev, azim = sun_angles(s)
    sun.data.energy = s.sky_sun_strength
    sun.data.color = tuple(s.sky_sun_color)
    sun.data.angle = math.radians(0.6)
    # A sun lamp shines along its local -Z: tilt it down from the zenith
    sun.rotation_euler = Euler((math.radians(90.0) - elev, 0.0, azim + math.radians(90.0)), "XYZ")
    return sun


# --------------------------------------------------------------------------
# World building blocks
# --------------------------------------------------------------------------
def _world(scene):
    w = bpy.data.worlds.get(WORLD) or bpy.data.worlds.new(WORLD)
    w.use_nodes = True
    w["r5_sky_mode"] = ""
    scene.world = w
    nt = w.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld"); out.location = (1300, 0)
    bg = nt.nodes.new("ShaderNodeBackground"); bg.location = (1100, 0); bg.name = "R5_Bg"
    nt.links.new(bg.outputs[0], out.inputs["Surface"])
    return w, nt, bg


def _set_nishita(node, s, sun_disc):
    elev, azim = sun_angles(s)
    # Blender 5.0+ renamed NISHITA to SINGLE_SCATTERING (same model, keeps the brightness calibration
    # of _NISHITA_BASE) and dust_density to aerosol_density.
    types = {e.identifier for e in node.bl_rna.properties["sky_type"].enum_items}
    sky_type = "NISHITA" if "NISHITA" in types else "SINGLE_SCATTERING"
    values = (("sky_type", sky_type), ("sun_elevation", elev), ("sun_rotation", azim), ("altitude", 500.0),
              ("air_density", s.sky_air_density), ("dust_density", s.sky_dust_density),
              ("aerosol_density", s.sky_dust_density), ("ozone_density", s.sky_ozone_density),
              ("sun_disc", sun_disc))
    for attr, val in values:
        if hasattr(node, attr):
            try:
                setattr(node, attr, val)
            except (AttributeError, TypeError, ValueError):
                pass


def _mix_rgba(nt, factor, a, b, loc, name=""):
    mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.location = loc
    if name:
        mix.name = name
    for sk in mix.inputs:
        if sk.identifier == "Factor_Float":
            if isinstance(factor, float):
                sk.default_value = factor
            else:
                nt.links.new(factor, sk)
        elif sk.identifier == "A_Color":
            nt.links.new(a, sk) if not isinstance(a, (tuple, list)) else setattr(sk, "default_value", a)
        elif sk.identifier == "B_Color":
            nt.links.new(b, sk) if not isinstance(b, (tuple, list)) else setattr(sk, "default_value", b)
    return next(sk for sk in mix.outputs if sk.identifier == "Result_Color")


def _atmosphere(nt, color, s, mean_lum):
    """Physical sky (air / dust / ozone) blended over the image, strongest towards the horizon."""
    N, L = nt.nodes, nt.links
    sky = N.new("ShaderNodeTexSky"); sky.name = "R5_Atmo_Sky"; sky.location = (500, -400)
    _set_nishita(sky, s, sun_disc=False)
    sc = N.new("ShaderNodeVectorMath"); sc.operation = "SCALE"; sc.name = "R5_Atmo_Scale"; sc.location = (700, -400)
    sc.inputs["Scale"].default_value = mean_lum / _nishita_base(sun_angles(s)[0])
    L.new(sky.outputs[0], sc.inputs[0])
    tc = N.new("ShaderNodeTexCoord"); tc.location = (100, -650)
    sep = N.new("ShaderNodeSeparateXYZ"); sep.location = (250, -650)
    L.new(tc.outputs["Generated"], sep.inputs[0])

    def m(op, a, b, loc, name=""):
        n = N.new("ShaderNodeMath"); n.operation = op; n.location = loc
        if name:
            n.name = name
        for i, v in enumerate((a, b)):
            if isinstance(v, float):
                n.inputs[i].default_value = v
            else:
                L.new(v, n.inputs[i])
        return n
    z = m("MAXIMUM", sep.outputs[2], 0.0, (400, -650)).outputs[0]
    one_minus = m("SUBTRACT", 1.0, z, (550, -650)).outputs[0]
    p = m("POWER", one_minus, 3.0, (700, -650)).outputs[0]
    wgt = N.new("ShaderNodeMath"); wgt.operation = "MULTIPLY_ADD"; wgt.location = (850, -650)
    L.new(p, wgt.inputs[0]); wgt.inputs[1].default_value = 0.65; wgt.inputs[2].default_value = 0.35
    f = m("MULTIPLY", wgt.outputs[0], s.sky_atmosphere_strength, (1000, -650), "R5_Atmo_Strength")
    f.use_clamp = True
    return _mix_rgba(nt, f.outputs[0], color, sc.outputs["Vector"], (900, -150), "R5_Atmo_Mix")


FOG = "R5_Fog"


def _fog_bounds(scene):
    """Box around the map geometry / props (meters), with some margin and height for the haze."""
    lo = hi = None
    for o in scene.objects:
        if not (o.get("apx_world_tile") or "apx_gn" in o) or o.type != "MESH":
            continue
        for c in o.bound_box:
            wv = o.matrix_world @ Vector(c)
            lo = wv.copy() if lo is None else Vector(map(min, lo, wv))
            hi = wv.copy() if hi is None else Vector(map(max, hi, wv))
    if lo is None:
        return (-2000.0, -2000.0, -50.0), (2000.0, 2000.0, 600.0)
    return (lo.x - 300, lo.y - 300, lo.z - 20), (hi.x + 300, hi.y + 300, hi.z + 400)


def _fog_object(scene, s):
    """Volumetric fog as a volume box around the map. A world volume would be infinite: EEVEE then
    absorbs the whole sky and the sun (black image)."""
    ob = bpy.data.objects.get(FOG)
    if not s.sky_fog:
        if ob is not None:
            me, mat = ob.data, bpy.data.materials.get(FOG)
            bpy.data.objects.remove(ob)
            if me is not None and me.users == 0:
                bpy.data.meshes.remove(me)
            if mat is not None and mat.users == 0:
                bpy.data.materials.remove(mat)
        return None
    if ob is None:
        (x0, y0, z0), (x1, y1, z1) = _fog_bounds(scene)
        me = bpy.data.meshes.new(FOG)
        verts = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        me.from_pydata(verts, [], faces)
        ob = bpy.data.objects.new(FOG, me)
        ob.display_type = "BOUNDS"
        ob.hide_select = True
        ob["r5_fog"] = True
        mat = bpy.data.materials.get(FOG) or bpy.data.materials.new(FOG)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (300, 0)
        vol = nt.nodes.new("ShaderNodeVolumePrincipled"); vol.name = "R5_Fog"
        nt.links.new(vol.outputs[0], out.inputs["Volume"])
        me.materials.append(mat)
    if ob.name not in scene.collection.objects:
        scene.collection.objects.link(ob)
    mat = ob.data.materials[0] if ob.data.materials else None
    if mat is not None and mat.node_tree and "R5_Fog" in mat.node_tree.nodes:
        _set_fog_values(mat.node_tree.nodes["R5_Fog"], s)
    _fog_range(scene, s)
    return ob


def _fog(scene, nt, s):
    _fog_object(scene, s)


def _set_fog_values(vol, s):
    vol.inputs["Density"].default_value = s.sky_fog_density
    vol.inputs["Color"].default_value = tuple(s.sky_fog_color) + (1.0,)
    vol.inputs["Anisotropy"].default_value = s.sky_fog_anisotropy


def _fog_range(scene, s):
    ee = getattr(scene, "eevee", None)
    if ee is not None and hasattr(ee, "volumetric_end"):
        try:
            ee.volumetric_end = s.sky_fog_distance
        except (AttributeError, TypeError):
            pass


def _finish(scene, w, nt, bg, color, mean_lum, mode):
    s = scene.r5_settings
    if s.sky_atmosphere and mode != "PROCEDURAL":
        color = _atmosphere(nt, color, s, mean_lum)
    nt.links.new(color, bg.inputs["Color"])
    bg.inputs["Strength"].default_value = s.sky_strength
    _fog(scene, nt, s)
    w["r5_sky_mode"] = mode
    w["r5_mean"] = float(mean_lum)
    _sun(scene, s)
    return w


def _mean_luminance(img, circle=None):
    """Mean linear luminance of an image (inside the sky disc for a dome). Cached on the image."""
    if "r5_mean" in img:
        return float(img["r5_mean"])
    w, h = img.size
    if not w or not h:
        return 0.5
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    step = max(1, max(w, h) // 512)
    a = px.reshape(h, w, 4)[::step, ::step, :3]
    if circle is not None:
        cx, cy, r = circle
        hh, ww = a.shape[:2]
        yy, xx = np.mgrid[0:hh, 0:ww]
        a = a[((xx + 0.5) / ww - cx) ** 2 + ((yy + 0.5) / hh - cy) ** 2 < r * r]
    a = a.reshape(-1, 3)
    if not img.is_float:
        a = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    mean = float((a @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean()) if len(a) else 0.5
    img["r5_mean"] = mean
    return mean


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------
def setup_procedural(scene):
    s = scene.r5_settings
    w, nt, bg = _world(scene)
    sky = nt.nodes.new("ShaderNodeTexSky"); sky.location = (800, 0); sky.name = "R5_Sky_Nishita"
    _set_nishita(sky, s, sun_disc=True)
    return _finish(scene, w, nt, bg, sky.outputs[0], 1.0, "PROCEDURAL")


def _setup_pano(scene, img, mode, rotation_deg=0.0):
    s = scene.r5_settings
    w, nt, bg = _world(scene)
    env = nt.nodes.new("ShaderNodeTexEnvironment"); env.location = (600, 0)
    env.image = img
    tc = nt.nodes.new("ShaderNodeTexCoord"); tc.location = (200, 0)
    mp = nt.nodes.new("ShaderNodeMapping"); mp.location = (400, 0); mp.name = "R5_Rot_Map"
    mp.inputs["Rotation"].default_value[2] = math.radians(rotation_deg)
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs[0], env.inputs["Vector"])
    return _finish(scene, w, nt, bg, env.outputs[0], _mean_luminance(img), mode)


def setup_image(scene, path):
    img = bpy.data.images.load(path, check_existing=True)
    return _setup_pano(scene, img, "IMAGE", scene.r5_settings.sky_image_rotation)


# ---- in-game sky dome (round image: zenith in the centre, horizon on the circle) ----
def find_dome_circle(img):
    """Centre and radius of the sky disc in UV units: the area that differs from the corner colour.
    Cached on the image."""
    if "r5_circle" in img:
        return tuple(img["r5_circle"])
    w, h = img.size
    if not w or not h:
        raise ValueError("Cannot read the sky image")
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    step = max(1, max(w, h) // 512)
    a = px.reshape(h, w, 4)[::step, ::step, :3]
    hh, ww = a.shape[:2]
    corners = np.concatenate([a[:4, :4].reshape(-1, 3), a[:4, -4:].reshape(-1, 3),
                              a[-4:, :4].reshape(-1, 3), a[-4:, -4:].reshape(-1, 3)])
    fill = np.median(corners, axis=0)
    img["r5_fill"] = [float(c) for c in fill]
    diff = np.abs(a - fill).max(axis=2) > 0.04
    ys, xs = np.nonzero(diff)
    if len(xs) < 100:
        circle = (0.5, 0.5, 0.5)
    else:
        x0, x1 = np.percentile(xs, [0.2, 99.8])
        y0, y1 = np.percentile(ys, [0.2, 99.8])
        cx, cy = (x0 + x1 + 1) / 2 / ww, (y0 + y1 + 1) / 2 / hh
        r = max((x1 - x0 + 1) / ww, (y1 - y0 + 1) / hh) / 2
        circle = (float(cx), float(cy), float(min(max(r, 0.1), 0.75)))
    img["r5_circle"] = circle
    return circle


def is_dome_image(img):
    w, h = img.size
    return w > 0 and abs(w - h) <= max(2, w // 100)


def setup_dome(scene, path):
    """World shader mapping a round sky image onto the sky: view direction -> angle from the zenith
    -> radius in the disc (equidistant / equisolid / orthographic), azimuth -> angle in the disc."""
    s = scene.r5_settings
    w, nt, bg = _world(scene)
    N, L = nt.nodes, nt.links
    img = bpy.data.images.load(path, check_existing=True)
    cx, cy, rad = find_dome_circle(img)
    k = rad * s.sky_dome_horizon          # disc radius at the horizon (UV units)

    def mth(op, a, b=None, loc=(0, 0)):
        n = N.new("ShaderNodeMath"); n.operation = op; n.location = loc
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                L.new(v, n.inputs[i])
        return n.outputs[0]

    tc = N.new("ShaderNodeTexCoord"); tc.location = (-1600, 0)
    rot = N.new("ShaderNodeVectorRotate"); rot.location = (-1400, 0); rot.name = "R5_Rot"
    rot.rotation_type = "Z_AXIS"
    rot.inputs["Angle"].default_value = math.radians(s.sky_image_rotation)
    L.new(tc.outputs["Generated"], rot.inputs["Vector"])
    sep = N.new("ShaderNodeSeparateXYZ"); sep.location = (-1200, 0)
    L.new(rot.outputs[0], sep.inputs[0])
    x, y, z = sep.outputs[0], sep.outputs[1], sep.outputs[2]
    cz = N.new("ShaderNodeClamp"); cz.location = (-1000, -200)
    cz.inputs["Min"].default_value, cz.inputs["Max"].default_value = -1.0, 1.0
    L.new(z, cz.inputs["Value"])
    theta = mth("ARCCOSINE", cz.outputs[0], loc=(-850, -200))            # 0 = zenith, pi/2 = horizon
    half_pi = math.pi / 2
    if s.sky_dome_projection == "EQUISOLID":
        r = mth("MULTIPLY", mth("SINE", mth("MULTIPLY", theta, 0.5, (-700, -200)), loc=(-550, -200)),
                k / math.sin(math.pi / 4), (-400, -200))
    elif s.sky_dome_projection == "ORTHO":
        above = mth("SINE", mth("MINIMUM", theta, half_pi, (-700, -150)), loc=(-550, -150))
        below = mth("DIVIDE", mth("MAXIMUM", mth("SUBTRACT", theta, half_pi, (-700, -300)), 0.0,
                                  (-550, -300)), half_pi, (-400, -300))
        r = mth("MULTIPLY", mth("ADD", above, below, (-300, -200)), k, (-200, -200))
    else:   # EQUIDISTANT
        r = mth("MULTIPLY", theta, k / half_pi, (-550, -200))
    # azimuth: unit horizontal direction
    ln = mth("SQRT", mth("ADD", mth("MULTIPLY", x, x, (-1000, 200)), mth("MULTIPLY", y, y, (-1000, 100)),
                         (-850, 150)), loc=(-700, 150))
    ln = mth("MAXIMUM", ln, 1e-6, (-550, 150))
    dx = mth("DIVIDE", x, ln, (-400, 250))
    dy = mth("DIVIDE", y, ln, (-400, 100))
    if s.sky_dome_mirror:
        dx = mth("MULTIPLY", dx, -1.0, (-250, 250))
    u = mth("ADD", mth("MULTIPLY", dx, r, (-100, 250)), cx, (50, 250))
    v = mth("ADD", mth("MULTIPLY", dy, r, (-100, 100)), cy, (50, 100))
    cmb = N.new("ShaderNodeCombineXYZ"); cmb.location = (200, 150)
    L.new(u, cmb.inputs[0]); L.new(v, cmb.inputs[1])
    tex = N.new("ShaderNodeTexImage"); tex.location = (400, 150)
    tex.image = img
    tex.extension = "EXTEND"
    tex.interpolation = "Linear"
    L.new(cmb.outputs[0], tex.inputs["Vector"])
    # Outside the disc (below the horizon): the image's background colour, not the stretched border
    outside = mth("GREATER_THAN", r, rad * 0.995, (200, -100))
    fill = list(img.get("r5_fill", (0.5, 0.5, 0.5)))[:3]
    if not img.is_float:   # 8-bit pixels are sRGB encoded, the node colour is linear
        fill = [_to_linear(c) for c in fill]
    res = _mix_rgba(nt, outside, tex.outputs["Color"], tuple(fill) + (1.0,), (650, 150))
    return _finish(scene, w, nt, bg, res, _mean_luminance(img, (cx, cy, rad)), "DOME")


# ---- cubemap -> panorama ----
_FACE_TAGS = (
    ("+X", ("_000", "px", "posx", "right", "_rt")),
    ("-X", ("_001", "nx", "negx", "left", "_lf")),
    ("+Y", ("_002", "py", "posy", "up", "_up")),
    ("-Y", ("_003", "ny", "negy", "down", "_dn")),
    ("+Z", ("_004", "pz", "posz", "front", "_ft")),
    ("-Z", ("_005", "nz", "negz", "back", "_bk")),
)


def find_cube_faces(any_face_path):
    """Find the 6 faces next to one of them. Returns {"+X": path, ...} or raises ValueError."""
    folder = os.path.dirname(any_face_path)
    stem = os.path.splitext(os.path.basename(any_face_path))[0].lower()
    ext_ok = (".png", ".jpg", ".jpeg", ".tga", ".dds", ".exr", ".hdr", ".tif", ".tiff", ".bmp", ".webp")
    files = [f for f in os.listdir(folder) if f.lower().endswith(ext_ok)]
    all_tags = sorted({t.strip("_") for _k, tags in _FACE_TAGS for t in tags}, key=len, reverse=True)
    # common prefix = the stem without its face tag
    prefix = stem
    for t in all_tags:
        if stem.endswith(t):
            prefix = stem[: -len(t)].rstrip("_-. ")
            break
    faces = {}
    for key, tags in _FACE_TAGS:
        wanted = {t.strip("_") for t in tags}
        for f in files:
            fs = os.path.splitext(f)[0].lower()
            if fs.startswith(prefix) and fs[len(prefix):].strip("_-. ") in wanted:
                faces[key] = os.path.join(folder, f)
                break
    if len(faces) != 6:
        raise ValueError(f"Found {len(faces)}/6 cubemap faces next to {os.path.basename(any_face_path)} "
                         f"(expected name_000..name_005, px/nx/py/ny/pz/nz or right/left/up/down/front/back)")
    return faces


def _load_rgba(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)[::-1]          # top row first


def cubemap_to_panorama(faces, width=2048, rotation_deg=0.0, y_up=True):
    """Convert 6 cube faces (D3D/OpenGL convention) into an equirectangular image (numpy HxWx4)."""
    data = {k: _load_rgba(p) for k, p in faces.items()}
    h = width // 2
    u = (np.arange(width) + 0.5) / width
    v = (np.arange(h) + 0.5) / h
    lon = (u - 0.5) * 2 * math.pi + math.radians(rotation_deg)
    lat = (0.5 - v) * math.pi                  # top row = +90 degrees
    lon, lat = np.meshgrid(lon, lat)
    # Blender equirect: u = atan2(y, -x) / 2pi + 0.5
    bx = -np.cos(lat) * np.cos(lon)
    by = np.cos(lat) * np.sin(lon)
    bz = np.sin(lat)
    if y_up:   # cube faces authored Y-up (D3D / RSX texture arrays): Blender (x, y, z) -> (x, z, y)
        X, Y, Z = bx, bz, by
    else:
        X, Y, Z = bx, by, bz
    ax, ay, az = np.abs(X), np.abs(Y), np.abs(Z)
    out = np.zeros((h, width, 4), np.float32)
    rules = (
        ("+X", (ax >= ay) & (ax >= az) & (X > 0), -Z, -Y, ax),
        ("-X", (ax >= ay) & (ax >= az) & (X <= 0), Z, -Y, ax),
        ("+Y", (ay > ax) & (ay >= az) & (Y > 0), X, Z, ay),
        ("-Y", (ay > ax) & (ay >= az) & (Y <= 0), X, -Z, ay),
        ("+Z", (az > ax) & (az > ay) & (Z > 0), X, -Y, az),
        ("-Z", (az > ax) & (az > ay) & (Z <= 0), -X, -Y, az),
    )
    for key, mask, sc, tc, ma in rules:
        img = data[key]
        fh, fw = img.shape[:2]
        s_ = (sc[mask] / ma[mask] + 1) * 0.5
        t_ = (tc[mask] / ma[mask] + 1) * 0.5
        xi = np.clip((s_ * fw).astype(np.int32), 0, fw - 1)
        yi = np.clip((t_ * fh).astype(np.int32), 0, fh - 1)
        out[mask] = img[yi, xi]
    out[..., 3] = 1.0
    return out


def setup_cubemap(scene, any_face_path):
    s = scene.r5_settings
    faces = find_cube_faces(any_face_path)
    pano = cubemap_to_panorama(faces, int(s.sky_panorama_width), s.sky_image_rotation, s.sky_cubemap_y_up)
    h, w = pano.shape[:2]
    img = bpy.data.images.get(PANO)
    if img is not None and tuple(img.size) != (w, h):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(PANO, w, h, alpha=False, float_buffer=True)
    img.pixels.foreach_set(pano[::-1].ravel())
    img.pack()
    if "r5_mean" in img:
        del img["r5_mean"]
    return _setup_pano(scene, img, "CUBEMAP")       # rotation already baked into the panorama


# --------------------------------------------------------------------------
# Live updates
# --------------------------------------------------------------------------
def _current(scene):
    w = bpy.data.worlds.get(WORLD)
    if w is None or scene.world != w or not w.get("r5_sky_mode"):
        return None, None
    return w, w["r5_sky_mode"]


def refresh(scene):
    """Rebuild the current sky with the current settings (structure changes)."""
    w, mode = _current(scene)
    if w is None:
        return
    s = scene.r5_settings
    path = bpy.path.abspath(s.sky_image) if s.sky_image else ""
    if mode == "PROCEDURAL":
        setup_procedural(scene)
    elif mode == "DOME" and os.path.isfile(path):
        setup_dome(scene, path)
    elif mode == "IMAGE" and os.path.isfile(path):
        setup_image(scene, path)
    elif mode == "CUBEMAP" and bpy.data.images.get(PANO) is not None:
        _setup_pano(scene, bpy.data.images[PANO], "CUBEMAP")


def update_live(scene):
    """Apply value changes in place (no shader rebuild); rebuild only when the structure changes."""
    s = scene.r5_settings
    w, mode = _current(scene)
    if w is None:
        _sun_if_present(scene, s)
        _fog_object(scene, s)
        return
    nodes = w.node_tree.nodes
    want_atmo = s.sky_atmosphere and mode != "PROCEDURAL"
    if ("R5_Atmo_Sky" in nodes) != want_atmo:
        refresh(scene)
        return
    _fog_object(scene, s)
    for name, disc in (("R5_Sky_Nishita", True), ("R5_Atmo_Sky", False)):
        n = nodes.get(name)
        if n is not None:
            _set_nishita(n, s, sun_disc=disc)
    n = nodes.get("R5_Atmo_Scale")
    if n is not None:
        n.inputs["Scale"].default_value = float(w.get("r5_mean", 0.5)) / _nishita_base(sun_angles(s)[0])
    n = nodes.get("R5_Atmo_Strength")
    if n is not None:
        n.inputs[1].default_value = s.sky_atmosphere_strength
    n = nodes.get("R5_Bg")
    if n is not None:
        n.inputs["Strength"].default_value = s.sky_strength
    n = nodes.get("R5_Rot")
    if n is not None:
        n.inputs["Angle"].default_value = math.radians(s.sky_image_rotation)
    n = nodes.get("R5_Rot_Map")
    if n is not None and mode == "IMAGE":
        n.inputs["Rotation"].default_value[2] = math.radians(s.sky_image_rotation)
    _sun(scene, s)


def _sun_if_present(scene, s):
    if bpy.data.objects.get(SUN) is not None:
        _sun(scene, s)


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------
class R5_OT_setup_sky(bpy.types.Operator):
    """Create the world sky (and a sun lamp) for the map"""
    bl_idname = "r5.setup_sky"
    bl_label = "Apply sky"

    def execute(self, context):
        scene = context.scene
        s = scene.r5_settings
        try:
            if s.sky_mode == "PROCEDURAL":
                setup_procedural(scene)
            elif s.sky_mode in ("IMAGE", "DOME"):
                path = bpy.path.abspath(s.sky_image)
                if not os.path.isfile(path):
                    raise ValueError("Choose a sky image")
                img = bpy.data.images.load(path, check_existing=True)
                if s.sky_mode == "IMAGE" and is_dome_image(img):
                    # square image = in-game dome texture, not a 2:1 panorama
                    s.sky_mode = "DOME"
                    self.report({"INFO"}, "Square image: used as an in-game sky dome")
                if s.sky_mode == "DOME":
                    setup_dome(scene, path)
                else:
                    setup_image(scene, path)
            else:
                path = bpy.path.abspath(s.sky_image)
                if not os.path.isfile(path):
                    raise ValueError("Choose one of the 6 cubemap face images")
                setup_cubemap(scene, path)
        except (ValueError, RuntimeError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Sky applied ({s.sky_mode.lower()})")
        return {"FINISHED"}


class R5_OT_sun_color_reset(bpy.types.Operator):
    """Reset the sun colour (to the map's light_environment colour when one was loaded)"""
    bl_idname = "r5.sun_color_reset"
    bl_label = "Reset sun colour"

    def execute(self, context):
        s = context.scene.r5_settings
        s.sky_sun_color = default_sun_color(s)
        return {"FINISHED"}


CLASSES = (R5_OT_setup_sky, R5_OT_sun_color_reset)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
