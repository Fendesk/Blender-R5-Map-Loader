bl_info = {
    "name": "Blender R5 Map Loader",
    "author": "Fendesk",
    "version": (0, 5, 3),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar (N) > R5 Map Loader  |  File > Import",
    "description": "Loads R5Reloaded / Apex maps (.mprt, .cast, .ent, .vpk) with instancing, switchable "
                   "dynamic LODs, off-screen unloading, regions / POIs, sky and Cycles setup",
    "category": "Import-Export",
}

if "bpy" in locals():
    import importlib
    for _m in (placement, castfile, library, regions, gn_instancer, lod_system, optimize,  # noqa: F821
               visibility, vpk, sky, cycles_setup, properties, operators, ui):  # noqa: F821
        importlib.reload(_m)

import bpy  # noqa: E402

from . import (placement, castfile, library, regions, gn_instancer, lod_system, optimize,  # noqa: E402
               visibility, vpk, sky, cycles_setup, properties, operators, ui)

# regions first: its POI type is used by the settings
_MODULES = (regions, properties, operators, optimize, visibility, sky, cycles_setup, ui, lod_system)


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
