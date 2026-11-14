bl_info = {
    "name": "Blender R5 Map Loader",
    "author": "fendegames7",
    "version": (1, 4, 0),
    "blender": (4, 2, 0),
    "location": "Vue 3D > Sidebar (N) > R5 Map Loader  |  Fichier > Importer",
    "description": "Charge les maps R5Reloaded / Apex (.mprt, .cast, .ent, .vpk) avec instancing, "
                   "LOD dynamique désactivable, déchargement des props hors champ et outils d'optimisation",
    "category": "Import-Export",
}

if "bpy" in locals():
    import importlib
    for _m in (placement, castfile, library, gn_instancer, lod_system, optimize,  # noqa: F821
               visibility, vpk, transfer, properties, operators, ui):  # noqa: F821
        importlib.reload(_m)

import bpy  # noqa: E402

from . import (placement, castfile, library, gn_instancer, lod_system, optimize,  # noqa: E402
               visibility, vpk, transfer, properties, operators, ui)

_MODULES = (properties, operators, ui, lod_system)


def register():
    for m in _MODULES:
        m.register()


def unregister():
    for m in reversed(_MODULES):
        m.unregister()
