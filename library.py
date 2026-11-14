"""Bibliothèque de modèles : indexation des fichiers exportés (Legion+ etc.),
import unique par modèle, construction des collections LOD.

Chaque modèle unique est importé UNE seule fois dans une collection cachée
"APX_Library". Chaque niveau de LOD est une sous-collection
"<modele>__LOD<n>". Les props de la map sont des instances de collection
(empties) -> la géométrie n'est jamais dupliquée en mémoire.
"""

import os
import re

import bpy
import bmesh
import numpy as np
from mathutils import Matrix

SUPPORTED_EXT = (".cast", ".glb", ".gltf", ".fbx", ".obj", ".semodel", ".smd")
LIB_COLLECTION = "APX_Library"

_LOD_FILE_RE = re.compile(r"^(?P<base>.+?)[_\-\. ]?lod[_\-]?(?P<lod>\d+)$", re.IGNORECASE)
_LOD_OBJ_RE = re.compile(r"lod[_\-]?(\d+)", re.IGNORECASE)


# --------------------------------------------------------------------------
# Indexation du dossier de modèles
# --------------------------------------------------------------------------
def build_index(root):
    """Retourne {cle_modele: {lod: chemin}}. Préférence d'extension = ordre SUPPORTED_EXT."""
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
    # Exclure du view layer : la bibliothèque n'est ni affichée ni évaluée directement
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
    """Retourne la liste ordonnée des collections LOD existantes pour un modèle."""
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
# Import d'un fichier
# --------------------------------------------------------------------------
def _import_file(path):
    """Importe un fichier et renvoie la liste des nouveaux objets."""
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
        # Lecteur intégré (pas besoin de l'addon Cast)
        from . import castfile
        return castfile.import_model_cast(path, bpy.context.scene.apx_settings)
    elif ext == ".semodel":
        if not hasattr(bpy.ops.import_scene, "semodel"):
            raise RuntimeError("Addon 'SEModel' requis pour les fichiers .semodel")
        bpy.ops.import_scene.semodel(filepath=path)
    elif ext == ".smd":
        if not hasattr(bpy.ops.import_scene, "smd"):
            raise RuntimeError("Addon 'Blender Source Tools' requis pour les fichiers .smd")
        bpy.ops.import_scene.smd(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


def _strip_to_meshes(objs):
    """Garde uniquement les meshes, transformations appliquées, sans armature."""
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
    # Applique la transformation dans la géométrie (le modèle doit être à l'identité)
    for o in meshes:
        if o.data.users > 1:
            o.data = o.data.copy()
        o.data.transform(o.matrix_world)
        o.matrix_world = Matrix.Identity(4)
    return meshes


def _join(objs, name):
    """Fusionne plusieurs meshes en un seul objet sans bpy.ops (rapide, sans contexte)."""
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
    """Crée une copie décimée (LOD généré automatiquement)."""
    tmp = bpy.data.objects.new("__apx_tmp", src_obj.data)
    bpy.context.scene.collection.objects.link(tmp)
    mod = tmp.modifiers.new("dec", "DECIMATE")
    mod.ratio = ratio
    mod.use_collapse_triangulate = True
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    me.name = name
    bpy.data.objects.remove(tmp, do_unlink=True)
    obj = bpy.data.objects.new(name, me)
    if "apx_group" in src_obj:
        obj["apx_group"] = src_obj["apx_group"]
    return obj


# --------------------------------------------------------------------------
# API principale
# --------------------------------------------------------------------------
def ensure_model(key, files, settings, scene):
    """Importe un modèle (tous ses LODs) s'il n'est pas déjà dans la bibliothèque.

    files : {lod: chemin}. Renvoie la liste des collections LOD (peut être vide).
    """
    existing = get_model_lods(key)
    if existing:
        return existing

    lib = get_library_collection(scene)
    model_root = bpy.data.collections.new(f"{key}")
    lib.children.link(model_root)

    corr = _axis_correction(settings.axis_correction)
    unit = settings.unit_scale

    lod_objects = {}   # lod -> objet mesh

    max_lod = settings.max_lods if settings.use_lod else 1
    for lod in sorted(files.keys()):
        if lod >= max_lod:
            continue
        try:
            new = _import_file(files[lod])
        except Exception as exc:  # noqa: BLE001
            print(f"[APX] Échec import {files[lod]} : {exc}")
            continue
        meshes = _strip_to_meshes(new)
        if not meshes:
            continue

        # Fichier unique contenant plusieurs LODs dans les noms d'objets ?
        groups = {}
        for o in meshes:
            m = _LOD_OBJ_RE.search(o.name)
            groups.setdefault(int(m.group(1)) if m and len(files) == 1 else lod, []).append(o)
        for glod, objs in groups.items():
            if glod >= max_lod or glod in lod_objects:
                for o in objs:
                    bpy.data.objects.remove(o, do_unlink=True)
                continue
            # Les objets triggers/outils (apx_group) restent séparés pour pouvoir être cachés
            normal = [o for o in objs if "apx_group" not in o]
            special = [o for o in objs if "apx_group" in o]
            if settings.join_meshes and normal:
                normal = [_join(normal, lod_collection_name(key, glod))]
            lod_objects[glod] = normal + special

    if not lod_objects:
        bpy.data.collections.remove(model_root)
        return []

    # Renumérotation contiguë 0..n
    ordered = [lod_objects[k] for k in sorted(lod_objects)]

    # Correction d'axe + échelle : appliquée UNE fois dans la géométrie
    xf = Matrix.Scale(unit, 4) @ corr
    for objs in ordered:
        for o in objs:
            o.data.transform(xf)
            if settings.merge_distance > 0:
                _merge_by_distance(o, settings.merge_distance)

    # LODs générés automatiquement si le modèle n'en a qu'un seul
    if settings.use_lod and settings.auto_generate_lods and len(ordered) == 1:
        ratios = [settings.auto_lod_ratio ** i for i in range(1, settings.max_lods)]
        base = ordered[0]
        tri_count = sum(len(o.data.polygons) for o in base)
        if tri_count >= settings.auto_lod_min_faces:
            for i, r in enumerate(ratios, start=1):
                ordered.append([_make_decimated(o, r, f"{lod_collection_name(key, i)}_{j}")
                                for j, o in enumerate(base)])

    radius = 0.0
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
                radius = max(radius, float(np.sqrt((co.reshape(-1, 3) ** 2).sum(1)).max()))
        lods.append(col)

    # Niveau "boîte englobante" (très loin) : réutilise la géométrie du dernier LOD
    if settings.use_lod and settings.use_bounds_level:
        bcol = bpy.data.collections.new(f"{key}__BOUNDS")
        model_root.children.link(bcol)
        for o in ordered[-1]:
            b = bpy.data.objects.new(o.name + "_bounds", o.data)
            b.display_type = "BOUNDS"
            b.hide_render = True
            bcol.objects.link(b)

    model_root["apx_radius"] = max(radius, 0.01)
    for c in lods:
        c["apx_radius"] = model_root["apx_radius"]
    return lods


def model_radius(key):
    c = bpy.data.collections.get(key)
    if c is not None and "apx_radius" in c:
        return float(c["apx_radius"])
    lods = get_model_lods(key)
    return float(lods[0].get("apx_radius", 1.0)) if lods else 1.0


def _axis_correction(mode):
    import math
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
