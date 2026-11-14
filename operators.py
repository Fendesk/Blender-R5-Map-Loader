import math
import os
import re
import time

import bpy
import numpy as np
from bpy.props import CollectionProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Euler, Vector

from . import castfile, gn_instancer, library, lod_system, optimize, placement, visibility, vpk

PLACEMENT_EXT = (".ent", ".json", ".csv", ".mprt")


def exclude_regex(text):
    """Mots entiers séparés par _ / - . ou chiffres ; '*' = joker."""
    words = [w.strip().strip("_/").lower() for w in text.split(",") if w.strip().strip("_/")]
    if not words:
        return None
    parts = [re.escape(w).replace(r"\*", r"[^/]*") for w in words]
    return re.compile(r"(?:^|[/_\-\.\d])(?:" + "|".join(parts) + r")(?=$|[/_\-\.\d])")


def _category(model_path):
    p = model_path.replace("\\", "/").lower()
    if "/" not in p:
        # .mprt : pas de dossier, on prend le préfixe du nom (desertlands_, indust_...)
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
    raise RuntimeError("Dossier des modèles introuvable : renseigne-le dans le panneau Apex Map > Import "
                       "(pour un export Legion+, c'est le dossier '_models' à côté du .mprt)")


def _offset(scene, s, centroid_fn):
    """Décalage de recentrage en mètres, partagé entre props et géométrie."""
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
    s = scene.apx_settings
    unit = s.unit_scale
    name = _map_name(cast_path)
    col = _get_child_collection(scene.collection, f"APX_World_{name}")
    # le centroïde de la géométrie n'est connu qu'après lecture : on retombe sur 0 si besoin
    off = _offset(scene, s, lambda: Vector((0, 0, 0)))
    xf = np.eye(3) * unit
    objs = castfile.import_world_cast(cast_path, s, col, xf, np.array(off[:], dtype=np.float64), report)
    return objs


def import_map(context, paths, report=print):
    scene = context.scene
    s = scene.apx_settings
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

    # Entités : .vpk choisis directement, ou champ "Entités" rempli et import d'un .mprt
    vpk_paths = [p for p in paths if p.lower().endswith(".vpk")]
    extra = bpy.path.abspath(s.ent_path) if s.ent_path else ""
    if extra and os.path.isfile(extra) and any(p.lower().endswith(".mprt") for p in place_paths):
        if extra.lower().endswith(".vpk"):
            if extra not in vpk_paths:
                vpk_paths.append(extra)
        elif extra not in place_paths:
            place_paths.append(extra)

    # 1) Placements --------------------------------------------------------
    placements, ziplines = [], []
    for p in place_paths:
        if p.lower().endswith(".ent"):
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                pl, zl = placement.parse_ent_text(f.read())
            placements.extend(pl)
            ziplines.extend(zl)
        else:
            placements.extend(placement.load_placements(p))
    kinds = tuple(k.strip().lower() for k in s.ent_files.split(",") if k.strip())
    ent_names = []
    for vp in vpk_paths:
        ents, skipped = vpk.read_ents(vp, kinds)
        for name, text in ents:
            pl, zl = placement.parse_ent_text(text)
            placements.extend(pl)
            ziplines.extend(zl)
            ent_names.append(os.path.basename(name))
            report(f"{os.path.basename(name)} : {len(pl)} props, {len(zl)} tyroliennes")
        for sk in skipped:
            report(f"{os.path.basename(sk)} ignoré (compressé dans le VPK)")
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
    if place_paths and not placements:
        raise RuntimeError(f"Aucun prop à importer ({total_raw} lus avant filtrage)")

    unit = s.unit_scale

    def centroid():
        if placements:
            a = np.array([p.origin for p in placements], dtype=np.float64)
            return Vector(a.mean(axis=0) * unit)
        return Vector((0, 0, 0))
    offset = _offset(scene, s, centroid)

    # 2) Géométrie de la map ----------------------------------------------
    for wp in world_paths:
        import_world(context, wp, report)

    created, available, missing = 0, {}, []
    if placements:
        # 3) Modèles uniques ----------------------------------------------
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
                print(f"[APX] modèles {i}/{len(keys)}")
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
            created = _instances_empty(placements, available, map_col, map_name, unit, offset, s)

        if missing:
            txt = bpy.data.texts.get("APX_missing_models") or bpy.data.texts.new("APX_missing_models")
            txt.clear()
            txt.write("Modèles introuvables dans le dossier des modèles (à exporter avec RSX) :\n")
            paths_by_key = {}
            for p in placements:
                paths_by_key.setdefault(placement.model_key(p.model), p.model)
            txt.write("\n".join(paths_by_key.get(k, k) for k in missing))

    if ziplines and s.import_ziplines:
        map_name = _map_name(place_paths[0]) if place_paths else "map"
        map_col = _get_child_collection(scene.collection, f"APX_Map_{map_name}")
        _create_ziplines(ziplines, map_col, map_name, unit, offset)

    if s.setup_viewport_on_import:
        optimize.setup_viewport_for_large_map()

    visibility.apply(scene)
    context.view_layer.update()
    lod_system.apply_all(scene)

    msg = (f"{created} props, {len(available)} modèles uniques, {len(missing)} manquants"
           f"{', géométrie importée' if world_paths else ''} ({time.time() - t0:.1f}s)")
    report(msg)
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
    mat = bpy.data.materials.get("APX_zipline_cable") or bpy.data.materials.new("APX_zipline_cable")
    mat.diffuse_color = (0.05, 0.05, 0.05, 1.0)
    cu.materials.append(mat)
    obj = bpy.data.objects.new(f"{map_name}_ziplines", cu)
    col.objects.link(obj)
    return obj


def _xf(p, unit, offset):
    pitch, yaw, roll = (math.radians(a) for a in p.angles)
    # Source/Apex : R = Rz(yaw) * Ry(pitch) * Rx(roll)  ==  Euler XYZ de Blender (roll, pitch, yaw)
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


def _instances_empty(placements, available, map_col, map_name, unit, offset, s):
    cat_cols = {}
    created = 0
    for p in placements:
        key = placement.model_key(p.model)
        lods = available.get(key)
        if not lods:
            continue
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
    return created


# --------------------------------------------------------------------------
class APX_OT_import_map(bpy.types.Operator, ImportHelper):
    """Importe une map Apex Legends : placements (.mprt/.ent/.json/.csv) et/ou géométrie (.cast)"""
    bl_idname = "apx.import_map"
    bl_label = "Importer une map Apex"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".mprt"
    filter_glob: StringProperty(default="*.mprt;*.cast;*.ent;*.vpk;*.json;*.csv", options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})

    def draw(self, context):
        s = context.scene.apx_settings
        col = self.layout.column()
        col.prop(s, "models_dir")
        col.label(text="(vide = dossier '_models' à côté du fichier)")
        col.prop(s, "instancing_mode")
        col.prop(s, "ent_path")
        col.prop(s, "ent_files")
        col.prop(s, "import_world")
        col.prop(s, "world_tile_size")
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
            self.report({"WARNING"}, f"{len(missing)} modèles introuvables : voir le texte 'APX_missing_models'")
        return {"FINISHED"}


class APX_OT_update_lods(bpy.types.Operator):
    """Recalcule immédiatement les LODs"""
    bl_idname = "apx.update_lods"
    bl_label = "Mettre à jour les LODs"

    def execute(self, context):
        n = lod_system.apply_all(context.scene)
        self.report({"INFO"}, f"{n} éléments mis à jour")
        return {"FINISHED"}


class APX_OT_show_all(bpy.types.Operator):
    """Affiche tous les props en LOD0 (annule le culling)"""
    bl_idname = "apx.show_all"
    bl_label = "Tout afficher en LOD0"

    def execute(self, context):
        s = context.scene.apx_settings
        s.lod_auto_update = False
        lod_system.rebuild(context.scene)
        n = lod_system.apply_forced(context.scene, 0)
        n += gn_instancer.apply_settings(context.scene, force_level=0)
        self.report({"INFO"}, f"{n} éléments en LOD0 (mise à jour auto désactivée)")
        return {"FINISHED"}


class APX_OT_optimize(bpy.types.Operator):
    """Dédoublonne matériaux/images, fusionne les sommets et purge les données orphelines"""
    bl_idname = "apx.optimize"
    bl_label = "Optimiser la scène"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        s = context.scene.apx_settings
        m = optimize.dedupe_materials()
        i = optimize.dedupe_images()
        v = optimize.merge_library_vertices(s.merge_distance) if s.merge_distance > 0 else 0
        p = optimize.purge_orphans()
        lod_system.invalidate()
        self.report({"INFO"}, f"Matériaux fusionnés: {m}, images: {i}, sommets: {v}, données purgées: {p}")
        return {"FINISHED"}


class APX_OT_texture_limit(bpy.types.Operator):
    """Limite la résolution des textures dans le viewport (préférence Blender : Viewport > Texture Limit).
Évite que la mémoire vidéo sature et que des textures ne s'affichent pas"""
    bl_idname = "apx.texture_limit"
    bl_label = "Appliquer la limite de textures"

    def execute(self, context):
        s = context.scene.apx_settings
        context.preferences.system.gl_texture_limit = s.texture_limit
        if hasattr(context.scene, "cycles"):
            size = {"CLAMP_OFF": 0, "CLAMP_2048": 2048, "CLAMP_1024": 1024,
                    "CLAMP_512": 512, "CLAMP_256": 256}[s.texture_limit]
            try:
                context.scene.render.use_simplify = size > 0 or context.scene.render.use_simplify
                context.scene.cycles.texture_limit = str(size) if size else "OFF"
            except (AttributeError, TypeError):
                pass
        self.report({"INFO"}, f"Limite de textures viewport : {s.texture_limit.replace('CLAMP_', '')}")
        return {"FINISHED"}


class APX_OT_select_group(bpy.types.Operator):
    """Sélectionne les objets d'un groupe (triggers, outils...) visibles dans la scène"""
    bl_idname = "apx.select_group"
    bl_label = "Sélectionner"
    bl_options = {"REGISTER", "UNDO"}

    group: StringProperty()

    def execute(self, context):
        n = 0
        for o in context.view_layer.objects:
            if o.get("apx_group") == self.group and o.visible_get():
                o.select_set(True)
                n += 1
        self.report({"INFO"}, f"{n} objets sélectionnés")
        return {"FINISHED"}


class APX_OT_viewport_setup(bpy.types.Operator):
    """Règle la distance d'affichage du viewport / caméra pour une grande map"""
    bl_idname = "apx.viewport_setup"
    bl_label = "Régler le viewport (grande map)"

    def execute(self, context):
        optimize.setup_viewport_for_large_map()
        return {"FINISHED"}


class APX_OT_make_real(bpy.types.Operator):
    """Convertit les instances sélectionnées (mode Empties) en vrais objets"""
    bl_idname = "apx.make_real"
    bl_label = "Rendre réel (sélection)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any("apx_model" in o for o in context.selected_objects)

    def execute(self, context):
        sel = [o for o in context.selected_objects if "apx_model" in o]
        for o in sel:
            o.hide_viewport = False
        bpy.ops.object.duplicates_make_real(use_base_parent=False, use_hierarchy=False)
        for o in sel:
            bpy.data.objects.remove(o, do_unlink=True)
        lod_system.invalidate()
        return {"FINISHED"}


def menu_import(self, context):
    self.layout.operator(APX_OT_import_map.bl_idname, text="Apex Legends Map (.mprt/.cast/.ent)")


CLASSES = (APX_OT_import_map, APX_OT_update_lods, APX_OT_show_all, APX_OT_optimize,
           APX_OT_texture_limit, APX_OT_select_group, APX_OT_viewport_setup, APX_OT_make_real)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(menu_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_import)
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
