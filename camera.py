"""Cameras from the viewport: a new camera (or the active one) placed exactly where the 3D view looks from."""
import bpy
from bpy.props import BoolProperty

COLLECTION = "R5 Cameras"        # not a map root: replacing the map keeps the cameras


def _view3d(context):
    """(area, space, region_3d) of the 3D view the operator runs in, or the largest one of the window."""
    sp = context.space_data
    if sp is not None and sp.type == "VIEW_3D" and context.region_data is not None:
        return context.area, sp, context.region_data
    best = None
    for area in (context.window.screen.areas if context.window else ()):
        if area.type == "VIEW_3D" and (best is None or area.width * area.height > best.width * best.height):
            best = area
    if best is None:
        return None, None, None
    space = best.spaces.active
    return best, space, space.region_3d


def _collection(scene):
    col = bpy.data.collections.get(COLLECTION)
    if col is None:
        col = bpy.data.collections.new(COLLECTION)
    if col.name not in scene.collection.children:
        scene.collection.children.link(col)
    return col


def _copy_view(cam_ob, area, space, r3d, scene):
    """Put the camera object and its lens where the viewport looks from (perspective, ortho or a camera view)."""
    cd = cam_ob.data
    src = scene.camera if r3d.view_perspective == "CAMERA" else None
    if src is not None and src is not cam_ob:
        # the view looks through a camera: copy that camera (the view adds nothing)
        cam_ob.matrix_world = src.matrix_world.copy()
        if src.type == "CAMERA":
            for k in ("type", "lens", "lens_unit", "sensor_fit", "sensor_width", "sensor_height", "ortho_scale",
                      "shift_x", "shift_y", "clip_start", "clip_end"):
                setattr(cd, k, getattr(src.data, k))
        return
    cam_ob.matrix_world = r3d.view_matrix.inverted()
    cd.sensor_fit = "AUTO"
    cd.sensor_width = 36.0
    cd.shift_x = cd.shift_y = 0.0
    if r3d.view_perspective == "ORTHO":
        cd.type = "ORTHO"
        # an orthographic view shows about 2 x view_distance across its larger side (36 mm / lens zoom)
        cd.ortho_scale = r3d.view_distance * 72.0 / space.lens
    else:
        cd.type = "PERSP"
        # the viewport draws with a 72 mm sensor: same field of view as half the lens on 36 mm
        cd.lens = space.lens / 2.0
    cd.clip_start = space.clip_start
    cd.clip_end = space.clip_end


class R5_OT_camera_from_view(bpy.types.Operator):
    """Create a camera exactly where the 3D view looks from (position, angle, focal length, clipping)"""
    bl_idname = "r5.camera_from_view"
    bl_label = "New camera from view"
    bl_options = {"REGISTER", "UNDO"}

    new: BoolProperty(name="New camera", default=True,
                      description="Create a new camera (off: move the active camera to the view)")
    set_active: BoolProperty(name="Make it the active camera", default=True,
                             description="The scene renders through this camera")
    match_aspect: BoolProperty(name="Match the viewport shape", default=False,
                               description="Set the render resolution to the viewport's width / height ratio "
                                           "(keeps the vertical size), so the camera frames exactly what you see")
    look_through: BoolProperty(name="Look through it", default=False,
                               description="Switch the 3D view to this camera")

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def execute(self, context):
        scene = context.scene
        area, space, r3d = _view3d(context)
        if r3d is None:
            self.report({"ERROR"}, "No 3D view to copy")
            return {"CANCELLED"}
        if self.new or scene.camera is None or scene.camera.type != "CAMERA":
            data = bpy.data.cameras.new("R5 Camera")
            ob = bpy.data.objects.new("R5 Camera", data)
            _collection(scene).objects.link(ob)
            created = True
        else:
            ob = scene.camera
            created = False
        _copy_view(ob, area, space, r3d, scene)
        if self.match_aspect and area is not None:
            win = next((r for r in area.regions if r.type == "WINDOW"), None)
            if win is not None and win.width > 0 and win.height > 0:
                rd = scene.render
                rd.resolution_x = max(4, round(rd.resolution_y * win.width / win.height))
        if self.set_active or created and scene.camera is None:
            scene.camera = ob
        if created:
            for o in context.selected_objects:
                o.select_set(False)
            try:
                ob.select_set(True)
                context.view_layer.objects.active = ob
            except RuntimeError:
                pass                 # the collection is excluded from the view layer
        if self.look_through and scene.camera is ob:
            r3d.view_perspective = "CAMERA"
        self.report({"INFO"}, f"{'Created' if created else 'Moved'} {ob.name}"
                              + (" (active camera)" if scene.camera is ob else ""))
        return {"FINISHED"}


def _menu(self, context):
    self.layout.separator()
    self.layout.operator(R5_OT_camera_from_view.bl_idname, icon="OUTLINER_OB_CAMERA")


CLASSES = (R5_OT_camera_from_view,)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.VIEW3D_MT_view_cameras.append(_menu)


def unregister():
    try:
        bpy.types.VIEW3D_MT_view_cameras.remove(_menu)
    except Exception:  # noqa: BLE001
        pass
    for c in reversed(CLASSES):
        if getattr(c, "is_registered", False):
            bpy.utils.unregister_class(c)
