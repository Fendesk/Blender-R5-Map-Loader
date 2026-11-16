import bpy

from . import optimize


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "R5 Map Loader"


class R5_PT_import(_Base, bpy.types.Panel):
    """Import: from the game (R5Reloaded / R5Flowstate install) or from exported files, in one panel."""
    bl_label = "Import"
    bl_idname = "R5_PT_import"
    bl_order = 0

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        if not hasattr(s, "import_source"):
            # settings class of the previous version still loaded (update installed without restarting)
            col.label(text="Restart Blender to finish the update", icon="ERROR")
            return
        col.row().prop(s, "import_source", expand=True)
        if s.import_source == "GAME":
            from . import game_import
            box = col.box()
            box.label(text="Experimental feature (beta)", icon="EXPERIMENTAL")
            box.label(text="Results may be incomplete: missing models,")
            box.label(text="textures, or approximate terrain (R5Flowstate).")
            game_import.draw_game(col, s)
        else:
            col.prop(s, "models_dir", text="Models")
            row = col.row()
            row.scale_y = 1.3
            row.operator("r5.import_map", text="Import files (.mprt .ent .vpk .nut .cast...)", icon="IMPORT")
        col.prop(s, "instancing_mode", text="")


class _ImportSub(_Base):
    bl_parent_id = "R5_PT_import"
    bl_options = {"DEFAULT_CLOSED"}


class R5_PT_import_tools(_ImportSub, bpy.types.Panel):
    bl_label = "Tools & cache"
    bl_idname = "R5_PT_import_tools"

    @classmethod
    def poll(cls, context):
        s = context.scene.r5_settings
        return getattr(s, "import_source", "GAME") == "GAME"

    def draw(self, context):
        from . import game_import
        game_import.draw_game_tools(self.layout.column(), context.scene.r5_settings)


class R5_PT_import_ents(_ImportSub, bpy.types.Panel):
    bl_label = "Entities (dynamic props, POIs)"
    bl_idname = "R5_PT_import_ents"

    @classmethod
    def poll(cls, context):
        return getattr(context.scene.r5_settings, "import_source", "FILES") == "FILES"

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        col.prop(s, "ent_path", text="")
        col.prop(s, "ent_files")
        col.label(text="R5Reloaded: vpk/englishserver_<map>.bsp.pak000_dir.vpk")


class R5_PT_import_geometry(_ImportSub, bpy.types.Panel):
    bl_label = "Map geometry & ziplines"
    bl_idname = "R5_PT_import_geometry"

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        col.prop(s, "import_world")
        col.prop(s, "world_split")
        if s.world_split == "TILES":
            col.prop(s, "world_tile_size")
        col.prop(s, "import_ziplines")


class R5_PT_import_cast(_ImportSub, bpy.types.Panel):
    bl_label = "Materials & meshes (.cast)"
    bl_idname = "R5_PT_import_cast"

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        col.prop(s, "cast_load_textures")
        if s.cast_load_textures:
            col.prop(s, "cast_alpha_mode")
            col.prop(s, "cast_spec_to_metal")
            col.prop(s, "cast_flip_normal_green")
            col.prop(s, "cast_extra_textures")
        col.prop(s, "decal_offset")
        col.prop(s, "cast_weld")
        col.prop(s, "cast_custom_normals")


class R5_PT_import_options(_ImportSub, bpy.types.Panel):
    bl_label = "Options"
    bl_idname = "R5_PT_import_options"

    def draw(self, context):
        s = context.scene.r5_settings
        col = self.layout.column()
        col.prop(s, "unit_scale")
        col.prop(s, "axis_correction", text="Axis")
        col.prop(s, "recenter")
        col.prop(s, "split_by_category")
        col.prop(s, "exclude_patterns")
        col.prop(s, "use_region")
        if s.use_region:
            col.prop(s, "region_center")
            col.prop(s, "region_radius")


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
        col.operator("r5.link_materials", icon="MATERIAL")
        col.label(text="Clicking a prop selects every copy of its model", icon="INFO")
        col.label(text="(one instancer object): its Material tab edits them all")


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

        # Sun
        box = col.box()
        box.prop(s, "sky_add_sun", text="Sun", icon="LIGHT_SUN")
        if s.sky_add_sun or s.sky_mode == "PROCEDURAL":
            if s.map_sun_json:
                box.prop(s, "sky_use_map_sun")
            # always editable: moving them switches to a manual sun (they start from the map's sun)
            box.prop(s, "sky_sun_elevation")
            box.prop(s, "sky_sun_rotation")
            box.prop(s, "sky_sun_strength")
            row = box.row(align=True)
            row.prop(s, "sky_sun_color", text="Colour")
            row.operator("r5.sun_color_reset", text="", icon="LOOP_BACK")

        # Atmosphere (gases, dust, ozone)
        box = col.box()
        if s.sky_mode == "PROCEDURAL":
            box.label(text="Atmosphere", icon="OUTLINER_DATA_VOLUME")
        else:
            box.prop(s, "sky_atmosphere", icon="OUTLINER_DATA_VOLUME")
        if s.sky_mode == "PROCEDURAL" or s.sky_atmosphere:
            if s.sky_mode != "PROCEDURAL":
                box.prop(s, "sky_atmosphere_strength")
            box.prop(s, "sky_air_density")
            box.prop(s, "sky_dust_density")
            box.prop(s, "sky_ozone_density")

        # Fog
        box = col.box()
        box.prop(s, "sky_fog", icon="FORCE_TURBULENCE")
        if s.sky_fog:
            box.prop(s, "sky_fog_density")
            box.prop(s, "sky_fog_color")
            box.prop(s, "sky_fog_anisotropy")
            box.prop(s, "sky_fog_distance")

        col.operator("r5.setup_sky", icon="WORLD_DATA")
        col.label(text="Changes apply live once the sky is applied", icon="INFO")
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


CLASSES = (R5_PT_import, R5_PT_import_tools, R5_PT_import_ents, R5_PT_import_geometry, R5_PT_import_cast,
           R5_PT_import_options, R5_PT_visibility, R5_PT_models, R5_PT_regions, R5_PT_lod, R5_PT_lod_import,
           R5_PT_sky, R5_PT_render, R5_PT_optimize)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
