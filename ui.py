import bpy

from . import optimize


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Apex Map"


class APX_PT_import(_Base, bpy.types.Panel):
    bl_label = "Import"
    bl_idname = "APX_PT_import"

    def draw(self, context):
        s = context.scene.apx_settings
        col = self.layout.column()
        col.prop(s, "models_dir", text="Modèles")
        col.operator("apx.import_map", icon="IMPORT")
        col.prop(s, "instancing_mode", text="")
        box = col.box()
        box.label(text="Entités (props dynamiques)", icon="OUTLINER_OB_EMPTY")
        box.prop(s, "ent_path", text="")
        box.prop(s, "ent_files")
        box.prop(s, "import_ziplines")
        box.label(text="R5Reloaded : vpk/englishserver_<map>.bsp.pak000_dir.vpk")
        box = col.box()
        box.label(text="Géométrie de la map (.cast)", icon="WORLD")
        box.prop(s, "import_world")
        box.prop(s, "world_tile_size")
        box = col.box()
        box.label(text="Fichiers .cast", icon="TEXTURE")
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
        box.prop(s, "axis_correction", text="Axe")
        box.prop(s, "recenter")
        box.prop(s, "split_by_category")
        box.prop(s, "exclude_patterns")
        box.prop(s, "use_region")
        if s.use_region:
            box.prop(s, "region_center")
            box.prop(s, "region_radius")


class APX_PT_lod(_Base, bpy.types.Panel):
    bl_label = "LOD"
    bl_idname = "APX_PT_lod"

    def draw_header(self, context):
        self.layout.prop(context.scene.apx_settings, "use_lod", text="")

    def draw(self, context):
        s = context.scene.apx_settings
        col = self.layout.column()
        if not s.use_lod:
            col.label(text="LODs désactivés", icon="INFO")
            col.prop(s, "forced_lod")
            col.operator("apx.update_lods", icon="FILE_REFRESH")
            return
        col.prop(s, "lod_source")
        row = col.row(align=True)
        row.prop(s, "lod_auto_update", toggle=True)
        row.operator("apx.update_lods", text="", icon="FILE_REFRESH")
        col.prop(s, "lod_distances")
        col.prop(s, "lod_bias")
        col.prop(s, "lod_size_scaling")
        if s.lod_size_scaling:
            col.prop(s, "lod_reference_size")

        box = col.box()
        box.label(text="Déchargement (FPS)", icon="HIDE_ON")
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
        box.label(text="Rendu / mise à jour", icon="RENDER_STILL")
        box.prop(s, "render_mode")
        if s.render_mode == "CAMERA":
            box.prop(s, "render_frustum")
        box.prop(s, "update_settle")
        box.prop(s, "update_interval")
        box.prop(s, "update_move_threshold")
        box.prop(s, "update_angle_threshold")
        col.operator("apx.show_all", icon="HIDE_OFF")


class APX_PT_lod_import(_Base, bpy.types.Panel):
    bl_label = "Génération des LODs (à l'import)"
    bl_parent_id = "APX_PT_lod"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = context.scene.apx_settings
        col = self.layout.column()
        col.prop(s, "max_lods")
        col.prop(s, "auto_generate_lods")
        if s.auto_generate_lods:
            col.prop(s, "auto_lod_ratio")
            col.prop(s, "auto_lod_min_faces")


class APX_PT_visibility(_Base, bpy.types.Panel):
    bl_label = "Visibilité (triggers…)"
    bl_idname = "APX_PT_visibility"

    def draw(self, context):
        from . import visibility
        from .castfile import GROUPS
        s = context.scene.apx_settings
        counts = visibility.counts()
        col = self.layout.column()
        for g, label in GROUPS:
            row = col.row(align=True)
            hidden = getattr(s, f"hide_{g}")
            row.prop(s, f"hide_{g}", text=f"{label} ({counts.get(g, 0)})", toggle=True,
                     icon="HIDE_ON" if hidden else "HIDE_OFF", invert_checkbox=False)
            op = row.operator("apx.select_group", text="", icon="RESTRICT_SELECT_OFF")
            op.group = g
        col.label(text="Bouton enfoncé = caché", icon="INFO")


class APX_PT_optimize(_Base, bpy.types.Panel):
    bl_label = "Optimisation"
    bl_idname = "APX_PT_optimize"

    def draw(self, context):
        s = context.scene.apx_settings
        col = self.layout.column()
        col.prop(s, "join_meshes")
        col.prop(s, "merge_distance")
        col.prop(s, "dedupe_on_import")
        row = col.row(align=True)
        row.prop(s, "texture_limit", text="Textures")
        row.operator("apx.texture_limit", text="", icon="CHECKMARK")
        col.operator("apx.optimize", icon="MODIFIER")
        col.operator("apx.viewport_setup", icon="VIEW_CAMERA")
        col.operator("apx.make_real", icon="OUTLINER_OB_MESH")
        models, inst, tris = optimize.library_stats()
        box = col.box()
        box.label(text=f"Modèles uniques : {models}")
        box.label(text=f"Instances : {inst}")
        box.label(text=f"Triangles LOD0 (uniques) : {tris:,}".replace(",", " "))


CLASSES = (APX_PT_import, APX_PT_visibility, APX_PT_lod, APX_PT_lod_import, APX_PT_optimize)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
