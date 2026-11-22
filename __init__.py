bl_info = {
    "name": "Blender R5 Map Loader",
    "author": "Fendesk",
    "version": (0, 19, 3),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar (N) > R5 Map Loader  |  File > Import",
    "description": "Loads R5Reloaded / Apex map exports (.mprt, .cast, .ent, .vpk, .nut) with instancing, "
                   "switchable dynamic LODs, off-screen unloading, regions / POIs, sky and Cycles setup",
    "category": "Import-Export",
}

# Installing a new version over a loaded one (or Reload Scripts) can leave the OLD submodules in sys.modules:
# "from . import x" would then mix old and new code (e.g. old settings without the new properties).
# Reload every submodule already imported, dependencies first.
import importlib as _importlib  # noqa: E402
import sys as _sys  # noqa: E402

for _n in ("placement", "castfile", "castwriter", "vpk", "library", "lights", "regions", "gn_instancer",
           "lod_system", "motion", "optimize", "visibility", "sky", "cycles_setup", "camera", "progress", "properties", "operators", "ui", "exwin"):
    _m = _sys.modules.get(f"{__name__}.{_n}")
    if _m is not None:
        try:
            _importlib.reload(_m)
        except Exception as _exc:  # noqa: BLE001
            print(f"[R5] reload {_n}: {_exc}")

import bpy  # noqa: E402

from . import (placement, castfile, castwriter, library, lights, regions, gn_instancer,  # noqa: E402
               lod_system, motion, optimize, visibility, vpk, sky, cycles_setup, camera, progress, properties, operators, ui)

# EX build only: the interface in a separate window (exwin.py is left out of the regular zip)
import importlib.util as _ilu  # noqa: E402

if _ilu.find_spec(f"{__name__}.exwin") is not None:
    from . import exwin  # noqa: E402
else:
    exwin = None

# regions first: its POI type is used by the settings (properties also registers the light type of lights.py).
_MODULES = (regions, properties, operators, motion, optimize, visibility, sky, cycles_setup, camera, progress, ui, lights,
            lod_system)
if exwin is not None:
    _MODULES += (exwin,)


def register():
    for m in _MODULES:
        m.register()


def _force_unregister(m):
    """Fallback when a module's unregister() failed halfway: drop whatever is still registered."""
    for c in reversed(getattr(m, "CLASSES", ())):
        if getattr(c, "is_registered", False):
            try:
                bpy.utils.unregister_class(c)
            except Exception:  # noqa: BLE001
                pass


def unregister():
    # Never raise here: Blender aborts the uninstall / disable when unregister() fails.
    for m in reversed(_MODULES):
        try:
            m.unregister()
        except Exception as exc:  # noqa: BLE001
            print(f"[R5] unregister {m.__name__}: {exc}")
            _force_unregister(m)
    if hasattr(bpy.types.Scene, "r5_settings"):
        try:
            del bpy.types.Scene.r5_settings
        except Exception:  # noqa: BLE001
            pass
