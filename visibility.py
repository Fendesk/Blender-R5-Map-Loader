"""Show / hide triggers, tool brushes, light shafts & atmosphere volumes, game mode barriers
and dev textures.

The objects concerned carry the "apx_group" custom property, set at import time from the
material name (toolstrigger, toolsblocklight, toolsskybox, *godray*, ar_*_barrier, dev_*...)
or from the model name (light shaft / atmosphere models). Also works for objects inside the
instanced LOD collections.
"""

import bpy

from .castfile import GROUP_KEY, GROUPS


def group_objects():
    out = {g: [] for g, _l in GROUPS}
    for o in bpy.data.objects:
        g = o.get(GROUP_KEY)
        if g in out:
            out[g].append(o)
    return out


def apply(scene):
    s = scene.r5_settings
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


class R5_OT_select_group(bpy.types.Operator):
    """Select the visible objects of a group (triggers, tools...)"""
    bl_idname = "r5.select_group"
    bl_label = "Select"
    bl_options = {"REGISTER", "UNDO"}

    group: bpy.props.StringProperty()

    def execute(self, context):
        n = 0
        for o in context.view_layer.objects:
            if o.get(GROUP_KEY) == self.group and o.visible_get():
                o.select_set(True)
                n += 1
        self.report({"INFO"}, f"{n} objects selected")
        return {"FINISHED"}


class R5_OT_hide_models(bpy.types.Operator):
    """Hide or show every prop model whose name contains the text (e.g. "godray", "sphere")"""
    bl_idname = "r5.hide_models"
    bl_label = "Hide / show models by name"
    bl_options = {"REGISTER", "UNDO"}

    hide: bpy.props.BoolProperty(default=True)

    def execute(self, context):
        text = context.scene.r5_settings.model_filter.strip().lower()
        if not text:
            self.report({"WARNING"}, "Type part of a model name first")
            return {"CANCELLED"}
        n = 0
        for o in context.scene.objects:
            key = o.get("apx_gn") or o.get("apx_model")
            if key and text in key.lower():
                o.hide_viewport = self.hide
                o.hide_render = self.hide
                n += 1
        self.report({"INFO"}, f"{n} objects {'hidden' if self.hide else 'shown'}")
        return {"FINISHED"}


class R5_UL_models(bpy.types.UIList):
    """List of the prop models (one instancer object per model in Geometry Nodes mode)."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        n = len(item.data.vertices) if item.type == "MESH" else 0
        split = layout.split(factor=0.66, align=True)
        split.label(text=item.get("apx_gn", item.name))
        row = split.row(align=True)
        row.alignment = "RIGHT"
        row.label(text=str(n))
        row.prop(item, "hide_viewport", text="", emboss=False)
        row.prop(item, "hide_render", text="", emboss=False)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        flt = [0] * len(items)
        needle = self.filter_name.lower()
        for i, o in enumerate(items):
            key = o.get("apx_gn")
            if key and (not needle or needle in key.lower()):
                flt[i] = self.bitflag_filter_item
        order = bpy.types.UI_UL_list.sort_items_by_name(items, "name") if self.use_filter_sort_alpha else []
        return flt, order


CLASSES = (R5_OT_select_group, R5_OT_hide_models, R5_UL_models)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
