"""Système de LOD + culling dynamique (désactivable).

Deux modes d'instanciation :
  - Geometry Nodes : on déplace seulement la sonde APX_LOD_Probe (position, rotation, champ
    de vision) ; tout le reste est calculé par les nœuds (voir gn_instancer).
  - Empties : chaque prop est un empty qui instancie une collection ; niveaux calculés en
    bloc avec numpy, seuls les props qui changent de niveau sont touchés.

Culling : hors du champ de vision (frustum), trop petit à l'écran, au-delà d'une distance.
La mise à jour attend que la vue soit stable (anti-rebond) pour ne pas saccader.
"""

import math
import time

import bpy
import numpy as np
from mathutils import Quaternion, Vector

from . import library

LEVEL_CULLED = -1


# --------------------------------------------------------------------------
# État de la vue
# --------------------------------------------------------------------------
class View:
    __slots__ = ("eye", "quat", "tanx", "tany", "mode")

    def __init__(self, eye, quat, tanx, tany, mode):
        self.eye, self.quat, self.tanx, self.tany, self.mode = eye, quat, tanx, tany, mode

    def differs(self, other, move, angle):
        if other is None:
            return True
        if (self.eye - other.eye).length >= move:
            return True
        if self.quat.rotation_difference(other.quat).angle >= angle:
            return True
        if abs(self.tanx - other.tanx) > 0.02 * other.tanx or abs(self.tany - other.tany) > 0.02 * other.tany:
            return True
        return self.mode != other.mode


def _largest_view3d():
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
                best_area, best = a, r3d
    return best


def viewport_view():
    r3d = _largest_view3d()
    if r3d is None:
        return None
    inv = r3d.view_matrix.inverted()
    wmx = r3d.window_matrix
    if r3d.is_perspective and abs(wmx[0][0]) > 1e-6 and abs(wmx[1][1]) > 1e-6:
        return View(inv.translation.copy(), inv.to_quaternion(), 1.0 / abs(wmx[0][0]), 1.0 / abs(wmx[1][1]), 1.0)
    return View(inv.translation.copy(), inv.to_quaternion(), 1.0, 1.0, 0.0)   # ortho : pas de culling


def camera_view(scene, frustum=True):
    cam = scene.camera
    if cam is None:
        return None
    mw = cam.matrix_world
    tanx = tany = 1.0
    mode = 1.0 if frustum else 0.5
    if cam.type == "CAMERA" and cam.data.type == "PERSP":
        r = scene.render
        try:
            pm = cam.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),
                                        x=r.resolution_x, y=r.resolution_y,
                                        scale_x=r.pixel_aspect_x, scale_y=r.pixel_aspect_y)
            tanx, tany = 1.0 / abs(pm[0][0]), 1.0 / abs(pm[1][1])
        except Exception:  # noqa: BLE001
            tanx = tany = math.tan(cam.data.angle / 2)
    elif cam.type == "CAMERA":
        mode = 0.0
    return View(mw.translation.copy(), mw.to_quaternion(), tanx, tany, mode)


def current_view(scene):
    s = scene.apx_settings
    if s.lod_source == "CAMERA" and scene.camera is not None:
        return camera_view(scene)
    v = viewport_view()
    if v is None and scene.camera is not None:
        v = camera_view(scene)
    return v


def current_eye(scene):
    v = current_view(scene)
    return v.eye if v else None


# --------------------------------------------------------------------------
# Mode Empties (numpy)
# --------------------------------------------------------------------------
class _Cache:
    def __init__(self):
        self.valid = False
        self.objs = []
        self.pos = None
        self.size = None
        self.model_idx = None
        self.models = []
        self.n_lods = None
        self.has_bounds = None
        self.current = None
        self.last_view = None
        self.forced_applied = None


_cache = _Cache()


def invalidate():
    _cache.valid = False
    _cache.last_view = None
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
    c.last_view = None
    c.forced_applied = None


def _compute_levels(view, s):
    c = _cache
    if len(c.objs) == 0:
        return np.zeros(0, dtype=np.int32)
    rel = c.pos - np.asarray(view.eye, dtype=np.float32)
    raw = np.linalg.norm(rel, axis=1)
    d = raw
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

    culled = np.zeros(len(level), dtype=bool)
    if s.use_culling:
        culled |= raw >= s.cull_distance
    if s.cull_small_props and view.mode > 0.25 and s.min_screen_pct > 0:
        culled |= c.size / np.maximum(raw * view.tany, 1e-4) < s.min_screen_pct / 100.0
    if s.use_frustum and view.mode > 0.75:
        rmat = np.array(view.quat.to_matrix(), dtype=np.float32)    # colonnes = axes de la vue
        vl = rel @ rmat                                              # coordonnées dans l'espace vue
        fwd = -vl[:, 2]
        m = s.frustum_margin
        sz = c.size * 1.5
        inside = ((np.abs(vl[:, 0]) <= fwd * view.tanx * m + sz) &
                  (np.abs(vl[:, 1]) <= fwd * view.tany * m + sz) & (fwd >= -sz))
        culled |= ~inside
    return np.where(culled, LEVEL_CULLED, level)


def _apply(levels):
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
        _cache.forced_applied = level
        return 0
    nl = c.n_lods[c.model_idx]
    levels = np.minimum(np.full(len(c.objs), level, dtype=np.int32), nl - 1)
    n = _apply(levels)
    _cache.forced_applied = level
    _cache.last_view = None
    return n


def update_empties(scene, view):
    if not _cache.valid:
        rebuild(scene)
    _cache.forced_applied = None
    _cache.last_view = view
    if len(_cache.objs) == 0:
        return 0
    return _apply(_compute_levels(view, scene.apx_settings))


# Compatibilité
def update_from_eye(scene, eye, force=False):
    v = current_view(scene) or View(Vector(eye), Quaternion(), 1.0, 1.0, 0.0)
    v.eye = Vector(eye)
    return update_empties(scene, v)


# --------------------------------------------------------------------------
# Application globale
# --------------------------------------------------------------------------
def _gn_present():
    from . import gn_instancer
    ng = bpy.data.node_groups.get(gn_instancer.GROUP)
    return ng is not None and ng.users > 0


def apply_view(scene, view):
    from . import gn_instancer
    n = update_empties(scene, view)
    if _gn_present():
        gn_instancer.set_probe(scene, view.eye, view.quat, view.tanx, view.tany, view.mode)
    return n


def apply_all(scene):
    """Applique immédiatement l'état LOD (empties + Geometry Nodes)."""
    from . import gn_instancer
    s = scene.apx_settings
    invalidate()
    n = 0
    if _gn_present():
        n += gn_instancer.apply_settings(scene)
    if not s.use_lod:
        n += apply_forced(scene, s.forced_lod)
    else:
        v = current_view(scene)
        if v is not None:
            n += apply_view(scene, v)
            _state.applied = v
    return n


# --------------------------------------------------------------------------
# Timer (anti-rebond) & handlers
# --------------------------------------------------------------------------
class _State:
    applied = None      # dernière vue appliquée
    last_seen = None    # vue au tick précédent
    changed_at = 0.0    # instant du dernier mouvement
    applied_at = 0.0


_state = _State()
_rendering = False
TICK = 0.1


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
            return 0.5
        if not s.lod_auto_update:
            return 0.5
        v = current_view(scene)
        if v is None:
            return 0.5
        now = time.monotonic()
        moving = v.differs(_state.last_seen, 0.05, math.radians(0.5))
        _state.last_seen = v
        if moving:
            _state.changed_at = now
        need = v.differs(_state.applied, s.update_move_threshold, math.radians(s.update_angle_threshold))
        if need:
            settled = (now - _state.changed_at) >= s.update_settle
            overdue = s.update_interval > 0 and (now - _state.applied_at) >= s.update_interval
            if settled or overdue:
                apply_view(scene, v)
                _state.applied = v
                _state.applied_at = now
    except Exception as exc:  # noqa: BLE001
        print(f"[APX] LOD timer: {exc}")
        invalidate()
    return TICK


@bpy.app.handlers.persistent
def _on_load(_dummy):
    invalidate()
    _state.applied = None


@bpy.app.handlers.persistent
def _on_undo(_dummy):
    invalidate()
    _state.applied = None


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
            v = camera_view(scene, frustum=s.render_frustum)
            update_empties(scene, v)
            if gn:
                gn_instancer.set_probe(scene, v.eye, v.quat, v.tanx, v.tany, v.mode)
    except Exception as exc:  # noqa: BLE001
        print(f"[APX] render_pre: {exc}")


@bpy.app.handlers.persistent
def _render_post(scene, _dg=None):
    global _rendering
    _rendering = False
    _cache.last_view = None
    _cache.forced_applied = None
    _state.applied = None
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
