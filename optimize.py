"""Scene optimization tools."""

import re

import bpy
import bmesh

from . import library

_DUP_RE = re.compile(r"^(.*)\.\d{3}$")


def dedupe_materials():
    """Replace 'Mat.001', 'Mat.002'... with 'Mat' when the original exists."""
    n = 0
    for mat in list(bpy.data.materials):
        m = _DUP_RE.match(mat.name)
        if not m:
            continue
        orig = bpy.data.materials.get(m.group(1))
        if orig is None or orig == mat:
            continue
        mat.user_remap(orig)
        bpy.data.materials.remove(mat)
        n += 1
    return n


def dedupe_images():
    """Merge images that point to the same file."""
    n = 0
    seen = {}
    for img in list(bpy.data.images):
        if not img.filepath:
            continue
        key = bpy.path.abspath(img.filepath).lower()
        if key in seen and seen[key] != img:
            img.user_remap(seen[key])
            bpy.data.images.remove(img)
            n += 1
        else:
            seen[key] = img
    return n


def purge_orphans():
    def total():
        return sum(len(c) for c in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.objects))
    before = total()
    try:
        bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    except TypeError:
        bpy.data.orphans_purge()
    return before - total()


def merge_library_vertices(dist):
    lib = bpy.data.collections.get(library.LIB_COLLECTION)
    if lib is None:
        return 0
    done, removed = set(), 0
    for o in lib.all_objects:
        if o.type != "MESH" or o.data in done:
            continue
        done.add(o.data)
        bm = bmesh.new()
        bm.from_mesh(o.data)
        v0 = len(bm.verts)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
        removed += v0 - len(bm.verts)
        bm.to_mesh(o.data)
        bm.free()
    return removed


def library_stats():
    lib = bpy.data.collections.get(library.LIB_COLLECTION)
    models = len(lib.children) if lib else 0
    tris0 = 0
    if lib:
        for c in bpy.data.collections:
            if c.name.endswith("__LOD0"):
                for o in c.objects:
                    if o.type == "MESH":
                        tris0 += sum(len(p.vertices) - 2 for p in o.data.polygons)
    instances = sum(1 for o in bpy.data.objects if "apx_model" in o)
    instances += sum(len(o.data.vertices) for o in bpy.data.objects if "apx_gn" in o and o.type == "MESH")
    return models, instances, tris0


def setup_viewport_for_large_map(clip_end=20000.0):
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                sp = area.spaces.active
                sp.clip_start = 0.5   # a too small clip_start makes far geometry flicker
                sp.clip_end = clip_end
                try:
                    sp.shading.color_type = "TEXTURE"   # otherwise Solid mode shows flat grey
                except (AttributeError, TypeError):
                    pass
    scene = bpy.context.scene
    if scene.camera and scene.camera.type == "CAMERA":
        scene.camera.data.clip_end = max(scene.camera.data.clip_end, clip_end)


class R5_OT_optimize(bpy.types.Operator):
    """De-duplicate materials / images, merge vertices and purge orphan data"""
    bl_idname = "apx.optimize"
    bl_label = "Optimize scene"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        from . import lod_system
        s = context.scene.apx_settings
        m = dedupe_materials()
        i = dedupe_images()
        v = merge_library_vertices(s.merge_distance) if s.merge_distance > 0 else 0
        p = purge_orphans()
        lod_system.invalidate()
        self.report({"INFO"}, f"Materials merged: {m}, images: {i}, vertices: {v}, data purged: {p}")
        return {"FINISHED"}


class R5_OT_texture_limit(bpy.types.Operator):
    """Limit the texture resolution in the viewport (Blender preference: Viewport > Texture Limit).
Keeps video memory from filling up, which makes textures disappear"""
    bl_idname = "apx.texture_limit"
    bl_label = "Apply texture limit"

    def execute(self, context):
        s = context.scene.apx_settings
        context.preferences.system.gl_texture_limit = s.texture_limit
        self.report({"INFO"}, f"Viewport texture limit: {s.texture_limit.replace('CLAMP_', '')}")
        return {"FINISHED"}


class R5_OT_viewport_setup(bpy.types.Operator):
    """Set the viewport / camera clipping for a large map and show textures in Solid mode"""
    bl_idname = "apx.viewport_setup"
    bl_label = "Set up viewport (large map)"

    def execute(self, context):
        setup_viewport_for_large_map()
        return {"FINISHED"}


CLASSES = (R5_OT_optimize, R5_OT_texture_limit, R5_OT_viewport_setup)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
