"""Sidebar (N) > R5 Map Loader.

Top-level panels, in the order of the work: Map (import) > Display > Regions / POI > Performance > Sky >
City lights (lights.py) > Animation > Camera & Render. Every panel keeps its main buttons on top; settings
used less often live in sub-panels closed by default.
"""
import bpy

from . import optimize, progress


SIDEBAR = [True]                 # False in the EX version (exwin.py): its interface is a separate window


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "R5 Map Loader"

    @classmethod
    def poll(cls, context):
        # during an import only the Map panel (with the progress bar) is drawn: the others read the whole scene
        return SIDEBAR[0] and (not progress.active() or cls.bl_idname == "R5_PT_import")


class _Sub(_Base):
    bl_options = {"DEFAULT_CLOSED"}


def _settings(context):
    return context.scene.r5_settings


def _num(n):
    return f"{n:,}".replace(",", " ")


def _icon_header(icon):
    def draw_header(self, context):
        self.layout.label(icon=icon)
    return draw_header


# ==========================================================================
# 1. Map: import
# ==========================================================================
class R5_PT_import(_Base, bpy.types.Panel):
    """Import exported maps (r5mex / Legion+ / RSX), entity files and ReMap scripts."""
    bl_label = "Map"
    bl_idname = "R5_PT_import"
    bl_order = 0
    draw_header = _icon_header("IMPORT")

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        if not hasattr(s, "auto_entities"):
            # settings class of the previous version still loaded (update installed without restarting)
            col.label(text="Restart Blender to finish the update", icon="ERROR")
            return
        col.prop(s, "models_dir", text="Models")
        col.prop(s, "instancing_mode", text="")
        from .operators import map_loaded
        col.separator()
        row = col.row()
        row.scale_y = 1.4
        row.operator("r5.import_map", text="Import map…", icon="IMPORT")
        if map_loaded(context.scene):
            row = col.row()
            row.scale_y = 1.2
            row.operator("r5.import_map", text="Replace loaded map…", icon="FILE_REFRESH").replace = True
        col.label(text=".mprt .cast .ent .vpk .nut", icon="FILE")
        col.label(text="One POI only: in the file browser", icon="SELECT_SUBTRACT")


class _ImportSub(_Sub):
    bl_parent_id = "R5_PT_import_settings"


class R5_PT_import_settings(_Sub, bpy.types.Panel):
    bl_label = "Import settings"
    bl_idname = "R5_PT_import_settings"
    bl_parent_id = "R5_PT_import"
    draw_header = _icon_header("PREFERENCES")

    def draw(self, context):
        col = self.layout.column()
        col.prop(_settings(context), "setup_viewport_on_import")
        col.label(text="Used by the next import", icon="INFO")


class R5_PT_import_geometry(_ImportSub, bpy.types.Panel):
    bl_label = "Map geometry & ziplines"
    bl_idname = "R5_PT_import_geometry"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "import_world")
        col.prop(s, "world_split", text="Split")
        if s.world_split == "TILES":
            col.prop(s, "world_tile_size")
        col.prop(s, "import_skybox")
        col.prop(s, "import_ziplines")


class R5_PT_import_ents(_ImportSub, bpy.types.Panel):
    bl_label = "Entities (dynamic props, POIs)"
    bl_idname = "R5_PT_import_ents"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "auto_entities")
        col.prop(s, "ent_path", text="")
        col.prop(s, "ent_files", text="Files")
        col.label(text="R5Reloaded: vpk/englishserver_")
        col.label(text="    <map>.bsp.pak000_dir.vpk")


class R5_PT_import_cast(_ImportSub, bpy.types.Panel):
    bl_label = "Materials & textures"
    bl_idname = "R5_PT_import_cast"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "cast_load_textures")
        sub = col.column()
        sub.enabled = s.cast_load_textures
        sub.prop(s, "texture_other_exports")
        sub.prop(s, "cast_alpha_mode", text="Alpha")
        sub.prop(s, "cast_spec_to_metal")
        sub.prop(s, "cast_flip_normal_green")
        sub.prop(s, "cast_extra_textures")
        col.prop(s, "decal_offset")
        col.prop(s, "dedupe_on_import")


class R5_PT_import_meshes(_ImportSub, bpy.types.Panel):
    bl_label = "Meshes"
    bl_idname = "R5_PT_import_meshes"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "join_meshes")
        col.prop(s, "merge_distance")
        col.prop(s, "cast_weld")
        col.prop(s, "cast_custom_normals")


class R5_PT_import_lods(_ImportSub, bpy.types.Panel):
    bl_label = "LOD generation"
    bl_idname = "R5_PT_lod_import"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "max_lods")
        col.prop(s, "auto_generate_lods")
        sub = col.column()
        sub.enabled = s.auto_generate_lods
        sub.prop(s, "auto_lod_ratio")
        sub.prop(s, "auto_lod_min_ratio")
        sub.prop(s, "auto_lod_min_faces")
        sub.prop(s, "auto_lod_max_radius")
        sub.prop(s, "auto_lod_skip_names", text="Never decimate")


class R5_PT_import_options(_ImportSub, bpy.types.Panel):
    bl_label = "Placement & filters"
    bl_idname = "R5_PT_import_options"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "unit_scale")
        col.prop(s, "axis_correction", text="Axis")
        col.prop(s, "lift_above_zero")
        col.prop(s, "recenter")
        col.prop(s, "split_by_category")
        col.prop(s, "exclude_patterns")
        col.prop(s, "use_region")
        if s.use_region:
            col.prop(s, "region_center")
            col.prop(s, "region_radius")


# ==========================================================================
# 2. Display: what is shown
# ==========================================================================
class R5_PT_visibility(_Base, bpy.types.Panel):
    bl_label = "Display"
    bl_order = 1
    bl_idname = "R5_PT_visibility"
    bl_options = {"DEFAULT_CLOSED"}
    draw_header = _icon_header("HIDE_OFF")

    def draw(self, context):
        from . import visibility
        from .castfile import GROUPS
        s = _settings(context)
        counts = visibility.counts()
        col = self.layout.column()
        col.label(text="Pressed = hidden", icon="INFO")
        grid = col.column(align=True)
        for g, label in GROUPS:
            row = grid.row(align=True)
            hidden = getattr(s, f"hide_{g}")
            row.prop(s, f"hide_{g}", text=f"{label.split(' (')[0]} ({counts.get(g, 0)})", toggle=True,
                     icon="HIDE_ON" if hidden else "HIDE_OFF")
            op = row.operator("r5.select_group", text="", icon="RESTRICT_SELECT_OFF")
            op.group = g


class R5_PT_models(_Sub, bpy.types.Panel):
    bl_label = "Prop models"
    bl_idname = "R5_PT_models"
    bl_parent_id = "R5_PT_visibility"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        row = col.row(align=True)
        row.prop(s, "model_filter", text="", icon="VIEWZOOM")
        row.operator("r5.hide_models", text="", icon="HIDE_ON").hide = True
        row.operator("r5.hide_models", text="", icon="HIDE_OFF").hide = False
        col.template_list("R5_UL_models", "", bpy.data, "objects", s, "model_index", rows=8)
        col.label(text="Number = placements", icon="INFO")
        col.operator("r5.link_materials", icon="MATERIAL")
        col.label(text="A click selects every copy of a model:", icon="INFO")
        col.label(text="its Material tab edits them all")


# ==========================================================================
# 3. Regions / POI
# ==========================================================================
class R5_PT_regions(_Base, bpy.types.Panel):
    bl_label = "Regions / POI"
    bl_order = 2
    bl_idname = "R5_PT_regions"
    draw_header = _icon_header("SELECT_SUBTRACT")

    def draw(self, context):
        from . import regions
        s = _settings(context)
        col = self.layout.column()
        n = len(regions.get_discs(context.scene))
        if n:
            col.label(text=f"Showing {n} area{'s' if n > 1 else ''} of the map", icon="SELECT_SUBTRACT")
        else:
            col.label(text="Showing the whole map", icon="WORLD")
        row = col.row()
        row.template_list("R5_UL_pois", "", s, "pois", s, "poi_index", rows=6)
        side = row.column(align=True)
        side.operator("r5.poi_add", text="", icon="ADD")
        side.operator("r5.poi_remove", text="", icon="REMOVE")
        side.separator()
        side.operator("r5.poi_goto", text="", icon="VIEW_CAMERA")
        side.separator()
        side.operator("r5.poi_check", text="", icon="CHECKBOX_HLT").state = True
        side.operator("r5.poi_check", text="", icon="CHECKBOX_DEHLT").state = False
        if 0 <= s.poi_index < len(s.pois):
            col.prop(s.pois[s.poi_index], "radius")
        col.prop(s, "region_scale")
        checked = sum(1 for p in s.pois if p.use)
        row = col.row(align=True)
        row.scale_y = 1.2
        row.operator("r5.region_poi", icon="SELECT_SUBTRACT",
                     text=f"Show {checked} POIs" if checked > 1 else "Show POI")
        row.operator("r5.region_clear", icon="WORLD")
        col.operator("r5.region_cursor", icon="PIVOT_CURSOR")


class R5_PT_regions_more(_Sub, bpy.types.Panel):
    bl_label = "Grid & new POIs"
    bl_idname = "R5_PT_regions_more"
    bl_parent_id = "R5_PT_regions"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        row = col.row(align=True)
        row.prop(s, "grid_size")
        row.operator("r5.poi_grid", icon="MESH_GRID")
        col.prop(s, "poi_default_radius")


# ==========================================================================
# 4. Performance: LODs, unloading, scene tools
# ==========================================================================
class R5_PT_lod(_Base, bpy.types.Panel):
    bl_label = "Performance (LOD & culling)"
    bl_order = 3
    bl_idname = "R5_PT_lod"

    def draw_header(self, context):
        self.layout.prop(_settings(context), "use_lod", text="")

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        if not s.use_lod:
            col.label(text="LODs disabled", icon="INFO")
            col.prop(s, "forced_lod")
            col.operator("r5.update_lods", icon="FILE_REFRESH")
            return
        from . import lod_system
        h = lod_system.HIDDEN
        box = col.box()
        row = box.row(align=True)
        if h["valid"] and h["total"]:
            hidden = max(h["total"] - h["drawn"], 0)
            pct = 100.0 * hidden / h["total"]
            row.label(text=f"{_num(hidden)} hidden ({pct:.0f}%)", icon="HIDE_ON" if hidden else "HIDE_OFF")
            box.label(text=f"of {_num(h['total'])} props, {_num(h['drawn'])} drawn")
        else:
            row.label(text="Hidden props: not counted yet", icon="HIDE_OFF")
        row.operator("r5.count_hidden", text="", icon="FILE_REFRESH")
        box.operator("r5.show_all", text="Show everything (LOD0, no culling)", icon="HIDE_OFF")
        row = col.row(align=True)
        row.prop(s, "lod_auto_update", toggle=True)
        row.operator("r5.update_lods", text="", icon="FILE_REFRESH")


class _LodSub(_Sub):
    bl_parent_id = "R5_PT_lod"

    @classmethod
    def poll(cls, context):
        return SIDEBAR[0] and _settings(context).use_lod


class R5_PT_lod_levels(_LodSub, bpy.types.Panel):
    bl_label = "Levels of detail"
    bl_idname = "R5_PT_lod_levels"
    bl_options = set()           # open: the settings used most

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "lod_source", text="Source")
        col.prop(s, "lod_distances")
        col.prop(s, "lod_bias")
        col.prop(s, "lod_size_scaling")
        if s.lod_size_scaling:
            col.prop(s, "lod_reference_size")


class R5_PT_lod_culling(_LodSub, bpy.types.Panel):
    bl_label = "Unloading (FPS)"
    bl_idname = "R5_PT_lod_culling"
    bl_options = set()

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "use_frustum")
        if s.use_frustum:
            col.prop(s, "frustum_margin")
        col.prop(s, "cull_small_props")
        if s.cull_small_props:
            col.prop(s, "min_screen_pct")
            col.prop(s, "cull_keep_radius")
        col.prop(s, "use_culling")
        if s.use_culling:
            col.prop(s, "cull_distance")
        if s.instancing_mode != "GN":
            col.prop(s, "use_bounds_level")
            if s.use_bounds_level:
                col.prop(s, "bounds_distance")


class R5_PT_lod_updates(_LodSub, bpy.types.Panel):
    bl_label = "Update timing"
    bl_idname = "R5_PT_lod_updates"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "update_settle")
        col.prop(s, "update_interval")
        col.prop(s, "update_move_threshold")
        col.prop(s, "update_angle_threshold")


class R5_PT_optimize(_Sub, bpy.types.Panel):
    bl_label = "Scene tools"
    bl_idname = "R5_PT_optimize"
    bl_parent_id = "R5_PT_lod"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        row = col.row(align=True)
        row.prop(s, "texture_limit", text="Textures")
        row.operator("r5.texture_limit", text="", icon="CHECKMARK")
        col.operator("r5.optimize", icon="MODIFIER")
        col.operator("r5.viewport_setup", icon="VIEW_CAMERA")
        col.operator("r5.make_real", icon="OUTLINER_OB_MESH")
        models, inst, tris = optimize.library_stats()
        box = col.box()
        box.label(text=f"Unique models: {_num(models)}")
        box.label(text=f"Instances: {_num(inst)}")
        box.label(text=f"LOD0 triangles (unique): {_num(tris)}")


# ==========================================================================
# 5. Sky (City lights: lights.py, bl_order 5)
# ==========================================================================
class R5_PT_sky(_Base, bpy.types.Panel):
    bl_label = "Sky"
    bl_order = 4
    bl_idname = "R5_PT_sky"
    bl_options = {"DEFAULT_CLOSED"}
    draw_header = _icon_header("WORLD_DATA")

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "sky_mode", text="")
        if s.sky_mode != "PROCEDURAL":
            label = {"IMAGE": "Panorama", "DOME": "Dome image"}.get(s.sky_mode, "One face")
            col.prop(s, "sky_image", text=label)
            col.prop(s, "sky_image_rotation")
            if s.sky_mode == "DOME":
                col.prop(s, "sky_dome_projection", text="")
                col.prop(s, "sky_dome_horizon")
                col.prop(s, "sky_dome_mirror")
            if s.sky_mode == "CUBEMAP":
                col.prop(s, "sky_cubemap_y_up")
                col.prop(s, "sky_panorama_width")
        col.prop(s, "sky_strength")
        row = col.row()
        row.scale_y = 1.2
        row.operator("r5.setup_sky", icon="WORLD_DATA")
        col.label(text="Then changes apply live", icon="INFO")
        if not s.map_sun_json:
            col.label(text="No map sun (compressed _env.ent)", icon="INFO")


class _SkySub(_Sub):
    bl_parent_id = "R5_PT_sky"


class R5_PT_sky_sun(_SkySub, bpy.types.Panel):
    bl_label = "Sun"
    bl_idname = "R5_PT_sky_sun"

    def draw_header(self, context):
        s = _settings(context)
        if s.sky_mode == "PROCEDURAL":
            self.layout.label(icon="LIGHT_SUN")
        else:
            self.layout.prop(s, "sky_add_sun", text="")

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.enabled = s.sky_add_sun or s.sky_mode == "PROCEDURAL"
        if s.map_sun_json:
            col.prop(s, "sky_use_map_sun")
        # always editable: moving them switches to a manual sun (they start from the map's sun)
        col.prop(s, "sky_sun_elevation")
        col.prop(s, "sky_sun_rotation")
        col.prop(s, "sky_sun_strength")
        row = col.row(align=True)
        row.prop(s, "sky_sun_color", text="Colour")
        row.operator("r5.sun_color_reset", text="", icon="LOOP_BACK")


class R5_PT_sky_atmosphere(_SkySub, bpy.types.Panel):
    bl_label = "Atmosphere"
    bl_idname = "R5_PT_sky_atmosphere"

    def draw_header(self, context):
        s = _settings(context)
        if s.sky_mode == "PROCEDURAL":
            self.layout.label(icon="OUTLINER_DATA_VOLUME")
        else:
            self.layout.prop(s, "sky_atmosphere", text="")

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.enabled = s.sky_mode == "PROCEDURAL" or s.sky_atmosphere
        if s.sky_mode != "PROCEDURAL":
            col.prop(s, "sky_atmosphere_strength")
        col.prop(s, "sky_air_density")
        col.prop(s, "sky_dust_density")
        col.prop(s, "sky_ozone_density")


class R5_PT_sky_fog(_SkySub, bpy.types.Panel):
    bl_label = "Fog"
    bl_idname = "R5_PT_sky_fog"

    def draw_header(self, context):
        self.layout.prop(_settings(context), "sky_fog", text="")

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.enabled = s.sky_fog
        col.prop(s, "sky_fog_density")
        col.prop(s, "sky_fog_color")
        col.prop(s, "sky_fog_anisotropy")
        col.prop(s, "sky_fog_distance")


# ==========================================================================
# 6. Animation
# ==========================================================================
class R5_PT_motion(_Base, bpy.types.Panel):
    """Moving props and animated materials, switched on after loading (they cost time on every frame)."""
    bl_label = "Animation"
    bl_order = 6
    bl_idname = "R5_PT_motion"
    bl_options = {"DEFAULT_CLOSED"}
    draw_header = _icon_header("PLAY")

    def draw(self, context):
        from . import motion
        scene = context.scene
        s = _settings(context)
        col = self.layout.column()

        row = col.row(align=True)
        row.scale_y = 1.2
        if motion.movers_enabled(scene):
            row.operator("r5.movers", text="Stop movers", icon="PAUSE").enable = False
            row.operator("r5.movers", text="", icon="FILE_REFRESH").enable = True
        else:
            row.operator("r5.movers", text="Start movers", icon="PLAY").enable = True
        if motion.shaders_enabled():
            row.operator("r5.anim_shaders", text="Stop shaders", icon="PAUSE").enable = False
            row.operator("r5.anim_shaders", text="", icon="FILE_REFRESH").enable = True
        else:
            row.operator("r5.anim_shaders", text="Start shaders", icon="PLAY").enable = True
        col.label(text="Rotators, fans, rings, Tridents", icon="FORCE_VORTEX")
        col.label(text="Play the timeline (Space)", icon="TIME")


class R5_PT_motion_speeds(_Sub, bpy.types.Panel):
    bl_label = "Speeds"
    bl_idname = "R5_PT_motion_speeds"
    bl_parent_id = "R5_PT_motion"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "movers_speed")
        col.prop(s, "shader_speed")
        sub = col.column(align=True)
        sub.prop(s, "speed_water")
        sub.prop(s, "speed_lava")
        sub.prop(s, "speed_screens")
        sub.prop(s, "speed_energy")
        sub.prop(s, "speed_effects")


class R5_PT_motion_wind(_Sub, bpy.types.Panel):
    bl_label = "Foliage wind"
    bl_idname = "R5_PT_motion_wind"
    bl_parent_id = "R5_PT_motion"

    def draw_header(self, context):
        self.layout.prop(_settings(context), "anim_foliage", text="")

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column(align=True)
        col.enabled = s.anim_foliage
        col.prop(s, "wind_speed")
        col.prop(s, "wind_strength")


# ==========================================================================
# 7. Camera & Render
# ==========================================================================
class R5_PT_render(_Base, bpy.types.Panel):
    bl_label = "Camera & Render"
    bl_order = 7
    bl_idname = "R5_PT_render"
    draw_header = _icon_header("RENDER_STILL")

    def draw(self, context):
        scene = context.scene
        col = self.layout.column()
        row = col.row(align=True)
        row.scale_y = 1.3
        op = row.operator("r5.camera_from_view", text="New camera from view", icon="OUTLINER_OB_CAMERA")
        op.new = True
        op = row.operator("r5.camera_from_view", text="", icon="CON_CAMERASOLVER")
        op.new = False
        col.prop(scene, "camera", text="Active")
        col.separator()
        row = col.row(align=True)
        row.scale_y = 1.2
        row.operator("r5.setup_cycles", text="Cycles preview", icon="SHADING_RENDERED").preset = "PREVIEW"
        row.operator("r5.setup_cycles", text="Cycles final", icon="RENDER_STILL").preset = "FINAL"
        col.label(text="Regions / POI: render part of the map", icon="INFO")


class R5_PT_render_cycles(_Sub, bpy.types.Panel):
    bl_label = "Cycles presets"
    bl_idname = "R5_PT_render_cycles"
    bl_parent_id = "R5_PT_render"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "cycles_samples_preview")
        col.prop(s, "cycles_samples_final")
        col.prop(s, "cycles_viewport_samples")
        col.prop(s, "cycles_texture_limit", text="Textures")
        col.prop(s, "cycles_final_lod0")


class R5_PT_render_props(_Sub, bpy.types.Panel):
    bl_label = "Props at render time"
    bl_idname = "R5_PT_render_props"
    bl_parent_id = "R5_PT_render"

    def draw(self, context):
        s = _settings(context)
        col = self.layout.column()
        col.prop(s, "render_mode", text="LOD")
        if s.render_mode == "CAMERA":
            col.prop(s, "render_frustum")
            col.prop(s, "render_min_screen_pct", text="Min. size on screen (%)")
            box = col.box()
            box.prop(s, "lod_fade", icon="MOD_TIME")
            if s.lod_fade:
                box.prop(s, "lod_fade_width")
                box.prop(s, "lod_fade_viewport")


CLASSES = (R5_PT_import, R5_PT_import_settings, R5_PT_import_geometry, R5_PT_import_ents, R5_PT_import_cast,
           R5_PT_import_meshes, R5_PT_import_lods, R5_PT_import_options,
           R5_PT_visibility, R5_PT_models,
           R5_PT_regions, R5_PT_regions_more,
           R5_PT_lod, R5_PT_lod_levels, R5_PT_lod_culling, R5_PT_lod_updates, R5_PT_optimize,
           R5_PT_sky, R5_PT_sky_sun, R5_PT_sky_atmosphere, R5_PT_sky_fog,
           R5_PT_motion, R5_PT_motion_speeds, R5_PT_motion_wind,
           R5_PT_render, R5_PT_render_cycles, R5_PT_render_props)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        if getattr(c, "is_registered", False):
            bpy.utils.unregister_class(c)
