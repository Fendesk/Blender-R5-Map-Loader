"""Model library: indexes the exported files (RSX, Legion+...), imports each model once and
builds its LOD collections.

Each unique model is imported ONCE into the hidden "APX_Library" collection. Every LOD level
is a sub-collection "<model>__LOD<n>". Map props are collection instances, so geometry is
never duplicated in memory.
"""

import math
import os
import re

import bpy
import bmesh
import numpy as np
from mathutils import Matrix

from .castfile import GROUP_KEY, is_fx_model, is_vfx_model, make_vfx

SUPPORTED_EXT = (".cast", ".glb", ".gltf", ".fbx", ".obj", ".semodel", ".smd")
LIB_COLLECTION = "APX_Library"

_LOD_FILE_RE = re.compile(r"^(?P<base>.+?)[_\-\. ]?lod[_\-]?(?P<lod>\d+)$", re.IGNORECASE)
_LOD_OBJ_RE = re.compile(r"lod[_\-]?(\d+)", re.IGNORECASE)


# --------------------------------------------------------------------------
# Model folder index
# --------------------------------------------------------------------------
def build_index(root):
    """Return {model_key: {lod: path}}. Extension preference = SUPPORTED_EXT order.
    `root` may list several folders separated by ";": the first folder holding a model wins."""
    if ";" in root:
        merged = {}
        for r in (p.strip() for p in root.split(";")):
            if r and os.path.isdir(r):
                for k, v in build_index(r).items():
                    merged.setdefault(k, v)
        return merged
    index = {}
    prio = {e: i for i, e in enumerate(SUPPORTED_EXT)}
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            stem, ext = os.path.splitext(fn)
            ext = ext.lower()
            if ext not in prio:
                continue
            m = _LOD_FILE_RE.match(stem)
            if m:
                base, lod = m.group("base").lower(), int(m.group("lod"))
            else:
                base, lod = stem.lower(), 0
            path = os.path.join(dirpath, fn)
            slot = index.setdefault(base, {})
            old = slot.get(lod)
            if old is None or prio[ext] < prio[os.path.splitext(old)[1].lower()]:
                slot[lod] = path
    return index


# --------------------------------------------------------------------------
# Collections
# --------------------------------------------------------------------------
def get_library_collection(scene):
    lib = bpy.data.collections.get(LIB_COLLECTION)
    if lib is None:
        lib = bpy.data.collections.new(LIB_COLLECTION)
    if lib.name not in scene.collection.children:
        scene.collection.children.link(lib)
    # Excluded from the view layer: the library is never drawn or evaluated directly
    _set_excluded(bpy.context.view_layer.layer_collection, lib.name, True)
    return lib


def _set_excluded(layer_coll, name, state):
    if layer_coll.collection.name == name:
        layer_coll.exclude = state
        return True
    for c in layer_coll.children:
        if _set_excluded(c, name, state):
            return True
    return False


def lod_collection_name(key, lod):
    return f"{key}__LOD{lod}"


def get_model_lods(key):
    """Ordered list of the existing LOD collections of a model."""
    out = []
    i = 0
    while True:
        c = bpy.data.collections.get(lod_collection_name(key, i))
        if c is None:
            break
        out.append(c)
        i += 1
    return out


def bounds_collection(key):
    return bpy.data.collections.get(f"{key}__BOUNDS")


# --------------------------------------------------------------------------
# File import
# --------------------------------------------------------------------------
def _import_file(path):
    """Import one file and return the new objects."""
    before = set(bpy.data.objects)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif ext == ".obj":
        if hasattr(bpy.ops.wm, "obj_import"):
            bpy.ops.wm.obj_import(filepath=path)
        else:
            bpy.ops.import_scene.obj(filepath=path)
    elif ext == ".cast":
        from . import castfile      # built-in reader (the Cast add-on is not needed)
        return castfile.import_model_cast(path, bpy.context.scene.r5_settings)
    elif ext == ".semodel":
        if not hasattr(bpy.ops.import_scene, "semodel"):
            raise RuntimeError("The 'SEModel' add-on is required for .semodel files")
        bpy.ops.import_scene.semodel(filepath=path)
    elif ext == ".smd":
        if not hasattr(bpy.ops.import_scene, "smd"):
            raise RuntimeError("The 'Blender Source Tools' add-on is required for .smd files")
        bpy.ops.import_scene.smd(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


def _strip_to_meshes(objs):
    """Keep meshes only, with transforms applied and no armature."""
    meshes = []
    for o in objs:
        if o.type == "MESH":
            mw = o.matrix_world.copy()
            o.parent = None
            o.matrix_world = mw
            for m in list(o.modifiers):
                if m.type == "ARMATURE":
                    o.modifiers.remove(m)
            if o.data.shape_keys:
                o.shape_key_clear()
            meshes.append(o)
    for o in objs:
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    # Bake the transform into the geometry (the model must sit at identity)
    for o in meshes:
        if o.data.users > 1:
            o.data = o.data.copy()
        o.data.transform(o.matrix_world)
        o.matrix_world = Matrix.Identity(4)
    return meshes


def _join(objs, name):
    """Merge several meshes into one object without bpy.ops (fast, no context needed)."""
    if len(objs) == 1:
        objs[0].name = name
        objs[0].data.name = name
        return objs[0]
    bm = bmesh.new()
    mats = []
    mat_index = {}
    for o in objs:
        me = o.data
        remap = []
        for slot in me.materials:
            key = slot.name if slot else None
            if key not in mat_index:
                mat_index[key] = len(mats)
                mats.append(slot)
            remap.append(mat_index[key])
        tmp = bmesh.new()
        tmp.from_mesh(me)
        for f in tmp.faces:
            if remap:
                f.material_index = remap[min(f.material_index, len(remap) - 1)]
        tmp.to_mesh(me)
        tmp.free()
        bm.from_mesh(me)
    new_me = bpy.data.meshes.new(name)
    bm.to_mesh(new_me)
    bm.free()
    for m in mats:
        new_me.materials.append(m)
    obj = bpy.data.objects.new(name, new_me)
    for o in objs:
        me = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if me.users == 0:
            bpy.data.meshes.remove(me)
    return obj


def _merge_by_distance(obj, dist):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
    bm.to_mesh(obj.data)
    bm.free()


def _make_decimated(src_obj, ratio, name):
    """Create a decimated copy (automatically generated LOD)."""
    tmp = bpy.data.objects.new("__r5_tmp", src_obj.data)
    bpy.context.scene.collection.objects.link(tmp)
    mod = tmp.modifiers.new("dec", "DECIMATE")
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    me.name = name
    bpy.data.objects.remove(tmp, do_unlink=True)
    # Decimation carries stale custom normals (some flipped / zero length -> black patches):
    # replace them with clean smooth normals computed from the simplified geometry
    if len(me.vertices) and len(me.polygons):
        vn = np.empty(len(me.vertices) * 3, np.float32)
        me.vertex_normals.foreach_get("vector", vn)
        me.polygons.foreach_set("use_smooth", np.ones(len(me.polygons), dtype=bool))
        me.normals_split_custom_set_from_vertices(vn.reshape(-1, 3))
    obj = bpy.data.objects.new(name, me)
    if GROUP_KEY in src_obj:
        obj[GROUP_KEY] = src_obj[GROUP_KEY]
    return obj


# --------------------------------------------------------------------------
# Main API
# --------------------------------------------------------------------------
def ensure_model(key, files, settings, scene):
    """Import a model (all its LODs) unless it is already in the library.

    files: {lod: path}. Returns the list of LOD collections (may be empty).
    """
    existing = get_model_lods(key)
    if existing:
        return existing

    lib = get_library_collection(scene)
    model_root = bpy.data.collections.new(f"{key}")
    lib.children.link(model_root)

    corr = _axis_correction(settings.axis_correction)
    unit = settings.unit_scale
    fx = is_fx_model(key)

    lod_objects = {}   # lod -> mesh objects

    max_lod = settings.max_lods if settings.use_lod else 1
    for lod in sorted(files.keys()):
        if lod >= max_lod:
            continue
        try:
            new = _import_file(files[lod])
        except Exception as exc:  # noqa: BLE001
            print(f"[R5] Import failed {files[lod]}: {exc}")
            continue
        meshes = _strip_to_meshes(new)
        if not meshes:
            continue
        if fx:
            # Light shaft / atmosphere volume: the whole model goes to the "fx" group
            for o in meshes:
                o[GROUP_KEY] = "fx"
        elif is_vfx_model(key):
            # Energy dome / hologram shell: visible, but see-through
            for o in meshes:
                for slot in o.material_slots:
                    make_vfx(slot.material)

        # A single file holding several LODs in its object names?
        groups = {}
        for o in meshes:
            m = _LOD_OBJ_RE.search(o.name)
            groups.setdefault(int(m.group(1)) if m and len(files) == 1 else lod, []).append(o)
        for glod, objs in groups.items():
            if glod >= max_lod or glod in lod_objects:
                for o in objs:
                    bpy.data.objects.remove(o, do_unlink=True)
                continue
            # Grouped objects (triggers, tools...) stay separate so they can be hidden
            normal = [o for o in objs if GROUP_KEY not in o]
            special = [o for o in objs if GROUP_KEY in o]
            if settings.join_meshes and normal:
                normal = [_join(normal, lod_collection_name(key, glod))]
            lod_objects[glod] = normal + special

    if not lod_objects:
        bpy.data.collections.remove(model_root)
        return []

    ordered = [lod_objects[k] for k in sorted(lod_objects)]     # contiguous 0..n

    # Axis correction + scale: baked ONCE into the geometry
    xf = Matrix.Scale(unit, 4) @ corr
    for objs in ordered:
        for o in objs:
            o.data.transform(xf)
            if settings.merge_distance > 0:
                _merge_by_distance(o, settings.merge_distance)

    # Generated LODs when the model only has one
    if settings.use_lod and settings.auto_generate_lods and len(ordered) == 1 and not fx:
        ratios = [settings.auto_lod_ratio ** i for i in range(1, settings.max_lods)]
        base = ordered[0]
        tri_count = sum(len(o.data.polygons) for o in base)
        if tri_count >= settings.auto_lod_min_faces:
            for i, r in enumerate(ratios, start=1):
                ordered.append([_make_decimated(o, r, f"{lod_collection_name(key, i)}_{j}")
                                for j, o in enumerate(base)])

    radius = 0.0
    bmin, bmax = np.full(3, np.inf), np.full(3, -np.inf)
    lods = []
    for i, objs in enumerate(ordered):
        col = bpy.data.collections.new(lod_collection_name(key, i))
        model_root.children.link(col)
        for o in objs:
            col.objects.link(o)
            for c in list(o.users_collection):
                if c != col:
                    c.objects.unlink(o)
            if i == 0 and len(o.data.vertices):
                co = np.empty(len(o.data.vertices) * 3, dtype=np.float32)
                o.data.vertices.foreach_get("co", co)
                co = co.reshape(-1, 3)
                radius = max(radius, float(np.sqrt((co ** 2).sum(1)).max()))
                bmin, bmax = np.minimum(bmin, co.min(0)), np.maximum(bmax, co.max(0))
        lods.append(col)
    if np.isfinite(bmin).all():
        model_root["apx_bmin"] = [float(v) for v in bmin]
        model_root["apx_bmax"] = [float(v) for v in bmax]

    # "Bounding box" level (very far): reuses the last LOD geometry
    if settings.use_lod and settings.use_bounds_level:
        bcol = bpy.data.collections.new(f"{key}__BOUNDS")
        model_root.children.link(bcol)
        for o in ordered[-1]:
            b = bpy.data.objects.new(o.name + "_bounds", o.data)
            b.display_type = "BOUNDS"
            b.hide_render = True
            bcol.objects.link(b)

    model_root["apx_radius"] = max(radius, 0.01)
    if fx:
        model_root[GROUP_KEY] = "fx"
    for c in lods:
        c["apx_radius"] = model_root["apx_radius"]
    return lods


def model_radius(key):
    c = bpy.data.collections.get(key)
    if c is not None and "apx_radius" in c:
        return float(c["apx_radius"])
    lods = get_model_lods(key)
    return float(lods[0].get("apx_radius", 1.0)) if lods else 1.0


def model_bounds(key):
    """(min xyz, max xyz) of the model's LOD0 in Blender units (model space), or None."""
    c = bpy.data.collections.get(key)
    if c is not None and "apx_bmin" in c:
        return np.array(c["apx_bmin"][:], np.float64), np.array(c["apx_bmax"][:], np.float64)
    lods = get_model_lods(key)
    if not lods:
        return None
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for o in lods[0].all_objects:
        if o.type == "MESH" and len(o.data.vertices):
            co = np.empty(len(o.data.vertices) * 3, dtype=np.float32)
            o.data.vertices.foreach_get("co", co)
            co = co.reshape(-1, 3)
            lo, hi = np.minimum(lo, co.min(0)), np.maximum(hi, co.max(0))
    if not np.isfinite(lo).all():
        return None
    if c is not None:
        c["apx_bmin"], c["apx_bmax"] = [float(v) for v in lo], [float(v) for v in hi]
    return lo, hi


def _axis_correction(mode):
    if mode == "X90":
        return Matrix.Rotation(math.radians(90), 4, "X")
    if mode == "X-90":
        return Matrix.Rotation(math.radians(-90), 4, "X")
    if mode == "Z90":
        return Matrix.Rotation(math.radians(90), 4, "Z")
    if mode == "Z-90":
        return Matrix.Rotation(math.radians(-90), 4, "Z")
    if mode == "Z180":
        return Matrix.Rotation(math.radians(180), 4, "Z")
    return Matrix.Identity(4)
