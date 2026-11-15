"""Sky / skybox for the map.

What the game data gives us: in R5Reloaded VPKs, the entities that define the sky and the sun
(light_environment, sky_camera...) are in <map>_env.ent, which is LZHAM-compressed, and the BSP
lumps are compressed too. When an uncompressed env .ent is provided, its light_environment is
used for the sun direction and color. Otherwise three sources are available:
  - PROCEDURAL: Blender's physical sky (Nishita) + a sun lamp,
  - IMAGE: an equirectangular panorama / HDRI,
  - CUBEMAP: 6 face images (e.g. a sky cubemap exported with RSX: name_000..name_005,
    or px/nx/py/ny/pz/nz, right/left/up/down/front/back) converted to a panorama.
"""

import json
import math
import os

import bpy
import numpy as np
from mathutils import Euler

WORLD = "R5_Sky"
SUN = "R5_Sun"
PANO = "R5_Sky_Panorama"


def _world(scene):
    w = bpy.data.worlds.get(WORLD) or bpy.data.worlds.new(WORLD)
    w.use_nodes = True
    scene.world = w
    nt = w.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld"); out.location = (400, 0)
    bg = nt.nodes.new("ShaderNodeBackground"); bg.location = (200, 0)
    nt.links.new(bg.outputs[0], out.inputs["Surface"])
    return w, nt, bg


def _sun(scene, elevation, azimuth, strength, color=(1.0, 0.96, 0.9)):
    sun = bpy.data.objects.get(SUN)
    if sun is None:
        sun = bpy.data.objects.new(SUN, bpy.data.lights.new(SUN, "SUN"))
    if sun.name not in scene.collection.objects:
        scene.collection.objects.link(sun)
    sun.data.energy = strength
    sun.data.color = color
    sun.data.angle = math.radians(0.6)
    # A sun lamp shines along its local -Z: tilt it down from the zenith
    sun.rotation_euler = Euler((math.radians(90.0) - elevation, 0.0, azimuth + math.radians(90.0)), "XYZ")
    return sun


def sun_from_entities(sun_ent):
    """light_environment -> (elevation, azimuth, color) in radians, or None."""
    if not sun_ent:
        return None
    ang = [float(v) for v in sun_ent.get("angles", "0 0 0").split()[:3]] + [0, 0, 0]
    pitch = float(sun_ent.get("pitch", ang[0]))
    yaw = ang[1]
    col = (1.0, 0.96, 0.9)
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


def setup_procedural(scene):
    s = scene.apx_settings
    w, nt, bg = _world(scene)
    elev, azim = math.radians(s.sky_sun_elevation), math.radians(s.sky_sun_rotation)
    color = (1.0, 0.96, 0.9)
    if s.sky_use_map_sun and s.map_sun_json:
        got = sun_from_entities(json.loads(s.map_sun_json))
        if got:
            elev, azim, color = got
    sky = nt.nodes.new("ShaderNodeTexSky"); sky.location = (0, 0)
    try:
        sky.sky_type = "NISHITA"
        sky.sun_elevation = elev
        sky.sun_rotation = azim
        sky.altitude = 500.0
        sky.air_density = 1.0
        sky.dust_density = s.sky_haze
        sky.sun_disc = True
    except (AttributeError, TypeError):
        pass
    nt.links.new(sky.outputs[0], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = s.sky_strength
    _sun(scene, elev, azim, s.sky_sun_strength, color)
    return w


def setup_image(scene, path):
    s = scene.apx_settings
    w, nt, bg = _world(scene)
    img = bpy.data.images.load(path, check_existing=True)
    env = nt.nodes.new("ShaderNodeTexEnvironment"); env.location = (-100, 0)
    env.image = img
    tc = nt.nodes.new("ShaderNodeTexCoord"); tc.location = (-500, 0)
    mp = nt.nodes.new("ShaderNodeMapping"); mp.location = (-300, 0)
    mp.inputs["Rotation"].default_value[2] = math.radians(s.sky_image_rotation)
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs[0], env.inputs["Vector"])
    nt.links.new(env.outputs[0], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = s.sky_strength
    if s.sky_add_sun:
        _sun(scene, math.radians(s.sky_sun_elevation), math.radians(s.sky_sun_rotation), s.sky_sun_strength)
    return w


# --------------------------------------------------------------------------
# Cubemap -> panorama
# --------------------------------------------------------------------------
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
    ext_ok = (".png", ".jpg", ".jpeg", ".tga", ".dds", ".exr", ".hdr", ".tif", ".tiff", ".bmp")
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
    s = scene.apx_settings
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
    world, nt, bg = _world(scene)
    env = nt.nodes.new("ShaderNodeTexEnvironment"); env.location = (-100, 0)
    env.image = img
    nt.links.new(env.outputs[0], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = s.sky_strength
    if s.sky_add_sun:
        _sun(scene, math.radians(s.sky_sun_elevation), math.radians(s.sky_sun_rotation), s.sky_sun_strength)
    return world


# --------------------------------------------------------------------------
# Operator
# --------------------------------------------------------------------------
class R5_OT_setup_sky(bpy.types.Operator):
    """Create the world sky (and a sun lamp) for the map"""
    bl_idname = "apx.setup_sky"
    bl_label = "Apply sky"

    def execute(self, context):
        scene = context.scene
        s = scene.apx_settings
        try:
            if s.sky_mode == "PROCEDURAL":
                setup_procedural(scene)
            elif s.sky_mode == "IMAGE":
                path = bpy.path.abspath(s.sky_image)
                if not os.path.isfile(path):
                    raise ValueError("Choose a panorama / HDRI image")
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


def register():
    bpy.utils.register_class(R5_OT_setup_sky)


def unregister():
    bpy.utils.unregister_class(R5_OT_setup_sky)
