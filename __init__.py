bl_info = {
    "name": "Apex Legends Map Importer",
    "author": "fendegames7",
    "version": (1, 1, 0),
    "blender": (3, 6, 0),
    "location": "Vue 3D > Sidebar (N) > Apex Map  |  Fichier > Importer",
    "description": "Importe des maps Apex (placements .ent/.json/.csv + modèles exportés) "
                   "avec instancing, LOD dynamique désactivable et outils d'optimisation",
    "category": "Import-Export",
}

if "bpy" in locals():
    import importlib
    for _m in (placement, castfile, library, gn_instancer, lod_system, optimize,  # noqa: F821
               properties, operators, ui):  # noqa: F821
        importlib.reload(_m)

import bpy  # noqa: E402

from . import (placement, castfile, library, gn_instancer, lod_system, optimize,  # noqa: E402
               properties, operators, ui)

_MODULES = (properties, operators, ui, lod_system)


def register():
    for m in _MODULES:
        m.register()


def unregister():
    for m in reversed(_MODULES):
        m.unregister()
