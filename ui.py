import bpy

from . import optimize


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "R5 Map Loader"


class R5_PT_import(_Base, bpy.types.Panel):
    bl_label = "Import"
    bl_idname = "R5_PT_import"

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        col.prop(s, "models_dir", text="Models")
        col.operator("r5.import_map", icon="IMPORT")
        col.prop(s, "instancing_mode", text="")
        box = col.box()
        box.label(text="Entities (dynamic props, POIs)", icon="OUTLINER_OB_EMPTY")
        box.prop(s, "ent_path", text="")
        box.prop(s, "ent_files")
        box.prop(s, "import_ziplines")
        box.label(text="R5Reloaded: vpk/englishserver_<map>.bsp.pak000_dir.vpk")
        box = col.box()
        box.label(text="Map geometry (.cast)", icon="WORLD")
        box.prop(s, "import_world")
        box.prop(s, "world_split")
        if s.world_split == "TILES":
            box.prop(s, "world_tile_size")
        box = col.box()
        box.label(text=".cast files", icon="TEXTURE")
        box.prop(s, "cast_load_textures")
        if s.cast_load_textures:
            box.prop(s, "cast_alpha_mode")
            box.prop(s, "cast_spec_to_metal")
            box.prop(s, "cast_flip_normal_green")
            box.prop(s, "cast_extra_textures")
        box.prop(s, "decal_offset")
        box.prop(s, "cast_weld")
        box.prop(s, "cast_custom_normals")
        box = col.box()
        box.label(text="Options", icon="PREFERENCES")
        box.prop(s, "unit_scale")
        box.prop(s, "axis_correction", text="Axis")
        box.prop(s, "recenter")
        box.prop(s, "split_by_category")
        box.prop(s, "exclude_patterns")
        box.prop(s, "use_region")
        if s.use_region:
            box.prop(s, "region_center")
            box.prop(s, "region_radius")


class R5_PT_visibility(_Base, bpy.types.Panel):
    bl_label = "Visibility"
    bl_idname = "R5_PT_visibility"

    def draw(self, context):
        from . import visibility
        from .castfile import GROUPS
        s = context.scene.r5_settings
        counts = visibility.counts()
        col = self.layout.column()
        for g, label in GROUPS:
            row = col.row(align=True)
            hidden = getattr(s, f"hide_{g}")
            row.prop(s, f"hide_{g}", text=f"{label} ({counts.get(g, 0)})", toggle=True,
                     icon="HIDE_ON" if hidden else "HIDE_OFF")
            op = row.operator("r5.select_group", text="", icon="RESTRICT_SELECT_OFF")
            op.group = g
        col.label(text="Pressed = hidden", icon="INFO")


class R5_PT_models(_Base, bpy.types.Panel):
    bl_label = "Prop models"
    bl_idname = "R5_PT_models"
    bl_parent_id = "R5_PT_visibility"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        row = col.row(align=True)
        row.prop(s, "model_filter", text="", icon="VIEWZOOM")
        op = row.operator("r5.hide_models", text="", icon="HIDE_ON")
        op.hide = True
        op = row.operator("r5.hide_models", text="", icon="HIDE_OFF")
        op.hide = False
        col.template_list("R5_UL_models", "", bpy.data, "objects", s, "model_index", rows=8)
        col.label(text="Count = placements. Eye = viewport, camera = render", icon="INFO")


class R5_PT_regions(_Base, bpy.types.Panel):
    bl_label = "Regions / POI"
    bl_idname = "R5_PT_regions"

    def draw(self, context):
        from . import regions
        s = context.scene.r5_settings
        col = self.layout.column()
        active = regions.region_active(context.scene)
        if active:
            r = regions.get_region(context.scene)
            col.label(text=f"Showing a region ({r.scale.x:.0f} m)", icon="SELECT_SUBTRACT")
        else:
            col.label(text="Showing the whole map", icon="WORLD")
        row = col.row()
        row.template_list("R5_UL_pois", "", s, "pois", s, "poi_index", rows=6)
        side = row.column(align=True)
        side.operator("r5.poi_add", text="", icon="ADD")
        side.operator("r5.poi_remove", text="", icon="REMOVE")
        side.separator()
        side.operator("r5.poi_goto", text="", icon="VIEW_CAMERA")
        if 0 <= s.poi_index < len(s.pois):
            p = s.pois[s.poi_index]
            col.prop(p, "radius")
        col.prop(s, "region_scale")
        row = col.row(align=True)
        row.operator("r5.region_poi", icon="SELECT_SUBTRACT")
        row.operator("r5.region_clear", icon="WORLD")
        col.operator("r5.region_cursor", icon="PIVOT_CURSOR")
        row = col.row(align=True)
        row.prop(s, "grid_size")
        row.operator("r5.poi_grid", icon="MESH_GRID")
        col.prop(s, "poi_default_radius")


class R5_PT_lod(_Base, bpy.types.Panel):
    bl_label = "LOD & streaming"
    bl_idname = "R5_PT_lod"

    def draw_header(self, context):
        self.layout.prop(context.scene.r5_settings, "use_lod", text="")

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        if not s.use_lod:
            col.label(text="LODs disabled", icon="INFO")
            col.prop(s, "forced_lod")
            col.operator("r5.update_lods", icon="FILE_REFRESH")
            return
        col.prop(s, "lod_source")
        row = col.row(align=True)
        row.prop(s, "lod_auto_update", toggle=True)
        row.operator("r5.update_lods", text="", icon="FILE_REFRESH")
        col.prop(s, "lod_distances")
        col.prop(s, "lod_bias")
        col.prop(s, "lod_size_scaling")
        if s.lod_size_scaling:
            col.prop(s, "lod_reference_size")

        box = col.box()
        box.label(text="Unloading (FPS)", icon="HIDE_ON")
        box.prop(s, "use_frustum")
        if s.use_frustum:
            box.prop(s, "frustum_margin")
        box.prop(s, "cull_small_props")
        if s.cull_small_props:
            box.prop(s, "min_screen_pct")
        box.prop(s, "use_culling")
        if s.use_culling:
            box.prop(s, "cull_distance")
        if s.instancing_mode != "GN":
            box.prop(s, "use_bounds_level")
            if s.use_bounds_level:
                box.prop(s, "bounds_distance")

        box = col.box()
        box.label(text="Updates", icon="TIME")
        box.prop(s, "update_settle")
        box.prop(s, "update_interval")
        box.prop(s, "update_move_threshold")
        box.prop(s, "update_angle_threshold")
        col.operator("r5.show_all", icon="HIDE_OFF")


class R5_PT_lod_import(_Base, bpy.types.Panel):
    bl_label = "LOD generation (at import)"
    bl_parent_id = "R5_PT_lod"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        col.prop(s, "max_lods")
        col.prop(s, "auto_generate_lods")
        if s.auto_generate_lods:
            col.prop(s, "auto_lod_ratio")
            col.prop(s, "auto_lod_min_faces")


class R5_PT_sky(_Base, bpy.types.Panel):
    bl_label = "Sky"
    bl_idname = "R5_PT_sky"

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        col.prop(s, "sky_mode", text="")
        if s.sky_mode == "PROCEDURAL":
            if s.map_sun_json:
                col.prop(s, "sky_use_map_sun")
            if not (s.map_sun_json and s.sky_use_map_sun):
                col.prop(s, "sky_sun_elevation")
                col.prop(s, "sky_sun_rotation")
            col.prop(s, "sky_haze")
        else:
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
            col.prop(s, "sky_add_sun")
            if s.sky_add_sun:
                col.prop(s, "sky_sun_elevation")
                col.prop(s, "sky_sun_rotation")
        col.prop(s, "sky_strength")
        col.prop(s, "sky_sun_strength")
        col.operator("r5.setup_sky", icon="WORLD_DATA")
        if not s.map_sun_json:
            col.label(text="The map sky/sun live in the compressed _env.ent", icon="INFO")


class R5_PT_render(_Base, bpy.types.Panel):
    bl_label = "Render (Cycles)"
    bl_idname = "R5_PT_render"

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        row = col.row(align=True)
        op = row.operator("r5.setup_cycles", text="Cycles preview", icon="SHADING_RENDERED")
        op.preset = "PREVIEW"
        op = row.operator("r5.setup_cycles", text="Cycles final", icon="RENDER_STILL")
        op.preset = "FINAL"
        col.prop(s, "cycles_samples_preview")
        col.prop(s, "cycles_samples_final")
        col.prop(s, "cycles_viewport_samples")
        col.prop(s, "cycles_texture_limit")
        col.prop(s, "cycles_final_lod0")
        box = col.box()
        box.label(text="Props at render time", icon="OUTLINER_OB_POINTCLOUD")
        box.prop(s, "render_mode")
        if s.render_mode == "CAMERA":
            box.prop(s, "render_frustum")
            box.prop(s, "render_min_screen_pct")
        col.label(text="Tip: Regions / POI renders only part of the map", icon="INFO")


class R5_PT_optimize(_Base, bpy.types.Panel):
    bl_label = "Optimization"
    bl_idname = "R5_PT_optimize"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        col.prop(s, "join_meshes")
        col.prop(s, "merge_distance")
        col.prop(s, "dedupe_on_import")
        row = col.row(align=True)
        row.prop(s, "texture_limit", text="Textures")
        row.operator("r5.texture_limit", text="", icon="CHECKMARK")
        col.operator("r5.optimize", icon="MODIFIER")
        col.operator("r5.viewport_setup", icon="VIEW_CAMERA")
        col.operator("r5.make_real", icon="OUTLINER_OB_MESH")
        models, inst, tris = optimize.library_stats()
        box = col.box()
        box.label(text=f"Unique models: {models}")
        box.label(text=f"Instances: {inst}")
        box.label(text=f"LOD0 triangles (unique): {tris:,}")


CLASSES = (R5_PT_import, R5_PT_visibility, R5_PT_models, R5_PT_regions, R5_PT_lod, R5_PT_lod_import,
           R5_PT_sky, R5_PT_render, R5_PT_optimize)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
