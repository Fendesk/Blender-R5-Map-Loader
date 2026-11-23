"""Player banners on the flex screens of the maps (survival_modular_flexscreens_04: the tall double-sided screens
hanging in E-District, Olympus, World's Edge and Storm Point, which show the champions' banners in game).

The exports only have the plastic texture of the screen: the banners are chosen in the panel (one image or
several, given out at random, one per screen). The screen model gets an object-level material (its mesh and
its own material are untouched; *Remove* gives them back). The image is placed from the screen's own shape
(width x height of the model), not from its UVs, filling the screen without stretching, readable from both sides.
"""

import os
import re

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty,
                       StringProperty)
from bpy_extras.io_utils import ImportHelper

from .castfile import _sock

# the screens (not their frames / stands): survival_modular_flexscreens_04__LOD0, ...__LOD1 (generated LODs)
SCREEN_RX = re.compile(r"^(.*flex_?screens?_\d+[a-z]?)__LOD\d+$", re.I)
NOT_SCREEN_RX = re.compile(r"frame|stand|base|bracket|mount", re.I)
TAG = "r5_banner"
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".tga", ".bmp", ".webp", ".tif", ".tiff", ".exr", ".dds")


def screen_collections():
    """{model key: [LOD collections]}"""
    out = {}
    for c in bpy.data.collections:
        m = SCREEN_RX.match(c.name)
        if m and not NOT_SCREEN_RX.search(m.group(1)):
            out.setdefault(m.group(1), []).append(c)
    return out


def screen_count(scene):
    keys = set(screen_collections())
    n = 0
    for o in scene.objects:
        k = o.get("apx_gn")
        if k in keys and not o.get("apx_sky") and o.type == "MESH":
            n += len(o.data.vertices)
        elif o.instance_type == "COLLECTION" and o.instance_collection is not None:
            m = SCREEN_RX.match(o.instance_collection.name)
            if m and m.group(1) in keys:
                n += 1
    return n


def _images(st):
    out = []
    for it in st.images:
        path = bpy.path.abspath(it.filepath)
        if not it.filepath or not os.path.isfile(path):
            continue
        try:
            img = bpy.data.images.load(path, check_existing=True)
        except RuntimeError:
            continue
        out.append(img)
    return out


def _screen_box(cols):
    """(width axis index, x0, x1, z0, z1) of the screen model, from the first mesh of its LOD0."""
    for c in sorted(cols, key=lambda c: c.name):
        for o in c.all_objects:
            if o.type == "MESH" and len(o.data.vertices):
                xs = [v.co.x for v in o.data.vertices]
                ys = [v.co.y for v in o.data.vertices]
                zs = [v.co.z for v in o.data.vertices]
                ax = 0 if (max(xs) - min(xs)) >= (max(ys) - min(ys)) else 1
                a = xs if ax == 0 else ys
                return ax, min(a), max(a), min(zs), max(zs)
    return None


def _material(key, box, images, st):
    name = f"R5_Banner_{key}"[:63]
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links
    ax, a0, a1, z0, z1 = box
    w, h = max(a1 - a0, 1e-4), max(z1 - z0, 1e-4)

    def op(kind, a, b=None, c=None, loc=(0, 0)):
        n = N.new("ShaderNodeMath"); n.operation = kind; n.location = loc
        for i, v in enumerate((a, b, c)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                L.new(v, n.inputs[i])
        return n.outputs[0]

    out = N.new("ShaderNodeOutputMaterial"); out.location = (1300, 0)
    bs = N.new("ShaderNodeBsdfPrincipled"); bs.location = (1000, 0)
    bs.inputs["Roughness"].default_value = 0.35
    L.new(bs.outputs[0], out.inputs["Surface"])
    tc = N.new("ShaderNodeTexCoord"); tc.location = (-1200, 0)
    sep = N.new("ShaderNodeSeparateXYZ"); sep.location = (-1000, 100)
    L.new(tc.outputs["Object"], sep.inputs[0])
    nsep = N.new("ShaderNodeSeparateXYZ"); nsep.location = (-1000, -150)
    L.new(tc.outputs["Normal"], nsep.inputs[0])
    axis = "XY"[ax]
    other = "YX"[ax]
    u = op("MULTIPLY_ADD", sep.outputs[axis], 1.0 / w, -a0 / w, (-800, 150))
    v = op("MULTIPLY_ADD", sep.outputs["Z"], 1.0 / h, -z0 / h, (-800, 0))
    # the face looking towards +other is seen mirrored: its u is flipped (readable from both sides)
    back = op("GREATER_THAN", nsep.outputs[other], 0.0, loc=(-800, -150))
    if ax == 1:
        back = op("SUBTRACT", 1.0, back, loc=(-650, -250))
    flip = op("MULTIPLY_ADD", u, -2.0, 1.0, (-650, 250))              # 1 - 2u
    u = op("MULTIPLY_ADD", flip, back, u, (-500, 200))                # u + back * (1 - 2u)
    sa = w / h
    cols = []
    for i, img in enumerate(images):
        iw, ih = img.size if img.size[0] and img.size[1] else (1, 1)
        ia = iw / ih
        ku, kv = 1.0, 1.0
        if st.fit == "FILL":
            if ia > sa:
                ku = sa / ia          # wider than the screen: the sides are cut
            else:
                kv = ia / sa          # taller: top and bottom are cut
        y = 300 - 320 * i
        uu = op("MULTIPLY_ADD", u, ku, 0.5 - 0.5 * ku, (-300, y))
        vv = op("MULTIPLY_ADD", v, kv, 0.5 - 0.5 * kv, (-300, y - 120))
        cv = N.new("ShaderNodeCombineXYZ"); cv.location = (-120, y)
        L.new(uu, cv.inputs["X"]); L.new(vv, cv.inputs["Y"])
        t = N.new("ShaderNodeTexImage"); t.image = img; t.location = (60, y); t.extension = "EXTEND"
        t.interpolation = "Cubic"
        L.new(cv.outputs[0], t.inputs["Vector"])
        cols.append(t.outputs["Color"])
    col = cols[0]
    if len(cols) > 1:
        info = N.new("ShaderNodeObjectInfo"); info.location = (60, 500)
        idx = op("MULTIPLY", info.outputs["Random"], float(len(cols)), loc=(250, 550))
        idx = op("FLOOR", idx, loc=(400, 550))
        for i, c in enumerate(cols[1:], start=1):
            pick = op("COMPARE", idx, float(i), 0.5, (550, 550 - 60 * i))
            m = N.new("ShaderNodeMix"); m.data_type = "RGBA"; m.location = (700, 400 - 120 * i)
            L.new(pick, _sock(m.inputs, "Factor_Float"))
            L.new(col, _sock(m.inputs, "A_Color"))
            L.new(c, _sock(m.inputs, "B_Color"))
            col = _sock(m.outputs, "Result_Color")
    L.new(col, bs.inputs["Base Color"])
    key_e = "Emission Color" if "Emission Color" in bs.inputs else "Emission"
    L.new(col, bs.inputs[key_e])
    bs.inputs["Emission Strength"].default_value = st.strength
    mat["r5_banner"] = True
    return mat


def clear(scene=None):
    n = 0
    for o in bpy.data.objects:
        if not o.get(TAG):
            continue
        for slot in o.material_slots:
            if slot.link == "OBJECT":
                slot.material = None
                slot.link = "DATA"
        del o[TAG]
        n += 1
    for m in list(bpy.data.materials):
        if m.get("r5_banner") and m.users == 0:
            bpy.data.materials.remove(m)
    return n


def apply(scene):
    """Banners on every flex screen of the file. Returns (screen models, screens)."""
    st = scene.r5_banners
    clear(scene)
    images = _images(st)
    if not st.enabled or not images:
        return 0, 0
    if st.mode == "SAME":
        idx = min(max(st.active, 0), len(images) - 1)
        images = images[idx:idx + 1]
    models = 0
    for key, cols in screen_collections().items():
        box = _screen_box(cols)
        if box is None:
            continue
        mat = _material(key, box, images, st)
        for c in cols:
            for o in c.all_objects:
                if o.type != "MESH":
                    continue
                for slot in o.material_slots:
                    slot.link = "OBJECT"
                    slot.material = mat
                o[TAG] = True
        models += 1
    return models, screen_count(scene)


def after_import(scene, report=print):
    st = scene.r5_banners
    if st.enabled and len(st.images):
        models, n = apply(scene)
        if n:
            report(f"Player banners: {n} flex screens")


def _changed(self, context):
    try:
        apply(context.scene)
    except Exception as exc:  # noqa: BLE001
        print(f"[R5] banners: {exc}")


class R5_BannerImage(bpy.types.PropertyGroup):
    filepath: StringProperty(name="Image", subtype="FILE_PATH", update=_changed)


class R5_BannerSettings(bpy.types.PropertyGroup):
    enabled: BoolProperty(name="Banners on the flex screens", default=True, update=_changed,
                          description="Show the banner images on the flex screens of the map")
    images: CollectionProperty(type=R5_BannerImage)
    active: IntProperty(name="Active banner", default=0, update=_changed)
    mode: EnumProperty(name="Banners", items=(
        ("RANDOM", "Random per screen", "Each screen shows one of the images, at random"),
        ("SAME", "Selected one", "Every screen shows the image selected in the list")),
        default="RANDOM", update=_changed)
    fit: EnumProperty(name="Fit", items=(
        ("FILL", "Fill", "Fill the screen, cutting what goes past its sides (no stretching)"),
        ("STRETCH", "Stretch", "The whole image, stretched to the screen")),
        default="FILL", update=_changed)
    strength: FloatProperty(name="Glow", default=1.5, min=0.0, soft_max=10.0, update=_changed,
                            description="Brightness of the screens (emission strength)")


class R5_OT_banner_add(bpy.types.Operator, ImportHelper):
    """Add banner images (PNG, JPG...): one is given to each flex screen, at random"""
    bl_idname = "r5.banner_add"
    bl_label = "Add banners"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(default="*" + ";*".join(_IMAGE_EXT), options={"HIDDEN"})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        st = context.scene.r5_banners
        names = [f.name for f in self.files if f.name] or [os.path.basename(self.filepath)]
        folder = self.directory or os.path.dirname(self.filepath)
        have = {bpy.path.abspath(i.filepath) for i in st.images}
        added = 0
        for n in names:
            p = os.path.join(folder, n)
            if not os.path.isfile(p) or os.path.splitext(p)[1].lower() not in _IMAGE_EXT or p in have:
                continue
            it = st.images.add()
            it["filepath"] = p                  # (no update per image: applied once below)
            added += 1
        st["active"] = max(0, len(st.images) - 1)
        models, n = apply(context.scene)
        if not n:
            self.report({"WARNING"}, f"{added} banner(s) added, but no flex screen in this file")
        else:
            self.report({"INFO"}, f"{added} banner(s) added: {n} flex screens")
        return {"FINISHED"}


class R5_OT_banner_remove(bpy.types.Operator):
    """Remove the selected banner image from the list"""
    bl_idname = "r5.banner_remove"
    bl_label = "Remove banner"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        st = context.scene.r5_banners
        if 0 <= st.active < len(st.images):
            st.images.remove(st.active)
            st["active"] = max(0, min(st.active, len(st.images) - 1))
        apply(context.scene)
        return {"FINISHED"}


class R5_OT_banner_apply(bpy.types.Operator):
    """Give the screens their banners again (new random draw after an import)"""
    bl_idname = "r5.banner_apply"
    bl_label = "Apply banners"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        models, n = apply(context.scene)
        self.report({"INFO"}, f"Banners on {n} flex screens" if n else "No flex screen in this file")
        return {"FINISHED"}


class R5_UL_banners(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        ok = os.path.isfile(bpy.path.abspath(item.filepath))
        layout.label(text=os.path.basename(item.filepath) or "(empty)", icon="IMAGE_DATA" if ok else "ERROR")


class R5_PT_banners(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "R5 Map Loader"
    bl_label = "Player banners"
    bl_idname = "R5_PT_banners"
    bl_order = 7                 # below Effects & water, above Animation (ui.py)
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        from .ui import SIDEBAR
        from . import progress
        return SIDEBAR[0] and not progress.active() and hasattr(context.scene, "r5_banners")

    def draw_header(self, context):
        self.layout.prop(context.scene.r5_banners, "enabled", text="")

    def draw(self, context):
        st = context.scene.r5_banners
        col = self.layout.column()
        n = screen_count(context.scene)
        col.label(text=f"{n} flex screens in the map" if n else "No flex screen loaded", icon="IMAGE_PLANE")
        row = col.row()
        row.template_list("R5_UL_banners", "", st, "images", st, "active", rows=3)
        side = row.column(align=True)
        side.operator("r5.banner_add", text="", icon="ADD")
        side.operator("r5.banner_remove", text="", icon="REMOVE")
        side.separator()
        side.operator("r5.banner_apply", text="", icon="FILE_REFRESH")
        sub = col.column()
        sub.enabled = st.enabled
        sub.prop(st, "mode")
        row = sub.row(align=True)
        row.prop(st, "fit", expand=True)
        sub.prop(st, "strength")


CLASSES = (R5_BannerImage, R5_BannerSettings, R5_OT_banner_add, R5_OT_banner_remove, R5_OT_banner_apply,
           R5_UL_banners, R5_PT_banners)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.r5_banners = PointerProperty(type=R5_BannerSettings)


def unregister():
    if hasattr(bpy.types.Scene, "r5_banners"):
        del bpy.types.Scene.r5_banners
    for c in reversed(CLASSES):
        if getattr(c, "is_registered", False):
            bpy.utils.unregister_class(c)
