import json
import math
import os
import re
import time

import bpy
import numpy as np
from bpy.props import CollectionProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Euler, Vector

from . import castfile, gn_instancer, library, lod_system, optimize, placement, regions, visibility, vpk

PLACEMENT_EXT = (".ent", ".json", ".csv", ".mprt", ".nut")


def exclude_regex(text):
    """Whole words separated by _ / - . or digits; '*' = wildcard."""
    words = [w.strip().strip("_/").lower() for w in text.split(",") if w.strip().strip("_/")]
    if not words:
        return None
    parts = [re.escape(w).replace(r"\*", r"[^/]*") for w in words]
    return re.compile(r"(?:^|[/_\-\.\d])(?:" + "|".join(parts) + r")(?=$|[/_\-\.\d])")


def _category(model_path):
    p = model_path.replace("\\", "/").lower()
    if "/" not in p:
        # .mprt: no folder, use the name prefix (desertlands_, indust_...)
        return p.split("_", 1)[0] if "_" in p else "misc"
    parts = p.split("/")
    if parts[0] in ("mdl", "models"):
        parts = parts[1:]
    return parts[0] if len(parts) > 1 else "misc"


def _get_child_collection(parent, name):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    if col.name not in parent.children:
        parent.children.link(col)
    return col


def _resolve_models_dir(s, paths):
    if any(p.lower().endswith(".nut") for p in paths):
        # ReMap maps use props of any map: the chosen models folder(s), separated by ";"
        d = bpy.path.abspath(s.models_dir) if s.models_dir else ""
        dirs = [x.strip() for x in d.split(";") if x.strip() and os.path.isdir(x.strip())]
        if dirs:
            return ";".join(dirs)
        raise RuntimeError("No models for this .nut map: set the Models folder to the '_models' folder of a map "
                           "export (several folders can be separated by ';')")
    d = bpy.path.abspath(s.models_dir) if s.models_dir else ""
    if d and ";" in d:          # several folders (game import: export + fallback caches)
        dirs = [p.strip() for p in d.split(";") if p.strip() and os.path.isdir(p.strip())]
        if dirs:
            return ";".join(dirs)
    if d and os.path.isdir(d):
        return d
    for p in paths:
        cand = os.path.join(os.path.dirname(p), "_models")
        if os.path.isdir(cand):
            s.models_dir = cand
            return cand
    raise RuntimeError("Models folder not found: set it in the R5 Map Loader > Import panel "
                       "(for a Legion+ export, it is the '_models' folder next to the .mprt)")


def _texture_libraries(s):
    """Folders where textures are looked up by material name (materials exported without their textures)."""
    out = []
    d = bpy.path.abspath(s.models_dir) if s.models_dir else ""
    for m in [x.strip() for x in d.split(";") if x.strip()]:
        parent = os.path.dirname(m.rstrip("\\/"))
        out += [m, os.path.join(parent, "_shared_textures"), os.path.join(parent, "_images")]
    return out


def _offset(scene, s, centroid_fn, ground_fn=None):
    """Offset in meters removed from every position (props, geometry, ziplines, POIs): the map centre when
    recentering, and the lowest point of the map in Z so that it sits on the ground grid (Z >= 0). Stored in
    the scene: files imported later in the same scene line up with the first one."""
    if "apx_offset" in scene:
        return Vector(scene["apx_offset"])
    if not s.recenter and not s.lift_above_zero:
        return Vector((0, 0, 0))
    off = centroid_fn() if s.recenter else Vector((0, 0, 0))
    off.z = 0.0
    if s.lift_above_zero and ground_fn is not None:
        older = any(c.name.startswith(("APX_World_", "APX_Map_")) and len(c.all_objects)
                    for c in scene.collection.children)
        if not older:        # (a map imported by an older version stays at its game height: keep them aligned)
            z = ground_fn()
            if z is not None:
                off.z = z
            elif not s.recenter:
                return off   # nothing to measure (a skybox alone): decided by the next map import
    scene["apx_offset"] = tuple(off)
    return off


_ENT_BLOCK_RE = re.compile(r"\{([^{}]*)\}")
_BRUSH_REF_RE = re.compile(r'"model"\s+"\*(\d+)"')
_ORIGIN_RE = re.compile(r'"origin"\s+"([^"]*)"')


_KV_RE = re.compile(r'"([^"]*)"\s+"([^"]*)"')
_ROT_AXES = {"roll": 0, "pitch": 1, "yaw": 2}


class EntInfo:
    """What the add-on reads from every entity file of a map export, besides the props."""

    def __init__(self):
        self.brush = {}         # N -> origin of the entity using brush model "*N" (game units)
        self.rotators = []      # [{origin, angles, axis_index, speed (deg/s), brushes [N], members [origin]}]
        self.lights = []        # [(origin, linear rgb)] light / light_spot entities (the map's real lights)


def _ent_files(paths):
    files = [p for p in paths if p.lower().endswith(".ent")]
    for d in {os.path.join(os.path.dirname(p), "entities") for p in paths}:
        if os.path.isdir(d):
            files += [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(".ent")]
    return list(dict.fromkeys(files))


def _scan_entities(paths):
    """Brush model positions, rotating movers (script_mover_lightweight / script_rotator with what they move) and
    the map's light entities, from the .ent files given and the 'entities' folders next to the files."""
    info = EntInfo()
    by_guid = {}
    rot_raw = []
    for fp in _ent_files(paths):
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for blk in _ENT_BLOCK_RE.finditer(text):
            b = blk.group(1)
            if '"*' not in b and "link_guid" not in b and '"light' not in b:
                continue
            kv = dict(_KV_RE.findall(b))
            model = kv.get("model", "")
            origin = placement._vec(kv.get("origin", "0 0 0"))
            if model.startswith("*") and model[1:].isdigit():
                info.brush.setdefault(int(model[1:]), origin)
            if "link_guid" in kv:
                by_guid[kv["link_guid"]] = kv
            cls = kv.get("classname", "")
            if cls in ("light", "light_spot") and "_light" in kv:
                rgb = placement._vec(kv["_light"], 4, 255.0)[:3]
                lin = tuple(max(0.0, min(1.0, c / 255.0)) ** 2.2 for c in rgb)
                if max(lin) > 0:
                    m = max(lin)
                    info.lights.append((origin, tuple(c / m for c in lin)))
            axis = kv.get("rotation_axis", "").lower()
            if cls == "script_mover_lightweight" and axis in _ROT_AXES:
                try:
                    speed = float(kv.get("rotate_forever_speed", "0"))
                except ValueError:
                    speed = 0.0
                if speed:
                    rot_raw.append((kv, origin, axis, speed))
    for kv, origin, axis, speed in rot_raw:
        r = {"origin": origin, "angles": placement._vec(kv.get("angles", "0 0 0")), "axis_index": _ROT_AXES[axis],
             "speed": speed, "brushes": [], "members": []}
        stack = [v for k, v in kv.items() if k.startswith("link_to_guid")]
        seen = set()
        while stack:
            g = stack.pop()
            if g in seen or g not in by_guid:
                continue
            seen.add(g)
            m = by_guid[g]
            mo = m.get("model", "")
            mpos = placement._vec(m.get("origin", "0 0 0"))
            if mo.startswith("*") and mo[1:].isdigit():
                r["brushes"].append(int(mo[1:]))
            r["members"].append(mpos)
            stack += [v for k, v in m.items() if k.startswith("link_to_guid")]
        info.rotators.append(r)
    return info


def _brush_origins(paths):
    """{N: origin (game units)} of the brush entities ("model" "*N": breakable glass, func_brush, fog volumes,
    triggers) found in the .ent files given and in the 'entities' folders next to the files."""
    return _scan_entities(paths).brush


def _store_entities(scene, info, unit, offset):
    """Rotators and map lights in Blender coordinates, for motion.py and lights.py."""
    from mathutils import Euler
    off = np.array(offset[:], dtype=np.float64)
    rots = []
    for r in info.rotators:
        pitch, yaw, roll = (math.radians(a) for a in r["angles"])
        mat = Euler((roll, pitch, yaw), "XYZ").to_matrix()
        axis = [float(v) for v in mat.col[r["axis_index"]]]
        rots.append({"pivot": list(np.array(r["origin"]) * unit - off), "axis": axis,
                     "rate": r["speed"] / 360.0, "brushes": r["brushes"],
                     "members": [list(np.array(m) * unit - off) for m in r["members"]]})
    if rots:
        scene["apx_rotators"] = json.dumps(rots)
    if info.lights:
        a = np.array([list(np.array(o) * unit - off) + list(c) for o, c in info.lights], dtype=np.float32)
        scene["apx_map_lights"] = a.ravel().tolist()


# Sky / vista models placed far outside the playable map (skybox copies, distant scenery)
_FAR_MODEL_RE = re.compile(r"(^|/)(vistas?|skybox)/|_se$|(^|_)skybox(_|$)")


def _props_min_z(placements, available, unit):
    """Lowest point of every imported prop (Blender units): model bounds turned by the prop's pitch / roll."""
    from . import library
    by_key = {}
    for p in placements:
        key = placement.model_key(p.model)
        if key in available and not (_FAR_MODEL_RE.search(p.model.replace("\\", "/").lower())
                                     or _FAR_MODEL_RE.search(key)):
            by_key.setdefault(key, []).append(p)
    out = []
    for key, ps in by_key.items():
        b = library.model_bounds(key)
        if b is None:
            continue
        lo, hi = b
        a = np.radians(np.array([p.angles for p in ps], dtype=np.float64))     # pitch, yaw, roll
        pitch, roll = a[:, 0], a[:, 2]
        r2 = np.stack([-np.sin(pitch), np.sin(roll) * np.cos(pitch), np.cos(roll) * np.cos(pitch)], axis=1)
        low = np.minimum(r2 * lo, r2 * hi).sum(1)
        z = np.array([p.origin[2] for p in ps]) * unit + np.array([p.scale for p in ps]) * low
        out.append(z.astype(np.float32))
    return np.concatenate(out) if out else np.zeros(0, np.float32)


def ground_level(z, gap=60.0, max_share=0.02):
    """Height of the bottom of the map from height samples (Blender units). Maps keep a few disconnected pieces
    far below the playable area (skybox room, test rooms, a distant backdrop: Olympus has some 400 to 700 m
    under its lowest island): when at most `max_share` of the samples lie under an empty band higher than
    `gap` metres, they are left below."""
    z = np.sort(np.asarray(z, dtype=np.float64))
    if len(z) == 0:
        return None
    d = np.diff(z)
    below = np.arange(1, len(z))                 # samples under each gap
    cut = np.nonzero((d > gap) & (below <= max_share * len(z)))[0]
    return float(z[cut[-1] + 1]) if len(cut) else float(z[0])


def _map_name(path):
    n = os.path.splitext(os.path.basename(path))[0]
    if n.lower().endswith("_skybox"):
        n = n[:-7]
    return n[:-5] if n.lower().endswith("_lod0") else n


def is_skybox_file(path):
    return os.path.basename(path).lower().endswith("_skybox.mprt")


# --------------------------------------------------------------------------
def _world_z(paths, unit, brush):
    zs = []
    for wp in paths:
        cast = castfile.CastFile(wp)
        castfile.place_brush_models(cast, brush)
        zs.append(castfile.world_z_samples(cast, np.eye(3) * unit, brush))
        cast.free()
    return np.concatenate(zs) if zs else np.zeros(0, np.float32)


def import_world(context, cast_path, report=print, brush=None, separate=None):
    scene = context.scene
    s = scene.r5_settings
    name = _map_name(cast_path)
    col = _get_child_collection(scene.collection, f"APX_World_{name}")
    if brush is None:
        brush = _brush_origins([cast_path])
    off = _offset(scene, s, lambda: Vector((0, 0, 0)),
                  lambda: ground_level(_world_z([cast_path], s.unit_scale, brush)))
    xf = np.eye(3) * s.unit_scale
    return castfile.import_world_cast(cast_path, s, col, xf, np.array(off[:], dtype=np.float64), report,
                                      brush_origins=brush, separate=separate)


def import_map(context, paths, report=print):
    scene = context.scene
    s = scene.r5_settings
    t0 = time.time()
    if context.object and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    castfile.clear_image_cache()
    castfile.set_texture_libraries(_texture_libraries(s))

    world_paths = [p for p in paths if p.lower().endswith(".cast")]
    place_paths = [p for p in paths if os.path.splitext(p)[1].lower() in PLACEMENT_EXT]
    # 3D skybox (r5mex: <map>_skybox.mprt, vista props already moved and scaled to where the game shows them)
    sky_paths = [p for p in place_paths if is_skybox_file(p)]
    place_paths = [p for p in place_paths if p not in sky_paths]
    if s.import_skybox:
        for p in place_paths:
            cand = os.path.join(os.path.dirname(p), f"{_map_name(p)}_skybox.mprt")
            if p.lower().endswith(".mprt") and os.path.isfile(cand) and cand not in sky_paths:
                sky_paths.append(cand)
    sky_only = bool(sky_paths) and not place_paths and not world_paths
    if s.import_world and not world_paths:
        # the .cast next to the .mprt, unless another geometry was given (R5Flowstate: a Legion+ export of the
        # map replaces the collision terrain of the cache)
        for p in place_paths:
            if p.lower().endswith(".mprt"):
                cand = os.path.splitext(p)[0] + ".cast"
                if os.path.isfile(cand) and cand not in world_paths:
                    world_paths.append(cand)

    # Entities: .vpk picked directly, or the "Entities" field filled in when loading an .mprt
    vpk_paths = [p for p in paths if p.lower().endswith(".vpk")]
    extra = bpy.path.abspath(s.ent_path) if s.ent_path else ""
    if extra and os.path.isfile(extra) and any(p.lower().endswith(".mprt") for p in place_paths):
        if extra.lower().endswith(".vpk"):
            if extra not in vpk_paths:
                vpk_paths.append(extra)
        elif extra not in place_paths:
            place_paths.append(extra)
    elif not extra and s.auto_entities:
        # RSXreloaded export: <map>_LOD0.mprt + entities/<map>_script.ent (doors, loot bins, evac towers...)
        for p in list(place_paths):
            if p.lower().endswith(".mprt"):
                ent = os.path.join(os.path.dirname(p), "entities", f"{_map_name(p)}_script.ent")
                if os.path.isfile(ent) and ent not in place_paths:
                    place_paths.append(ent)

    # 1) Placements --------------------------------------------------------
    placements, ziplines, zones, sun = [], [], [], {}

    def take(data, label):
        placements.extend(data.placements)
        ziplines.extend(data.ziplines)
        zones.extend(data.zones)
        if data.sun:
            sun.update(data.sun)
        report(f"{label}: {len(data.placements)} props, {len(data.ziplines)} ziplines, {len(data.zones)} zones")

    for p in place_paths:
        if p.lower().endswith(".ent"):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                take(placement.parse_ent_full(f.read()), os.path.basename(p))
        elif p.lower().endswith(".nut"):
            take(placement.parse_nut(p), os.path.basename(p))      # ReMap custom map script
        else:
            placements.extend(placement.load_placements(p))
    kinds = tuple(k.strip().lower() for k in s.ent_files.split(",") if k.strip())
    ent_names = []
    for vp in vpk_paths:
        ents, skipped = vpk.read_ents(vp, kinds)
        for name, text in ents:
            take(placement.parse_ent_full(text), os.path.basename(name))
            ent_names.append(os.path.basename(name))
        for sk in skipped:
            report(f"{os.path.basename(sk)} skipped (compressed in the VPK)")
    if not place_paths and ent_names:
        place_paths = [os.path.join(os.path.dirname(vpk_paths[0]), ent_names[0])]
    # editor helpers of the entity files (editor_ref, editor_traverse, mp_titan_spawn...): invisible in game
    placements = [p for p in placements
                  if p.classname == "prop_static" or not p.model.replace("\\", "/").lower().startswith("mdl/dev/")]
    total_raw = len(placements)
    excl_re = exclude_regex(s.exclude_patterns)
    if excl_re is not None:
        placements = [p for p in placements if not excl_re.search(p.model.lower())]
    if s.use_region:
        c = Vector(s.region_center)
        r2 = s.region_radius ** 2
        placements = [p for p in placements if (Vector(p.origin) - c).length_squared <= r2]
    if place_paths and not placements and not world_paths:
        raise RuntimeError(f"No prop to import ({total_raw} read before filtering)")

    unit = s.unit_scale

    def centroid():
        if placements:
            a = np.array([p.origin for p in placements], dtype=np.float64)
            return Vector(a.mean(axis=0) * unit)
        return Vector((0, 0, 0))

    # entities: brush model positions (their meshes are exported around the origin), rotating movers, lights
    ents = _scan_entities(world_paths + place_paths) if (world_paths or place_paths) else EntInfo()
    brush = ents.brush

    sky_pl = [p for sp in sky_paths for p in placement.parse_mprt(sp)]
    created, available, missing = 0, {}, []
    wm = context.window_manager
    if placements or sky_pl:
        # 2) Unique models ------------------------------------------------
        models_dir = _resolve_models_dir(s, place_paths or sky_paths)
        index = library.build_index(models_dir)
        keys = sorted({placement.model_key(p.model) for p in placements + sky_pl})
        wm.progress_begin(0, len(keys) + 1)
        for i, key in enumerate(keys):
            wm.progress_update(i)
            files = index.get(key)
            lods = library.ensure_model(key, files, s, scene) if files else []
            if lods:
                available[key] = lods
            else:
                missing.append(key)
            if i % 50 == 0:
                print(f"[R5] models {i}/{len(keys)}")
        wm.progress_end()
        if s.dedupe_on_import:
            optimize.dedupe_materials()
            optimize.dedupe_images()

    def ground():
        return ground_level(np.concatenate([_world_z(world_paths, unit, brush),
                                            _props_min_z(placements, available, unit)]))
    had_offset = "apx_offset" in scene
    offset = _offset(scene, s, centroid, ground)
    if not had_offset and abs(offset.z) > 1e-3:
        report(f"Map moved {-offset.z:+.1f} m in Z: its lowest point sits on the ground grid (Z = 0)")
    if not sky_only:
        _store_entities(scene, ents, unit, offset)

    # 3) Map geometry ------------------------------------------------------
    rotor_brushes = {n for r in ents.rotators for n in r["brushes"]}
    for wp in world_paths:
        import_world(context, wp, report, brush, rotor_brushes)

    if placements:
        # 4) Instances ----------------------------------------------------
        map_name = _map_name(place_paths[0])
        map_col = _get_child_collection(scene.collection, f"APX_Map_{map_name}")
        if s.instancing_mode == "GN":
            created = _instances_gn(scene, placements, available, map_col, map_name, unit, offset)
        else:
            created = _instances_empty(placements, available, map_col, map_name, unit, offset, s, wm)

        if missing:
            txt = bpy.data.texts.get("APX_missing_models") or bpy.data.texts.new("APX_missing_models")
            txt.clear()
            txt.write("Models not found in the models folder (export them with RSX):\n")
            paths_by_key = {}
            for p in placements:
                paths_by_key.setdefault(placement.model_key(p.model), p.model)
            txt.write("\n".join(paths_by_key.get(k, k) for k in missing))

    n_sky = 0
    if sky_pl:
        # skybox: its own collection, always LOD0, never hidden by the culling or the regions (gn_instancer)
        sky_name = _map_name(sky_paths[0])
        sky_col = _get_child_collection(scene.collection, f"APX_Sky_{sky_name}")
        groups = {}
        for p in sky_pl:
            key = placement.model_key(p.model)
            if key in available:
                groups.setdefault(key, []).append(_xf(p, unit, offset))
        for key, items in groups.items():
            o = gn_instancer.create_instancer(key, available[key], items, sky_col, scene, sky=True)
            n_sky += len(items)
            far = max(float(np.linalg.norm(np.array(it[0][:]))) + library.model_radius(key) * it[2] for it in items)
            o["apx_sky_far"] = far
        report(f"Skybox: {n_sky} vista props ({os.path.basename(sky_paths[0])})")

    if ziplines and s.import_ziplines:
        map_name = _map_name(place_paths[0]) if place_paths else "map"
        map_col = _get_child_collection(scene.collection, f"APX_Map_{map_name}")
        _create_ziplines(ziplines, map_col, map_name, unit, offset)

    n_poi = regions.add_zones(scene, zones, unit, offset) if zones else 0
    if sun:
        s.map_sun_json = json.dumps(sun)
        from . import sky, properties as _props
        s.sky_sun_color = sky.default_sun_color(s)
        if s.sky_use_map_sun:
            _props.sync_sun_sliders(s)        # the sliders start from the map's sun direction
    regions.get_region(scene)
    regions.ensure_clip_modifiers(scene)

    if s.setup_viewport_on_import:
        optimize.setup_viewport_for_large_map()
        if s.cast_load_textures:
            lim = optimize.apply_texture_limit(s.texture_limit)
            report(f"Viewport texture limit: {lim.replace('CLAMP_', '').replace('OFF', 'none')}")
    if n_sky:
        far = fit_clip_to_sky(context, scene)
        if far:
            report(f"View / camera clip end raised to {far / 1000:.0f} km for the skybox")

    if placements and created:
        _frame_views(context, placements, unit, offset)
    visibility.apply(scene)
    try:
        from . import motion
        motion.after_import(scene)          # movers / animated shaders / LOD fade already switched on
    except Exception as exc:  # noqa: BLE001
        report(f"Movers / shaders: {exc}")
    context.view_layer.update()
    lod_system.apply_all(scene)

    msg = (f"{created} props{f' + {n_sky} skybox' if n_sky else ''}, {len(available)} unique models, "
           f"{len(missing)} missing"
           f"{', geometry imported' if world_paths else ''}{f', {n_poi} POIs' if n_poi else ''}"
           f" ({time.time() - t0:.1f}s)")
    report(msg)
    if s.instancing_mode != "GN" and created > 100000:
        # Measured on World's Edge (433k empties): LODs update but Blender draws ~0.8 fps with that many objects
        report(f"WARNING: {created} separate objects make the viewport very slow (~1 fps). Use Geometry Nodes "
               f"instancing for a full map, or 'Import an area only' for this mode")
    return created, len(available), missing


def fit_clip_to_sky(context, scene):
    """The 3D skybox lies tens of km away (the vista models are scaled x1000): push the clip end of the 3D views
    and of the scene camera past it. Returns the new clip end (m) or 0."""
    far = max((float(o.get("apx_sky_far", 0.0)) for o in scene.objects if o.get("apx_sky")), default=0.0)
    if far <= 0:
        return 0.0
    end = far * 1.25
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                sp = area.spaces.active
                if sp.clip_end < end:
                    sp.clip_end = end
                    if sp.clip_start < 1.0:
                        sp.clip_start = 1.0       # keeps the depth precision over such a range
    if scene.camera is not None and scene.camera.type == "CAMERA" and scene.camera.data.clip_end < end:
        scene.camera.data.clip_end = end
    return end


def _frame_views(context, placements, unit, offset):
    """Point the 3D views at the imported props when they are not looking at them. ReMap maps (.nut) are
    often built far from the origin (1 km up in the sky): the view stayed on an empty grid, and as the
    off-screen props are not instanced, View All could not find them either."""
    wm = context.window_manager
    if bpy.app.background or not wm.windows:
        return
    a = np.array([p.origin for p in placements], dtype=np.float64) * unit - np.array(offset[:])
    lo, hi = np.percentile(a, 2, axis=0), np.percentile(a, 98, axis=0)
    center = (lo + hi) / 2
    size = float(np.linalg.norm(hi - lo))
    margin = max(size * 0.1, 20.0)
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type != "VIEW_3D":
                continue
            sp = area.spaces.active
            r3d = sp.region_3d
            v = np.array(r3d.view_location[:])
            if np.all(v >= lo - margin) and np.all(v <= hi + margin):
                continue            # already looking inside the imported area
            r3d.view_location = Vector(center)
            r3d.view_distance = min(max(size * 0.6, 30.0), 4000.0)
            if sp.clip_end < r3d.view_distance * 4:
                sp.clip_end = r3d.view_distance * 4
            area.tag_redraw()


ZIPLINE_YELLOW =(1.0, 0.62, 0.02, 1.0)      # linear; the yellow cables of the game (sRGB ~ #FFCF1A)


def zipline_material():
    """Yellow cable material (Solid colour and Principled BSDF), also fixes older grey ones."""
    mat = bpy.data.materials.get("R5_zipline_cable") or bpy.data.materials.new("R5_zipline_cable")
    mat.diffuse_color = ZIPLINE_YELLOW
    if not mat.get("r5_zip_v2"):
        mat.use_nodes = True
        bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = ZIPLINE_YELLOW
            bsdf.inputs["Roughness"].default_value = 0.45
            if "Metallic" in bsdf.inputs:
                bsdf.inputs["Metallic"].default_value = 0.0
        mat["r5_zip_v2"] = True
    return mat


def _create_ziplines(ziplines, map_col, map_name, unit, offset):
    col = _get_child_collection(map_col, f"{map_name}_ziplines")
    cu = bpy.data.curves.new(f"{map_name}_ziplines", "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 0.025
    cu.bevel_resolution = 1
    off = (offset.x, offset.y, offset.z)
    for pts in ziplines:
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        for i, p in enumerate(pts):
            sp.points[i].co = (p[0] * unit - off[0], p[1] * unit - off[1], p[2] * unit - off[2], 1.0)
    cu.materials.append(zipline_material())
    obj = bpy.data.objects.new(f"{map_name}_ziplines", cu)
    col.objects.link(obj)
    return obj


def _xf(p, unit, offset):
    pitch, yaw, roll = (math.radians(a) for a in p.angles)
    # Source/Apex: R = Rz(yaw) * Ry(pitch) * Rx(roll)  ==  Blender XYZ Euler (roll, pitch, yaw)
    loc = (p.origin[0] * unit - offset.x, p.origin[1] * unit - offset.y, p.origin[2] * unit - offset.z)
    return loc, (roll, pitch, yaw), p.scale


def _instances_gn(scene, placements, available, map_col, map_name, unit, offset):
    groups = {}
    for p in placements:
        key = placement.model_key(p.model)
        if key in available:
            groups.setdefault(key, []).append(_xf(p, unit, offset))
    col = _get_child_collection(map_col, f"{map_name}_props")
    n = 0
    for key, items in groups.items():
        gn_instancer.create_instancer(key, available[key], items, col, scene)
        n += len(items)
    return n


def _instances_empty(placements, available, map_col, map_name, unit, offset, s, wm=None):
    # Blender keeps bpy.data.objects sorted by name: creating "a", "z", "a.001", "m"... inserts in the
    # middle of the list every time (quadratic, ~15 min for World's Edge). Creating the empties in name
    # order always appends at the end (linear, ~25 s for 433k props).
    keyed = sorted(((placement.model_key(p.model), p) for p in placements), key=lambda kp: kp[0])
    keyed = [(k, p) for k, p in keyed if available.get(k)]
    cat_cols = {}
    created = 0
    total = len(keyed)
    if wm is not None:
        wm.progress_begin(0, max(total, 1))
    t0 = time.time()
    for key, p in keyed:
        lods = available[key]
        if created % 20000 == 0 and created:
            if wm is not None:
                wm.progress_update(created)
            print(f"[R5] empties {created}/{total} ({time.time() - t0:.0f}s)")
        if s.split_by_category:
            cat = _category(p.model)
            col = cat_cols.get(cat)
            if col is None:
                col = cat_cols[cat] = _get_child_collection(map_col, f"{map_name}_{cat}")
        else:
            col = map_col
        loc, rot, scl = _xf(p, unit, offset)
        o = bpy.data.objects.new(key, None)
        o.instance_type = "COLLECTION"
        o.instance_collection = lods[0]
        o.empty_display_size = 0.25
        o.location = loc
        o.rotation_euler = Euler(rot, "XYZ")
        o.scale = (scl, scl, scl)
        o["apx_model"] = key
        o["apx_path"] = p.model
        if p.name:
            o["apx_targetname"] = p.name
        col.objects.link(o)
        created += 1
    if wm is not None:
        wm.progress_end()
    return created


# --------------------------------------------------------------------------
class R5_OT_import_map(bpy.types.Operator, ImportHelper):
    """Load an R5 / Apex map: placements (.mprt/.ent/.vpk/.json/.csv) and/or geometry (.cast)"""
    bl_idname = "r5.import_map"
    bl_label = "Load R5 map"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".mprt"
    filter_glob: StringProperty(default="*.mprt;*.cast;*.ent;*.vpk;*.json;*.csv;*.nut", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        col.prop(s, "models_dir")
        col.label(text="(empty = '_models' folder next to the file)")
        col.prop(s, "instancing_mode")
        col.prop(s, "auto_entities")
        col.prop(s, "ent_path")
        col.prop(s, "ent_files")
        col.prop(s, "import_world")
        col.prop(s, "import_skybox")
        col.prop(s, "world_split")
        col.prop(s, "unit_scale")
        col.prop(s, "axis_correction")
        col.prop(s, "use_lod")
        col.prop(s, "max_lods")
        col.prop(s, "auto_generate_lods")
        col.prop(s, "join_meshes")
        col.prop(s, "cast_load_textures")
        col.prop(s, "lift_above_zero")
        col.prop(s, "recenter")

    def execute(self, context):
        if self.files and self.files[0].name:
            paths = [os.path.join(self.directory, f.name) for f in self.files]
        else:
            paths = [self.filepath]
        try:
            _c, _m, missing = import_map(context, paths, report=lambda m: self.report({"INFO"}, m))
        except (RuntimeError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if missing:
            self.report({"WARNING"}, f"{len(missing)} models not found: see the 'APX_missing_models' text")
        return {"FINISHED"}


class R5_OT_update_lods(bpy.types.Operator):
    """Recompute the LODs now"""
    bl_idname = "r5.update_lods"
    bl_label = "Update LODs"

    def execute(self, context):
        n = lod_system.apply_all(context.scene)
        self.report({"INFO"}, f"{n} items updated")
        return {"FINISHED"}


class R5_OT_show_all(bpy.types.Operator):
    """Show every prop in LOD0 (cancels culling and auto update)"""
    bl_idname = "r5.show_all"
    bl_label = "Show everything in LOD0"

    def execute(self, context):
        s = context.scene.r5_settings
        s.lod_auto_update = False
        lod_system.rebuild(context.scene)
        n = lod_system.apply_forced(context.scene, 0)
        n += gn_instancer.apply_settings(context.scene, force_level=0)
        self.report({"INFO"}, f"{n} items in LOD0 (auto update disabled)")
        return {"FINISHED"}


class R5_OT_make_real(bpy.types.Operator):
    """Turn the selected instances (Empties mode) into real objects"""
    bl_idname = "r5.make_real"
    bl_label = "Make real (selection)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any("apx_model" in o for o in context.selected_objects)

    def execute(self, context):
        sel = [o for o in context.selected_objects if "apx_model" in o]
        for o in sel:
            o.hide_viewport = False
            o.instance_type = "COLLECTION"     # culled empties have their instancing switched off
        bpy.ops.object.duplicates_make_real(use_base_parent=False, use_hierarchy=False)
        for o in sel:
            bpy.data.objects.remove(o, do_unlink=True)
        lod_system.invalidate()
        return {"FINISHED"}


def menu_import(self, context):
    self.layout.operator(R5_OT_import_map.bl_idname, text="R5 Map Loader (.mprt/.cast/.ent/.vpk/.nut)")


class R5_OT_link_materials(bpy.types.Operator):
    """Show the materials of the props in the Material tab: each instancer (one object for every copy of a model)
    gets its model's materials. Editing them changes every copy of the model"""
    bl_idname = "r5.link_materials"
    bl_label = "Show prop materials"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        objs = [o for o in context.scene.objects if "apx_gn" in o and o.type == "MESH"]
        n = sum(1 for o in objs if gn_instancer.link_model_materials(o) >= 0)
        self.report({"INFO"}, f"{n} prop models: select one, then open the Material tab")
        return {"FINISHED"}


CLASSES = (R5_OT_import_map, R5_OT_update_lods, R5_OT_show_all, R5_OT_make_real, R5_OT_link_materials)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)


def unregister():
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    except Exception:  # noqa: BLE001
        pass
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
