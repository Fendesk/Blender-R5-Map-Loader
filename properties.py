import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, StringProperty)

from . import lod_system
from .regions import R5_POI


def _lod_changed(self, context):
    lod_system.invalidate()
    try:
        from . import gn_instancer
        gn_instancer.apply_settings(context.scene)
    except Exception as exc:  # noqa: BLE001
        print(f"[R5] {exc}")


def _vis_changed(self, context):
    from . import visibility
    visibility.apply(context.scene)


def _sky_changed(self, context):
    from . import sky
    w = bpy.data.worlds.get(sky.WORLD)
    if w is not None and w.get("r5_sky_mode") == "DOME":
        try:
            sky.refresh_dome(context.scene)
        except Exception as exc:  # noqa: BLE001
            print(f"[R5] sky: {exc}")
    elif w is not None and context.scene.world == w:
        for n in w.node_tree.nodes:
            if n.type == "BACKGROUND":
                n.inputs["Strength"].default_value = self.sky_strength


_TEX_LIMITS = [("OFF", "No limit", ""), ("4096", "4096", ""), ("2048", "2048", ""), ("1024", "1024", ""),
               ("512", "512", "")]


class R5_Settings(bpy.types.PropertyGroup):
    # ---------------- Visibility ----------------
    hide_trigger: BoolProperty(name="Hide triggers", default=True, update=_vis_changed,
                               description="toolstrigger, fog volumes...")
    hide_tool: BoolProperty(name="Hide tool brushes", default=True, update=_vis_changed,
                            description="clip, blocklight, skybox, nodraw, invisible...")
    hide_fx: BoolProperty(name="Hide light shafts & atmosphere", default=True, update=_vis_changed,
                          description="Godray / light shaft / atmosphere volume props. In game they are additive "
                                      "translucent effects; in Blender they show up as large ellipses / cones")
    hide_gamemode: BoolProperty(name="Hide game mode barriers", default=True, update=_vis_changed,
                                description="Arenas / Control walls and borders (ar_* materials)")
    hide_dev: BoolProperty(name="Hide dev textures", default=True, update=_vis_changed,
                           description="dev_* checkers, test_*, placeholders. dev_*_light panels, which are visible "
                                       "in game, are not affected")
    model_filter: StringProperty(name="Model name", description="Part of a model name, e.g. godray, sphere, fog")
    model_index: IntProperty(default=0)
    decal_offset: FloatProperty(
        name="Overlay offset (game units)", default=0.35, min=0.0, max=5.0, precision=3,
        description="Pushes decals and terrain overlays (_b2t, _bt, decal...) above the surface to remove "
                    "flickering (z-fighting). 0 = off")
    cast_extra_textures: BoolProperty(
        name="Add unused textures", default=False,
        description="Also add AO, cavity, etc. to the materials (not connected, more memory)")
    texture_limit: EnumProperty(
        name="Max texture size (viewport)",
        items=[("CLAMP_OFF", "No limit", ""), ("CLAMP_2048", "2048", ""), ("CLAMP_1024", "1024", ""),
               ("CLAMP_512", "512", ""), ("CLAMP_256", "256", "")],
        default="CLAMP_512",
        description="A full map uses ~4,400 unique textures. Applied on import (only ever lowered). Measured on "
                    "World's Edge with a 16 GB GPU, Material Preview: no limit = 12 s frames and driver reset, "
                    "1024 = 13.6 GB, 512 = 6.2 GB at the same speed")

    # ---------------- Import ----------------
    models_dir: StringProperty(
        name="Models folder", subtype="DIR_PATH",
        description="Folder holding the exported models (RSX / Legion+: .cast, or .gltf/.glb/.fbx/.obj/.smd). "
                    "Files named *_LOD0, *_LOD1... are detected as LODs. Empty = the '_models' folder next to the "
                    "imported file")
    unit_scale: FloatProperty(
        name="Scale", default=0.0254, min=1e-5, precision=4,
        description="Game units -> meters (1 Source unit = 1 inch = 0.0254 m)")
    axis_correction: EnumProperty(
        name="Model axis correction",
        items=[("NONE", "None", ""), ("X90", "X +90°", ""), ("X-90", "X -90°", ""),
               ("Z90", "Z +90°", ""), ("Z-90", "Z -90°", ""), ("Z180", "Z 180°", "")],
        default="NONE",
        description="Rotation applied to the models if the exporter outputs them lying down / turned")
    recenter: BoolProperty(name="Recenter map", default=False,
                           description="Moves the map center to the origin (better precision)")
    split_by_category: BoolProperty(name="Collections per category", default=True,
                                    description="Empties mode: sort props by folder (props, foliage, rocks...)")
    exclude_patterns: StringProperty(
        name="Exclude", default="dev,editor,trigger,playerclip,invisible,nodraw,collision,error",
        description="Comma separated words. A model is skipped when a whole word of its name matches "
                    "('col' excludes 'rock_col' but not 'column'). * = wildcard")
    use_region: BoolProperty(name="Import an area only", default=False,
                             description="Only import the props within a radius around a point (game units)")
    region_center: FloatVectorProperty(name="Center", size=3, default=(0, 0, 0))
    region_radius: FloatProperty(name="Radius", default=10000.0, min=1.0)
    join_meshes: BoolProperty(name="Merge the meshes of a model", default=True,
                              description="One object per LOD instead of several (fewer objects = faster viewport)")
    merge_distance: FloatProperty(name="Merge vertices", default=0.0, min=0.0, precision=5,
                                  description="Merge distance for duplicate vertices (0 = off)")
    dedupe_on_import: BoolProperty(name="De-duplicate materials / images", default=True)
    instancing_mode: EnumProperty(
        name="Instancing",
        items=[("GN", "Geometry Nodes (fast)",
                "One object per unique model, LOD computed by Blender. Recommended for full maps (.mprt)"),
               ("EMPTY", "Empties (one object per prop)",
                "Every prop can be selected / edited. Slow above ~50,000 props")],
        default="GN")
    ent_path: StringProperty(
        name="Entities (.vpk / .ent)", subtype="FILE_PATH",
        description="Optional, loaded with the .mprt: the map's server VPK (R5Reloaded: "
                    "vpk/englishserver_<map>.bsp.pak000_dir.vpk) or an .ent file. Adds the dynamic props "
                    "(doors, loot bins, ziplines...) and the points of interest")
    ent_files: StringProperty(
        name="VPK .ent files", default="script",
        description="Which ones to read in the VPK, comma separated: script, snd, spawn, env, fx "
                    "(env and fx are compressed in R5Reloaded VPKs and cannot be read)")
    import_ziplines: BoolProperty(name="Create ziplines", default=True,
                                  description="Create the zipline cables as curves from the .ent files")
    setup_viewport_on_import: BoolProperty(
        name="Set up viewport after import", default=True,
        description="Solid shading with 'Texture' color, clipping distances suited to a large map")
    import_world: BoolProperty(
        name="Import map geometry", default=True,
        description="With an .mprt: also import the .cast with the same name (terrain, buildings)")
    world_split: EnumProperty(
        name="Geometry split",
        items=[("MATERIAL", "One object per material",
                "Same draw cost as a single object, and regions / Cycles only load the materials in view"),
               ("SINGLE", "Single object", "One object for the whole map geometry"),
               ("TILES", "Square tiles", "Slowest: every tile multiplies the draw calls per material")],
        default="MATERIAL")
    world_tile_size: FloatProperty(
        name="Tile size (m)", default=1024.0, min=1.0,
        description="Square tiles mode only (measured 62 fps in Solid with 256 m tiles vs 247 fps with a single "
                    "object)")

    # ---------------- Cast ----------------
    cast_load_textures: BoolProperty(name="Load textures", default=True)
    cast_alpha_mode: EnumProperty(
        name="Transparency",
        items=[("AUTO", "Auto (foliage, fences, decals...)",
                "Albedo alpha only for materials whose name suggests it"),
               ("ALWAYS", "Always", "Albedo alpha on every material"),
               ("NEVER", "Never", "")],
        default="AUTO",
        description="Apex albedos are all RGBA but the alpha is only transparency on some of them")
    cast_spec_to_metal: BoolProperty(
        name="Convert specular -> metal", default=True,
        description="Apex uses a specular/gloss workflow: metals have a black albedo and their color in the "
                    "specular map. Converts to Base Color / Metallic")
    cast_flip_normal_green: BoolProperty(name="Flip normal map green", default=True,
                                         description="Apex normal maps use the DirectX convention")
    cast_weld: FloatProperty(name="Weld vertices (game units)", default=0.01, min=0.0, precision=4,
                             description="Merge identical vertices (greatly reduces memory). 0 = off")
    cast_custom_normals: BoolProperty(name="Original normals", default=True,
                                      description="Use the game normals (more faithful, slightly heavier)")
    cast_smooth_by_angle: BoolProperty(name="Smooth shading", default=True)

    # ---------------- LOD ----------------
    use_lod: BoolProperty(name="Enable LODs", default=True, update=_lod_changed,
                          description="Off: every prop uses the fixed LOD below, without culling")
    forced_lod: IntProperty(name="Fixed LOD", default=0, min=0, max=7, update=_lod_changed,
                            description="Level used when LODs are disabled")
    max_lods: IntProperty(name="Max imported LODs", default=4, min=1, max=8,
                          description="Maximum number of LOD levels loaded per model")
    auto_generate_lods: BoolProperty(name="Generate missing LODs", default=True,
                                     description="Create LODs by decimation when a model only has one")
    auto_lod_ratio: FloatProperty(name="Decimation ratio", default=0.5, min=0.05, max=0.95,
                                  description="Each LOD keeps this ratio of the previous one's faces")
    auto_lod_min_faces: IntProperty(name="Min faces to generate", default=200, min=0)
    lod_distances: FloatVectorProperty(
        name="LOD distances (m)", size=4, default=(25.0, 60.0, 150.0, 400.0), min=0.0,
        update=_lod_changed, description="Distance at which LOD1, LOD2, LOD3, LOD4 start")
    lod_bias: FloatProperty(name="Quality bias", default=1.0, min=0.1, max=10.0, update=_lod_changed,
                            description=">1 = high quality further away, <1 = switch earlier")
    lod_size_scaling: BoolProperty(name="Scale with size", default=True, update=_lod_changed,
                                   description="Large objects keep their detail further away")
    lod_reference_size: FloatProperty(name="Reference size (m)", default=2.0, min=0.01, update=_lod_changed)
    use_bounds_level: BoolProperty(name="Bounding boxes far away", default=False, update=_lod_changed)
    bounds_distance: FloatProperty(name="Bounding box distance (m)", default=800.0, min=0.0, update=_lod_changed)
    use_culling: BoolProperty(name="Hide far away (distance culling)", default=False, update=_lod_changed,
                              description="Hide far props (smoother, but the map is no longer fully visible)")
    cull_distance: FloatProperty(name="Culling distance (m)", default=3000.0, min=0.0, update=_lod_changed)
    cull_small_props: BoolProperty(
        name="Unload small far-away objects", default=True, update=_lod_changed,
        description="Hide a prop once it is smaller than a percentage of the screen height (small objects "
                    "vanish quickly, large rocks stay visible very far away)")
    min_screen_pct: FloatProperty(
        name="Min. size on screen (%)", default=1.0, min=0.0, max=20.0, precision=2, update=_lod_changed,
        description="Minimum prop radius in % of half the screen height (1% ~ 5 pixel radius at 1080p). "
                    "Measured on World's Edge from the ground: 0.6% ~ 19 fps, 1% ~ 35 fps, 2% ~ 48 fps")
    use_frustum: BoolProperty(
        name="Hide outside the field of view", default=True, update=_lod_changed,
        description="Props behind the camera or off screen are not instanced (big FPS gain)")
    frustum_margin: FloatProperty(
        name="Field of view margin", default=1.5, min=1.0, max=3.0, update=_lod_changed,
        description="Widens the tested field of view so props do not pop in when turning the view")
    small_prop_size: FloatProperty(name="'Small' size (m)", default=0.5, min=0.0, update=_lod_changed)
    small_prop_distance: FloatProperty(name="Small props distance (m)", default=250.0, min=0.0,
                                       update=_lod_changed)
    lod_source: EnumProperty(
        name="Point of view",
        items=[("VIEWPORT", "Viewport", "Distance from the 3D view"),
               ("CAMERA", "Scene camera", "Distance from the active camera")],
        default="VIEWPORT", update=_lod_changed)
    lod_auto_update: BoolProperty(name="Auto update", default=True,
                                  description="Update the LODs continuously while navigating")
    update_interval: FloatProperty(
        name="Update while moving (s)", default=0.0, min=0.0, max=30.0,
        description="0 = never while the view moves (smooth navigation, update when it stops). Otherwise "
                    "force an update every N seconds of continuous movement (each update freezes ~0.3 s)")
    update_settle: FloatProperty(
        name="Delay after stopping (s)", default=0.25, min=0.0, max=2.0,
        description="Update once the view has been still for this long")
    update_move_threshold: FloatProperty(name="Move threshold (m)", default=2.0, min=0.0,
                                         description="Only update when the view moved more than this")
    update_angle_threshold: FloatProperty(name="Rotation threshold (°)", default=6.0, min=0.0, max=90.0,
                                          description="... or turned more than this")

    # ---------------- Render ----------------
    render_mode: EnumProperty(
        name="At render time",
        items=[("LOD0", "Everything in LOD0", "Maximum quality at render time"),
               ("CAMERA", "LOD from the camera", "Recompute the LODs from the camera on every frame"),
               ("KEEP", "Keep the viewport state", "")],
        default="CAMERA")
    render_frustum: BoolProperty(
        name="Field of view culling at render", default=False,
        description="Also hide off-screen props at render time (faster, but shadows and reflections of "
                    "off-screen objects disappear)")
    render_min_screen_pct: FloatProperty(
        name="Min. size on screen at render (%)", default=0.15, min=0.0, max=20.0, precision=2,
        description="Props smaller than this on the final image are skipped at render time (0 = keep all)")
    cycles_samples_preview: IntProperty(name="Preview samples", default=64, min=1)
    cycles_samples_final: IntProperty(name="Final samples", default=512, min=1)
    cycles_viewport_samples: IntProperty(name="Viewport samples", default=16, min=1)
    cycles_texture_limit: EnumProperty(name="Texture limit (Cycles)", items=_TEX_LIMITS, default="1024",
                                       description="Max texture size sent to Cycles (saves a lot of memory)")
    cycles_final_lod0: BoolProperty(name="Final: everything in LOD0", default=False,
                                    description="Final preset renders every prop at full detail (more memory)")

    # ---------------- Sky ----------------
    sky_mode: EnumProperty(
        name="Sky",
        items=[("PROCEDURAL", "Procedural sky + sun", "Physical sky (Nishita) with a sun lamp"),
               ("IMAGE", "Panorama / HDRI image", "Equirectangular image"),
               ("CUBEMAP", "Cubemap (6 faces)", "6 face images, e.g. a sky cubemap exported with RSX"),
               ("DOME", "In-game sky dome (round image)",
                "Square image with a round sky: zenith in the centre, horizon on the circle (Apex skybox "
                "textures)")],
        default="PROCEDURAL")
    sky_image: StringProperty(name="Image", subtype="FILE_PATH",
                              description="Panorama / HDRI, round sky-dome image, or ONE of the 6 cubemap faces "
                                          "(the others are found next to it)")
    sky_strength: FloatProperty(name="Sky strength", default=1.0, min=0.0, soft_max=10.0, update=_sky_changed)
    sky_image_rotation: FloatProperty(name="Rotation (°)", default=0.0, soft_min=-180, soft_max=180,
                                      update=_sky_changed)
    sky_dome_projection: EnumProperty(
        name="Dome projection",
        items=[("EQUIDISTANT", "Equidistant", "Distance from the centre proportional to the angle from the zenith"),
               ("EQUISOLID", "Equal area", "Fisheye keeping areas (more room near the horizon)"),
               ("ORTHO", "Planar (top view)", "Dome seen from above: the horizon is squeezed on the circle")],
        default="EQUIDISTANT", update=_sky_changed)
    sky_dome_horizon: FloatProperty(
        name="Horizon radius", default=1.0, min=0.3, max=1.5, subtype="FACTOR", update=_sky_changed,
        description="Where the horizon sits in the disc. Lower it when the mountains float above the horizon, "
                    "raise it when they are cut")
    sky_dome_mirror: BoolProperty(name="Mirror", default=False, update=_sky_changed,
                                  description="Flip east / west (images authored as seen from below)")
    sky_cubemap_y_up: BoolProperty(name="Faces authored Y-up", default=True,
                                   description="On for D3D / RSX cubemaps. Off for Z-up faces")
    sky_panorama_width: EnumProperty(name="Panorama width",
                                     items=[("2048", "2048", ""), ("4096", "4096", ""), ("8192", "8192", "")],
                                     default="4096")
    sky_sun_elevation: FloatProperty(name="Sun elevation (°)", default=35.0, min=-10.0, max=90.0)
    sky_sun_rotation: FloatProperty(name="Sun rotation (°)", default=40.0, soft_min=-180, soft_max=180)
    sky_sun_strength: FloatProperty(name="Sun strength", default=4.0, min=0.0)
    sky_haze: FloatProperty(name="Haze", default=1.5, min=0.0, max=10.0)
    sky_add_sun: BoolProperty(name="Add a sun lamp", default=True)
    sky_use_map_sun: BoolProperty(name="Use the map's sun", default=True,
                                  description="Use light_environment from an .ent file when one was loaded")
    map_sun_json: StringProperty(default="", options={"HIDDEN"})

    # ---------------- Regions / POI ----------------
    pois: CollectionProperty(type=R5_POI)
    poi_index: IntProperty(default=0)
    poi_default_radius: FloatProperty(name="Default radius", default=150.0, min=5.0, subtype="DISTANCE")
    region_scale: FloatProperty(name="Radius multiplier", default=1.0, min=0.1, max=10.0,
                                description="Enlarge / shrink the shown area around the POI")
    grid_size: IntProperty(name="Grid", default=6, min=2, max=26)


def register():
    bpy.utils.register_class(R5_Settings)
    bpy.types.Scene.r5_settings = bpy.props.PointerProperty(type=R5_Settings)


CLASSES = (R5_Settings,)


def unregister():
    if hasattr(bpy.types.Scene, "r5_settings"):
        del bpy.types.Scene.r5_settings
    bpy.utils.unregister_class(R5_Settings)
