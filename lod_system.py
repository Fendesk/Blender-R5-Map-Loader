"""Système de LOD dynamique (désactivable).

Principe : chaque prop est un empty qui instancie une collection. Changer de
LOD = changer `instance_collection` -> aucune copie de géométrie, coût quasi nul.
Les distances sont calculées en bloc avec numpy, et seuls les props dont le
niveau change sont touchés.

Niveaux : 0..n-1 = LODs, n = boîte englobante (si présente), -1 = masqué (cull).
"""

import bpy
import numpy as np
from mathutils import Vector

from . import library

LEVEL_CULLED = -1


class _Cache:
    def __init__(self):
        self.valid = False
        self.objs = []          # références aux empties
        self.pos = None         # (N,3)
        self.size = None        # (N,) rayon * échelle
        self.model_idx = None   # (N,)
        self.models = []        # [(lods[], bounds_coll|None)]
        self.n_lods = None      # (M,)
        self.has_bounds = None  # (M,)
        self.current = None     # (N,) niveau actuel
        self.last_eye = None
        self.forced_applied = None


_cache = _Cache()


def invalidate():
    _cache.valid = False
    _cache.last_eye = None
    _cache.forced_applied = None


def rebuild(scene):
    c = _cache
    objs, pos, size, midx = [], [], [], []
    model_lookup, models = {}, []
    for o in scene.objects:
        key = o.get("apx_model")
        if key is None or o.type != "EMPTY":
            continue
        if key not in model_lookup:
            lods = library.get_model_lods(key)
            if not lods:
                continue
            model_lookup[key] = len(models)
            models.append((lods, library.bounds_collection(key), library.model_radius(key)))
        mi = model_lookup[key]
        objs.append(o)
        pos.append(o.matrix_world.translation[:])
        size.append(models[mi][2] * max(o.scale))
        midx.append(mi)
    c.objs = objs
    c.pos = np.array(pos, dtype=np.float32).reshape(-1, 3)
    c.size = np.array(size, dtype=np.float32)
    c.model_idx = np.array(midx, dtype=np.int32)
    c.models = models
    c.n_lods = np.array([len(m[0]) for m in models], dtype=np.int32)
    c.has_bounds = np.array([m[1] is not None for m in models], dtype=bool)
    c.current = np.full(len(objs), -2, dtype=np.int32)
    c.valid = True
    c.last_eye = None
    c.forced_applied = None


# --------------------------------------------------------------------------
def _compute_levels(eye, s):
    c = _cache
    if len(c.objs) == 0:
        return np.zeros(0, dtype=np.int32)
    raw = np.linalg.norm(c.pos - np.asarray(eye, dtype=np.float32), axis=1)
    d = raw
    # Normalisation par la taille : un gros rocher garde son LOD0 plus loin
    if s.lod_size_scaling:
        factor = np.clip(c.size / max(s.lod_reference_size, 1e-3), 0.25, 20.0)
        d = d / factor
    d = d / max(s.lod_bias, 1e-3)

    thresholds = np.array(sorted(s.lod_distances), dtype=np.float32)
    level = np.searchsorted(thresholds, d, side="right").astype(np.int32)
    nl = c.n_lods[c.model_idx]
    level = np.minimum(level, nl - 1)

    if s.use_bounds_level:
        hb = c.has_bounds[c.model_idx]
        level = np.where(hb & (d >= s.bounds_distance), nl, level)
    if s.use_culling:
        level = np.where(d >= s.cull_distance, LEVEL_CULLED, level)
        if s.cull_small_props:
            # Petits objets masqués plus tôt
            small = c.size < s.small_prop_size
            level = np.where(small & (raw >= s.small_prop_distance), LEVEL_CULLED, level)
    return level


def _apply(levels, for_render=False):
    c = _cache
    changed = np.nonzero(levels != c.current)[0]
    try:
        for i in changed:
            o = c.objs[i]
            lv = int(levels[i])
            lods, bounds, _r = c.models[c.model_idx[i]]
            if lv == LEVEL_CULLED:
                if not o.hide_viewport:
                    o.hide_viewport = True
                o.hide_render = True
            else:
                col = bounds if lv >= len(lods) else lods[lv]
                if o.instance_collection != col:
                    o.instance_collection = col
                if o.hide_viewport:
                    o.hide_viewport = False
                o.hide_render = False
            c.current[i] = lv
    except ReferenceError:
        invalidate()
        return 0
    return len(changed)


def apply_forced(scene, level):
    """LOD désactivé : tous les props au même niveau, sans culling."""
    if not _cache.valid:
        rebuild(scene)
    c = _cache
    if len(c.objs) == 0:
        return 0
    nl = c.n_lods[c.model_idx]
    levels = np.minimum(np.full(len(c.objs), level, dtype=np.int32), nl - 1)
    n = _apply(levels)
    _cache.forced_applied = level
    _cache.last_eye = None
    return n


def update_from_eye(scene, eye, force=False):
    s = scene.apx_settings
    if not _cache.valid:
        rebuild(scene)
    c = _cache
    if not force and c.last_eye is not None:
        if (Vector(eye) - c.last_eye).length < s.update_move_threshold:
            return 0
    c.last_eye = Vector(eye)
    c.forced_applied = None
    return _apply(_compute_levels(eye, s))


# --------------------------------------------------------------------------
# Point de vue
# --------------------------------------------------------------------------
def viewport_eye(context=None):
    wm = bpy.context.window_manager
    best, best_area = None, -1
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type != "VIEW_3D":
                continue
            r3d = area.spaces.active.region_3d
            if r3d is None:
                continue
            a = area.width * area.height
            if a > best_area:
                best_area = a
                best = r3d.view_matrix.inverted().translation.copy()
    return best


def current_eye(scene):
    s = scene.apx_settings
    if s.lod_source == "CAMERA" and scene.camera is not None:
        return scene.camera.matrix_world.translation.copy()
    eye = viewport_eye()
    if eye is None and scene.camera is not None:
        eye = scene.camera.matrix_world.translation.copy()
    return eye


# --------------------------------------------------------------------------
# Timer & handlers
# --------------------------------------------------------------------------
_rendering = False


def _gn_present():
    from . import gn_instancer
    ng = bpy.data.node_groups.get(gn_instancer.GROUP)
    return ng is not None and ng.users > 0


def _move_probe_throttled(scene, eye, s):
    from . import gn_instancer
    p = gn_instancer.get_probe(scene)
    if (p.location - Vector(eye)).length >= s.update_move_threshold:
        p.location = eye


def apply_all(scene):
    """Applique immédiatement l'état LOD (empties + Geometry Nodes)."""
    from . import gn_instancer
    s = scene.apx_settings
    invalidate()
    n = 0
    if not s.use_lod:
        n += apply_forced(scene, s.forced_lod)
    else:
        eye = current_eye(scene)
        if eye is not None:
            n += update_from_eye(scene, eye, force=True)
            if _gn_present():
                gn_instancer.move_probe(scene, Vector(eye))
    if _gn_present():
        n += gn_instancer.apply_settings(scene)
    return n


def _timer():
    scene = bpy.context.scene
    if scene is None or not hasattr(scene, "apx_settings"):
        return 1.0
    s = scene.apx_settings
    if _rendering:
        return 0.5
    try:
        if not s.use_lod:
            if _cache.forced_applied != s.forced_lod:
                apply_forced(scene, s.forced_lod)
            return 1.0
        if not s.lod_auto_update:
            return 1.0
        eye = current_eye(scene)
        if eye is not None:
            update_from_eye(scene, eye)
            if _gn_present():
                _move_probe_throttled(scene, eye, s)
    except Exception as exc:  # noqa: BLE001
        print(f"[APX] LOD timer: {exc}")
        invalidate()
    return max(s.update_interval, 0.05)


@bpy.app.handlers.persistent
def _on_load(_dummy):
    invalidate()


@bpy.app.handlers.persistent
def _on_undo(_dummy):
    invalidate()


@bpy.app.handlers.persistent
def _render_pre(scene, _dg=None):
    global _rendering
    _rendering = True
    s = scene.apx_settings
    from . import gn_instancer
    gn = _gn_present()
    try:
        if not s.use_lod or s.render_mode == "LOD0":
            level = s.forced_lod if not s.use_lod else 0
            apply_forced(scene, level)
            if gn:
                gn_instancer.apply_settings(scene, force_level=level)
        elif s.render_mode == "CAMERA" and scene.camera is not None:
            cam = scene.camera.matrix_world.translation.copy()
            update_from_eye(scene, cam, force=True)
            if gn:
                gn_instancer.move_probe(scene, cam)
    except Exception as exc:  # noqa: BLE001
        print(f"[APX] render_pre: {exc}")


@bpy.app.handlers.persistent
def _render_post(scene, _dg=None):
    global _rendering
    _rendering = False
    _cache.last_eye = None
    _cache.forced_applied = None
    try:
        if _gn_present():
            from . import gn_instancer
            gn_instancer.apply_settings(scene)
    except Exception as exc:  # noqa: BLE001
        print(f"[APX] render_post: {exc}")


_HANDLERS = (
    ("load_post", _on_load),
    ("undo_post", _on_undo),
    ("redo_post", _on_undo),
    ("render_pre", _render_pre),
    ("render_post", _render_post),
    ("render_cancel", _render_post),
)


def register():
    for name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn not in lst:
            lst.append(fn)
    if not bpy.app.timers.is_registered(_timer):
        bpy.app.timers.register(_timer, first_interval=1.0, persistent=True)


def unregister():
    for name, fn in _HANDLERS:
        lst = getattr(bpy.app.handlers, name)
        if fn in lst:
            lst.remove(fn)
    if bpy.app.timers.is_registered(_timer):
        bpy.app.timers.unregister(_timer)
    invalidate()
