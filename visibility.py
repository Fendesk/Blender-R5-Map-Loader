"""Afficher / cacher les triggers, brushes outils, barrières de mode de jeu et textures dev.

Les objets concernés portent la propriété "apx_group" (posée à l'import d'après le nom
du matériau : toolstrigger, toolsblocklight, toolsskybox, ar_*_barrier, dev_*...).
Fonctionne aussi pour les objets dans les collections LOD instanciées.
"""

import bpy

from .castfile import GROUPS


def group_objects():
    out = {g: [] for g, _l in GROUPS}
    for o in bpy.data.objects:
        g = o.get("apx_group")
        if g in out:
            out[g].append(o)
    return out


def apply(scene):
    s = scene.apx_settings
    n = 0
    for g, objs in group_objects().items():
        hidden = getattr(s, f"hide_{g}")
        for o in objs:
            if o.hide_viewport != hidden:
                o.hide_viewport = hidden
                n += 1
            o.hide_render = hidden
    return n


def counts():
    return {g: len(v) for g, v in group_objects().items()}
