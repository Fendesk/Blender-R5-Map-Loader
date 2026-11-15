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

PLACEMENT_EXT = (".ent", ".json", ".csv", ".mprt")


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
    d = bpy.path.abspath(s.models_dir) if s.models_dir else ""
    if d and os.path.isdir(d):
        return d
    for p in paths:
        cand = os.path.join(os.path.dirname(p), "_models")
        if os.path.isdir(cand):
            s.models_dir = cand
            return cand
    raise RuntimeError("Models folder not found: set it in the R5 Map Loader > Import panel "
                       "(for a Legion+ export, it is the '_models' folder next to the .mprt)")


def _offset(scene, s, centroid_fn):
    """Recentering offset in meters, shared by props and geometry."""
    if not s.recenter:
        return Vector((0, 0, 0))
    if "apx_offset" in scene:
        return Vector(scene["apx_offset"])
    off = centroid_fn()
    off.z = 0.0
    scene["apx_offset"] = tuple(off)
    return off


def _map_name(path):
    n = os.path.splitext(os.path.basename(path))[0]
    return n[:-5] if n.lower().endswith("_lod0") else n


# --------------------------------------------------------------------------
def import_world(context, cast_path, report=print):
    scene = context.scene
    s = scene.r5_settings
    name = _map_name(cast_path)
    col = _get_child_collection(scene.collection, f"APX_World_{name}")
    off = _offset(scene, s, lambda: Vector((0, 0, 0)))
    xf = np.eye(3) * s.unit_scale
    return castfile.import_world_cast(cast_path, s, col, xf, np.array(off[:], dtype=np.float64), report)


def import_map(context, paths, report=print):
    scene = context.scene
    s = scene.r5_settings
    t0 = time.time()
    if context.object and context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    castfile.clear_image_cache()

    world_paths = [p for p in paths if p.lower().endswith(".cast")]
    place_paths = [p for p in paths if os.path.splitext(p)[1].lower() in PLACEMENT_EXT]
    if s.import_world:
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
    offset = _offset(scene, s, centroid)

    # 2) Map geometry ------------------------------------------------------
    for wp in world_paths:
        import_world(context, wp, report)

    created, available, missing = 0, {}, []
    if placements:
        # 3) Unique models ------------------------------------------------
        models_dir = _resolve_models_dir(s, place_paths)
        index = library.build_index(models_dir)
        keys = sorted({placement.model_key(p.model) for p in placements})
        wm = context.window_manager
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

    if ziplines and s.import_ziplines:
        map_name = _map_name(place_paths[0]) if place_paths else "map"
        map_col = _get_child_collection(scene.collection, f"APX_Map_{map_name}")
        _create_ziplines(ziplines, map_col, map_name, unit, offset)

    n_poi = regions.add_zones(scene, zones, unit, offset) if zones else 0
    if sun:
        s.map_sun_json = json.dumps(sun)
        from . import sky
        s.sky_sun_color = sky.default_sun_color(s)
    regions.get_region(scene)
    regions.ensure_clip_modifiers(scene)

    if s.setup_viewport_on_import:
        optimize.setup_viewport_for_large_map()
        if s.cast_load_textures:
            lim = optimize.apply_texture_limit(s.texture_limit)
            report(f"Viewport texture limit: {lim.replace('CLAMP_', '').replace('OFF', 'none')}")

    visibility.apply(scene)
    context.view_layer.update()
    lod_system.apply_all(scene)

    msg = (f"{created} props, {len(available)} unique models, {len(missing)} missing"
           f"{', geometry imported' if world_paths else ''}{f', {n_poi} POIs' if n_poi else ''}"
           f" ({time.time() - t0:.1f}s)")
    report(msg)
    if s.instancing_mode != "GN" and created > 100000:
        # Measured on World's Edge (433k empties): LODs update but Blender draws ~0.8 fps with that many objects
        report(f"WARNING: {created} separate objects make the viewport very slow (~1 fps). Use Geometry Nodes "
               f"instancing for a full map, or 'Import an area only' for this mode")
    return created, len(available), missing


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
    mat = bpy.data.materials.get("R5_zipline_cable") or bpy.data.materials.new("R5_zipline_cable")
    mat.diffuse_color = (0.05, 0.05, 0.05, 1.0)
    cu.materials.append(mat)
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
    filter_glob: StringProperty(default="*.mprt;*.cast;*.ent;*.vpk;*.json;*.csv", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        col.prop(s, "models_dir")
        col.label(text="(empty = '_models' folder next to the file)")
        col.prop(s, "instancing_mode")
        col.prop(s, "ent_path")
        col.prop(s, "ent_files")
        col.prop(s, "import_world")
        col.prop(s, "world_split")
        col.prop(s, "unit_scale")
        col.prop(s, "axis_correction")
        col.prop(s, "use_lod")
        col.prop(s, "max_lods")
        col.prop(s, "auto_generate_lods")
        col.prop(s, "join_meshes")
        col.prop(s, "cast_load_textures")
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
    self.layout.operator(R5_OT_import_map.bl_idname, text="R5 Map Loader (.mprt/.cast/.ent/.vpk)")


CLASSES = (R5_OT_import_map, R5_OT_update_lods, R5_OT_show_all, R5_OT_make_real)


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
