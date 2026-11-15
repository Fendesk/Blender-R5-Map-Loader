"""Cycles setup for a full map.

Cycles handles the Geometry Nodes instances natively (each unique mesh is stored once), so the
whole map can be path traced. What this preset takes care of:
  - GPU when one is enabled in Preferences > System > Cycles Render Devices,
  - samples + denoising (render and viewport), adaptive sampling,
  - light paths suited to a large outdoor map, with enough TRANSPARENT bounces for foliage /
    fences (alpha cutouts need them, otherwise they render black),
  - texture size limit at render time (a full map uses thousands of 2K textures),
  - persistent data (faster re-renders / animations),
  - LOD at render time: from the camera, without field-of-view culling (props behind the camera
    still cast shadows and show in reflections); only props that are tiny on screen are dropped,
  - a sky + sun when the scene has no world lighting.
"""

import bpy

from . import sky


def gpu_available(context):
    addon = context.preferences.addons.get("cycles")
    if addon is None:
        return False, "NONE"
    prefs = addon.preferences
    try:
        prefs.refresh_devices()
    except (AttributeError, RuntimeError):
        pass
    kind = getattr(prefs, "compute_device_type", "NONE")
    if kind == "NONE":
        return False, kind
    return any(d.use and d.type != "CPU" for d in prefs.devices), kind


def setup_cycles(context, preset):
    scene = context.scene
    s = scene.apx_settings
    r = scene.render
    r.engine = "CYCLES"
    c = scene.cycles
    has_gpu, kind = gpu_available(context)
    c.device = "GPU" if has_gpu else "CPU"

    final = preset == "FINAL"
    c.samples = s.cycles_samples_final if final else s.cycles_samples_preview
    c.preview_samples = s.cycles_viewport_samples
    c.use_adaptive_sampling = True
    c.adaptive_threshold = 0.01 if final else 0.03
    c.use_denoising = True
    c.use_preview_denoising = True

    c.max_bounces = 8 if final else 4
    c.diffuse_bounces = 3 if final else 2
    c.glossy_bounces = 3 if final else 2
    c.transmission_bounces = 6 if final else 4
    c.volume_bounces = 0
    c.transparent_max_bounces = 64 if final else 32
    c.caustics_reflective = False
    c.caustics_refractive = False
    c.sample_clamp_indirect = 10.0

    if s.cycles_texture_limit != "OFF":
        r.use_simplify = True
        c.texture_limit_render = s.cycles_texture_limit
        c.texture_limit = s.cycles_texture_limit
    r.use_persistent_data = True
    c.use_auto_tile = True
    c.tile_size = 2048

    # LOD at render time
    s.render_mode = "LOD0" if (final and s.cycles_final_lod0) else "CAMERA"
    s.render_frustum = False

    if scene.world is None or not scene.world.use_nodes:
        sky.setup_procedural(scene)
    return has_gpu, kind


class R5_OT_setup_cycles(bpy.types.Operator):
    """Configure Cycles for rendering the map (heavy)"""
    bl_idname = "apx.setup_cycles"
    bl_label = "Set up Cycles"
    bl_options = {"REGISTER", "UNDO"}

    preset: bpy.props.EnumProperty(
        items=[("PREVIEW", "Preview", "Fast settings for look-dev and the Rendered viewport"),
               ("FINAL", "Final", "High quality settings for the final render")],
        default="PREVIEW")

    def execute(self, context):
        has_gpu, kind = setup_cycles(context, self.preset)
        if has_gpu:
            self.report({"INFO"}, f"Cycles {self.preset.lower()} preset applied (GPU, {kind})")
        else:
            self.report({"WARNING"}, "Cycles preset applied on CPU: enable your GPU in Preferences > System > "
                                     "Cycles Render Devices for much faster renders")
        return {"FINISHED"}


def register():
    bpy.utils.register_class(R5_OT_setup_cycles)


def unregister():
    bpy.utils.unregister_class(R5_OT_setup_cycles)
