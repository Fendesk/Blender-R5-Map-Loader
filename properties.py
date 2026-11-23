import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, StringProperty)

from . import lod_system
from .regions import R5_POI
from .lights import R5_LightCat


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
    """Value change: applied in place (sun, atmosphere, fog, strength, rotation)."""
    from . import sky
    try:
        sky.update_live(context.scene)
    except Exception as exc:  # noqa: BLE001
        print(f"[R5] sky: {exc}")


_SUN_SYNC = [False]


def sync_sun_sliders(s):
    """Copy the map's sun direction into the elevation / rotation sliders (without leaving the map sun)."""
    from . import sky
    import math
    got = sky._map_sun(s)
    if not got:
        return
    _SUN_SYNC[0] = True
    try:
        s.sky_sun_elevation = max(-10.0, min(90.0, math.degrees(got[0])))
        s.sky_sun_rotation = (math.degrees(got[1]) + 180.0) % 360.0 - 180.0
    finally:
        _SUN_SYNC[0] = False


def _sun_angle_changed(self, context):
    """Moving the sun by hand leaves the map's sun (otherwise the sliders would have no effect)."""
    if not _SUN_SYNC[0] and self.sky_use_map_sun and self.map_sun_json:
        _SUN_SYNC[0] = True
        try:
            self.sky_use_map_sun = False
        finally:
            _SUN_SYNC[0] = False
    _sky_changed(self, context)


def _use_map_sun_changed(self, context):
    if self.sky_use_map_sun and not _SUN_SYNC[0]:
        sync_sun_sliders(self)
    _sky_changed(self, context)


def _sky_rebuild(self, context):
    """Dome mapping change: the world nodes are rebuilt."""
    from . import sky
    try:
        sky.refresh(context.scene)
    except Exception as exc:  # noqa: BLE001
        print(f"[R5] sky: {exc}")


def _movers_speed_changed(self, context):
    from . import motion
    motion.update_movers_speed(context.scene)


def _lod_fade_changed(self, context):
    from . import motion, gn_instancer
    try:
        if self.lod_fade:
            motion.ensure_lod_fade(context.scene)
        else:
            motion.remove_lod_fade()
        gn_instancer.apply_settings(context.scene)
    except Exception as exc:  # noqa: BLE001
        print(f"[R5] LOD fade: {exc}")


def _lod_fade_width_changed(self, context):
    from . import gn_instancer
    gn_instancer.apply_settings(context.scene)


_TEX_LIMITS = [("OFF", "No limit", ""), ("4096", "4096", ""), ("2048", "2048", ""), ("1024", "1024", ""),
               ("512", "512", "")]


class R5_Settings(bpy.types.PropertyGroup):
    # (0.13: the "Import from the game" mode was removed; maps are imported from exports only)

    # ---------------- Visibility ----------------
    hide_trigger: BoolProperty(name="Hide triggers", default=True, update=_vis_changed,
                               description="toolstrigger, fog volumes...")
    hide_tool: BoolProperty(name="Hide tool brushes", default=True, update=_vis_changed,
                            description="clip, blocklight, skybox, nodraw, invisible...")
    hide_fx: BoolProperty(name="Hide light shafts & atmosphere", default=True, update=_vis_changed,
                          description="Godray / light shaft / atmosphere volume props. In game they are additive "
                                      "translucent effects; in Blender they show up as large ellipses / cones")
    hide_particle: BoolProperty(name="Hide particle / mist cards", default=True, update=_vis_changed,
                                description="Crossed planes with a mist / steam texture that are animated particles "
                                            "in game, e.g. the big mist next to the climatizer vents")
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
    lift_above_zero: BoolProperty(
        name="Map above Z = 0", default=True,
        description="Move the map up or down so that its lowest point sits on the ground grid (the maps are "
                    "built far below or above it in game). Sky / vista models and tool brushes are ignored")
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
    auto_entities: BoolProperty(
        name="Export's entities", default=True,
        description="With an .mprt and an empty Entities field: also load entities/<map>_script.ent next to it "
                    "(r5mex exports): doors, loot bins, evac towers, ziplines, POIs")
    texture_other_exports: BoolProperty(
        name="Textures from other exports", default=True,
        description="A material exported without its textures takes them, by material name, from the other map "
                    "exports next to this one (same folder or one level up). R5Flowstate S30 exports have some "
                    "trims without textures (s2s_metal_trims_02_vc, district_bronze_trim_01, trim_metal_01...) "
                    "that the S21 and retail exports have")
    import_skybox: BoolProperty(
        name="3D skybox", default=True,
        description="With an .mprt: also load <map>_skybox.mprt next to it (r5mex exports): the distant scenery "
                    "around the map, at the size and place the game shows it (tens of km away). A *_skybox.mprt "
                    "can also be imported on its own")
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
    auto_lod_min_ratio: FloatProperty(
        name="Min. ratio", default=0.25, min=0.01, max=1.0,
        description="Generated LODs never keep fewer faces than this ratio of the model (0.5 per level would "
                    "leave 12.5% at LOD3 and make holes in the meshes)")
    auto_lod_max_radius: FloatProperty(
        name="Keep full detail above (m)", default=10.0, min=0.0, subtype="DISTANCE",
        description="Models with a larger radius get no decimated LODs: they are seen from far away and "
                    "decimation makes holes in them")
    auto_lod_skip_names: StringProperty(
        name="Never decimate", description="Comma separated words of model names that keep their full mesh "
                                           "(architecture: decimation makes holes in thin walls and façades). "
                                           "* = wildcard",
        default="bldg*,building*,wall*,terrain*,cliff*,rock*,floor*,roof*,stair*,bridge*,facade*,pillar*,"
                "column*,arch*,tower*,house*,ground*,road*,platform*,dock*,pier*,fence*,window*,door*")
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
    cull_keep_radius: FloatProperty(
        name="Always keep props larger than (m)", default=10.0, min=0.0, subtype="DISTANCE", update=_lod_changed,
        description="Props with a radius (model radius x prop scale) above this are never hidden by their size "
                    "on screen (buildings stay visible from far away). Also at render time")
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
        default="CAMERA", update=_lod_changed)
    render_frustum: BoolProperty(
        name="Field of view culling at render", default=False, update=_lod_changed,
        description="Also hide off-screen props at render time (faster, but shadows and reflections of "
                    "off-screen objects disappear)")
    render_min_screen_pct: FloatProperty(
        name="Min. size on screen at render (%)", default=0.15, min=0.0, max=20.0, precision=2, update=_lod_changed,
        description="Props smaller than this on the final image are skipped at render time (0 = keep all)")
    lod_fade: BoolProperty(
        name="Smooth LOD transitions", default=False, update=_lod_fade_changed,
        description="Animation renders: a prop changing LOD is drawn in both LODs for a few frames and cross-fades "
                    "(dithered) instead of popping. Adds a small mask to the prop materials. Geometry Nodes "
                    "instancing and 'LOD from the camera' only")
    lod_fade_width: FloatProperty(
        name="Transition band", default=0.2, min=0.02, max=0.6, subtype="FACTOR", update=_lod_fade_width_changed,
        description="Width of the cross-fade around each LOD distance, as a fraction of the distance (0.2: from "
                    "-20% to +20%). Wider = smoother but more props drawn twice")
    lod_fade_viewport: BoolProperty(
        name="Also in the viewport", default=False, update=_lod_fade_width_changed,
        description="Cross-fade in the viewport too (props in the band are drawn twice: a bit slower)")
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
        default="EQUIDISTANT", update=_sky_rebuild)
    sky_dome_horizon: FloatProperty(
        name="Horizon radius", default=1.0, min=0.3, max=1.5, subtype="FACTOR", update=_sky_rebuild,
        description="Where the horizon sits in the disc. Lower it when the mountains float above the horizon, "
                    "raise it when they are cut")
    sky_dome_mirror: BoolProperty(name="Mirror", default=False, update=_sky_rebuild,
                                  description="Flip east / west (images authored as seen from below)")
    sky_cubemap_y_up: BoolProperty(name="Faces authored Y-up", default=True,
                                   description="On for D3D / RSX cubemaps. Off for Z-up faces")
    sky_panorama_width: EnumProperty(name="Panorama width",
                                     items=[("2048", "2048", ""), ("4096", "4096", ""), ("8192", "8192", "")],
                                     default="4096")
    sky_sun_elevation: FloatProperty(name="Sun elevation (°)", default=35.0, min=-10.0, max=90.0,
                                     update=_sun_angle_changed,
                                     description="Height of the sun above the horizon. Changing it leaves the "
                                                 "map's sun")
    sky_sun_rotation: FloatProperty(name="Sun rotation (°)", default=40.0, soft_min=-180, soft_max=180,
                                    update=_sun_angle_changed,
                                    description="Direction the sun comes from. Changing it leaves the map's sun")
    sky_sun_strength: FloatProperty(name="Sun strength", default=4.0, min=0.0, update=_sky_changed)
    sky_sun_color: FloatVectorProperty(
        name="Sun colour", subtype="COLOR", size=3, min=0.0, max=1.0, default=(1.0, 0.96, 0.9),
        update=_sky_changed, description="Colour of the sun lamp. Reset = the map's sun colour when an env .ent "
                                         "with light_environment was loaded, otherwise warm white")
    # Atmosphere (Blender's physical sky: same parameters as the Procedural sky)
    sky_atmosphere: BoolProperty(
        name="Atmosphere", default=False, update=_sky_changed,
        description="Blend a physical atmosphere (air, dust, ozone) over the image sky, strongest towards the "
                    "horizon")
    sky_atmosphere_strength: FloatProperty(name="Atmosphere strength", default=0.5, min=0.0, max=1.0,
                                           subtype="FACTOR", update=_sky_changed)
    sky_air_density: FloatProperty(
        name="Air (gas)", default=1.0, min=0.0, max=10.0, update=_sky_changed,
        description="Air molecules (Rayleigh scattering): blue sky, orange sunsets")
    sky_dust_density: FloatProperty(
        name="Dust / haze", default=1.0, min=0.0, max=10.0, update=_sky_changed,
        description="Dust and water droplets (Mie scattering): whitish haze and glow around the sun")
    sky_ozone_density: FloatProperty(
        name="Ozone", default=1.0, min=0.0, max=10.0, update=_sky_changed,
        description="Ozone layer: absorbs orange light, deeper blue sky")
    # Fog (volume box around the map)
    sky_fog: BoolProperty(name="Volumetric fog", default=False, update=_sky_changed,
                          description="Height fog filling the map, lit by the sun and the sky (a volume box around "
                                      "the map). Starts from the map's own fog when the export has it. Heavier to "
                                      "render")
    sky_fog_density: FloatProperty(
        name="Density", default=0.0004, min=0.0, soft_max=0.01, precision=5, step=0.001, update=_sky_changed,
        description="Per metre, at the fog base. 0.0004 = about 1/3 of the light gone over 1 km")
    sky_fog_base: FloatProperty(name="Base height", default=0.0, subtype="DISTANCE", update=_sky_changed,
                                description="Height where the fog is densest (below it, the fog stays as dense)")
    sky_fog_thickness: FloatProperty(name="Thickness", default=80.0, min=1.0, soft_max=2000.0, subtype="DISTANCE",
                                     update=_sky_changed,
                                     description="The fog gets half as dense every this many metres above its base "
                                                 "(low: ground mist; high: an even haze)")
    sky_fog_above: FloatProperty(name="Above the layer", default=0.15, min=0.0, max=1.0, subtype="FACTOR",
                                 update=_sky_changed,
                                 description="Density kept high above the layer, relative to the base: 0, the fog "
                                             "only covers the ground; 1, an even fog at every height")
    sky_fog_patchiness: FloatProperty(name="Patches", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                                      update=_sky_changed,
                                      description="0: even fog; 1: drifting banks of mist with clear gaps")
    sky_fog_patch_size: FloatProperty(name="Patch size", default=120.0, min=1.0, soft_max=1000.0,
                                      subtype="DISTANCE", update=_sky_changed)
    sky_fog_wind: FloatProperty(name="Wind", default=1.5, soft_min=0.0, soft_max=20.0, update=_sky_changed,
                                description="Speed the patches drift at when the timeline plays (m/s)")
    sky_fog_color: FloatVectorProperty(name="Colour", subtype="COLOR", size=3, min=0.0, max=1.0,
                                       default=(0.75, 0.82, 0.9), update=_sky_changed)
    sky_fog_anisotropy: FloatProperty(name="Sun glow", default=0.4, min=-0.9, max=0.9, update=_sky_changed,
                                      description="Anisotropy: > 0, the fog glows towards the sun (forward "
                                                  "scattering, like real mist)")
    sky_fog_glow: FloatProperty(name="Self-lit", default=0.0, min=0.0, soft_max=2.0, precision=3, step=1,
                                update=_sky_changed,
                                description="Brightness the fog takes by itself, in its colour, whatever the light "
                                            "(0: lit by the sun and the sky only). The map's fog sets it to the "
                                            "game's fog brightness: the city glow in the night fog of E-District")
    sky_fog_distance: FloatProperty(name="Range (EEVEE)", default=3000.0, min=10.0, subtype="DISTANCE",
                                    update=_sky_changed,
                                    description="EEVEE only computes volumes up to this distance")
    sky_add_sun: BoolProperty(name="Add a sun lamp", default=True, update=_sky_changed)
    sky_use_map_sun: BoolProperty(name="Use the map's sun", default=True, update=_use_map_sun_changed,
                                  description="Use light_environment from an .ent file when one was loaded")
    map_sun_json: StringProperty(default="", options={"HIDDEN"})
    # City lights (lights.py): one entry per light type found in the map
    light_cats: CollectionProperty(type=R5_LightCat)

    # ---------------- Movers / Shaders (motion.py) ----------------
    movers_speed: FloatProperty(name="Movers speed", default=1.0, soft_min=0.0, soft_max=5.0, min=-20.0, max=20.0,
                                update=_movers_speed_changed,
                                description="Speed of the moving props (1 = about the game speed, negative = "
                                            "reverse)")
    shader_speed: FloatProperty(name="Shaders speed", default=1.0, soft_min=0.0, soft_max=5.0, min=-20.0, max=20.0,
                                description="Global speed of the animated materials")
    speed_water: FloatProperty(name="Water", default=1.0, soft_min=-5.0, soft_max=5.0,
                               description="Water, waterfalls, fountains (negative = other direction)")
    speed_lava: FloatProperty(name="Lava", default=1.0, soft_min=-5.0, soft_max=5.0)
    speed_screens: FloatProperty(name="Screens / holograms", default=1.0, soft_min=-5.0, soft_max=5.0)
    speed_energy: FloatProperty(name="Energy", default=1.0, soft_min=-5.0, soft_max=5.0,
                                description="Energy domes, electric rings, zipline magnets")
    speed_effects: FloatProperty(name="Smoke / fire / clouds", default=1.0, soft_min=-5.0, soft_max=5.0)
    anim_foliage: BoolProperty(name="Wind in the foliage", default=True,
                               description="Grass, leaves, plants and trees sway (vertex displacement: the heaviest "
                                           "part, especially in Cycles)")
    wind_speed: FloatProperty(name="Wind speed", default=1.0, soft_min=0.0, soft_max=5.0)
    wind_strength: FloatProperty(name="Wind strength", default=1.0, min=0.0, soft_max=5.0,
                                 description="1 = about 12 cm of sway at the top of a 10 m tree")

    # ---------------- Regions / POI ----------------
    pois: CollectionProperty(type=R5_POI)
    poi_index: IntProperty(default=0)
    poi_default_radius: FloatProperty(name="Default radius", default=150.0, min=5.0, subtype="DISTANCE")
    region_scale: FloatProperty(name="Radius multiplier", default=1.0, min=0.1, max=10.0,
                                description="Enlarge / shrink the shown area around the POI")
    grid_size: IntProperty(name="Grid", default=6, min=2, max=26)


def register():
    bpy.utils.register_class(R5_LightCat)
    bpy.utils.register_class(R5_Settings)
    bpy.types.Scene.r5_settings = bpy.props.PointerProperty(type=R5_Settings)


CLASSES = (R5_LightCat, R5_Settings)


def unregister():
    if hasattr(bpy.types.Scene, "r5_settings"):
        del bpy.types.Scene.r5_settings
    bpy.utils.unregister_class(R5_Settings)
    if getattr(R5_LightCat, "is_registered", False):
        bpy.utils.unregister_class(R5_LightCat)
