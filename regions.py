"""Partial loading: show only one part of a loaded map (a point of interest, a grid sector,
or a disc around the 3D cursor).

The active region is a single empty, "APX_Region":
  location = center, scale.x = radius in meters, scale.z = 1 (enabled) / 0 (disabled).
  - Props (Geometry Nodes instancers) read it and drop every point outside the disc.
  - Map geometry and zipline cables get an "APX_RegionClip" modifier that deletes what is outside.
Moving to another region only moves this empty: nothing is re-imported, and Cycles / EEVEE
only receive the visible part.
"""

import math
import string

import bpy
import numpy as np
from bpy.props import FloatProperty, FloatVectorProperty, IntProperty, StringProperty
from mathutils import Vector

REGION = "APX_Region"
CLIP_GROUP = "APX_RegionClip"
CLIP_MOD = "APX_RegionClip"


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
class R5_POI(bpy.types.PropertyGroup):
    center: FloatVectorProperty(name="Center", size=3, subtype="TRANSLATION")
    radius: FloatProperty(name="Radius", default=150.0, min=1.0, subtype="DISTANCE")
    kind: StringProperty(name="Kind")


def get_region(scene):
    r = bpy.data.objects.get(REGION)
    if r is None:
        r = bpy.data.objects.new(REGION, None)
        r.empty_display_type = "CIRCLE"
        r.empty_display_size = 1.0
        r.hide_render = True
        r.hide_select = True
        r.scale = (100.0, 100.0, 0.0)
    if r.name not in scene.collection.objects:
        scene.collection.objects.link(r)
        _show(r, False)
    return r


def _show(obj, state):
    # Eye icon only: the object must stay evaluated so the node groups can read it
    try:
        obj.hide_set(not state)
    except RuntimeError:
        pass


def region_active(scene):
    r = bpy.data.objects.get(REGION)
    return r is not None and r.scale.z > 0.5


REGION_HIDDEN = "r5_region_hidden"


def _bbox_xy(o):
    """World-space XY bounds of an object's original geometry (cached)."""
    bb = o.get("r5_bbox")
    if bb:
        return tuple(bb)
    if o.type != "MESH" or not len(o.data.vertices):
        return None
    co = np.empty(len(o.data.vertices) * 3, np.float32)
    o.data.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mw = np.array(o.matrix_world, dtype=np.float32)
    co = co @ mw[:3, :3].T + mw[:3, 3]
    bb = (float(co[:, 0].min()), float(co[:, 1].min()), float(co[:, 0].max()), float(co[:, 1].max()))
    o["r5_bbox"] = bb
    return bb


def _hide_outside(scene, center, radius):
    """Fully disable the geometry objects / prop models that do not touch the region, so that
    Cycles and EEVEE do not even load their materials and textures."""
    n = 0
    for o in scene.objects:
        if not (o.get("apx_world_tile") or "apx_gn" in o):
            continue
        bb = _bbox_xy(o)
        if bb is None:
            continue
        dx = max(bb[0] - center.x, 0.0, center.x - bb[2])
        dy = max(bb[1] - center.y, 0.0, center.y - bb[3])
        outside = dx * dx + dy * dy > radius * radius
        if outside and not o.hide_viewport:
            o.hide_viewport = True
            o.hide_render = True
            o[REGION_HIDDEN] = True
            n += 1
        elif not outside and o.get(REGION_HIDDEN):
            o.hide_viewport = False
            o.hide_render = False
            del o[REGION_HIDDEN]
    return n


def _restore_hidden(scene):
    for o in scene.objects:
        if o.get(REGION_HIDDEN):
            o.hide_viewport = False
            o.hide_render = False
            del o[REGION_HIDDEN]


def set_region(scene, center, radius):
    from . import visibility
    r = get_region(scene)
    r.location = center
    r.scale = (radius, radius, 1.0)
    _show(r, True)
    ensure_clip_modifiers(scene)
    _hide_outside(scene, Vector(center), radius)
    visibility.apply(scene)
    _after_change(scene)


def clear_region(scene):
    from . import visibility
    r = get_region(scene)
    r.scale = (r.scale.x, r.scale.y, 0.0)
    _show(r, False)
    _restore_hidden(scene)
    visibility.apply(scene)
    _after_change(scene)


def _after_change(scene):
    from . import lod_system
    lod_system.invalidate()
    for o in scene.objects:
        if "apx_gn" in o or o.modifiers.get(CLIP_MOD):
            o.update_tag()


# --------------------------------------------------------------------------
# Clip modifier for the map geometry / cables
# --------------------------------------------------------------------------
def get_clip_group():
    ng = bpy.data.node_groups.get(CLIP_GROUP)
    if ng is not None:
        return ng
    from .gn_instancer import _region_fail_nodes
    ng = bpy.data.node_groups.new(CLIP_GROUP, "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Region", in_out="INPUT", socket_type="NodeSocketObject")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    N, L = ng.nodes, ng.links
    gi = N.new("NodeGroupInput"); gi.location = (-1400, 0)
    go = N.new("NodeGroupOutput"); go.location = (600, 0)
    pos = N.new("GeometryNodeInputPosition"); pos.location = (-1400, -300)
    fail = _region_fail_nodes(N, L, gi.outputs["Region"], pos.outputs[0], (-600, -300))
    dele = N.new("GeometryNodeDeleteGeometry"); dele.location = (300, 0)
    dele.domain = "POINT"
    L.new(gi.outputs["Geometry"], dele.inputs["Geometry"])
    L.new(fail, dele.inputs["Selection"])
    L.new(dele.outputs[0], go.inputs[0])
    return ng


def _clip_targets(scene):
    for o in scene.objects:
        if o.get("apx_world_tile") or (o.type == "CURVE" and o.name.endswith("_ziplines")):
            yield o


def ensure_clip_modifiers(scene):
    ng = get_clip_group()
    region = get_region(scene)
    ident = next(it.identifier for it in ng.interface.items_tree
                 if getattr(it, "item_type", "SOCKET") == "SOCKET" and it.in_out == "INPUT"
                 and it.name == "Region")
    for o in _clip_targets(scene):
        mod = o.modifiers.get(CLIP_MOD)
        if mod is None:
            mod = o.modifiers.new(CLIP_MOD, "NODES")
            mod.node_group = ng
        if mod.get(ident) != region:
            mod[ident] = region


# --------------------------------------------------------------------------
# Points of interest
# --------------------------------------------------------------------------
def add_zones(scene, zones, unit, offset):
    """Create POIs from the zones read in the entity files."""
    s = scene.r5_settings
    existing = {p.name for p in s.pois}
    named = [z for z in zones if z.kind == "named"]
    named_pos = np.array([z.origin for z in named], dtype=np.float64).reshape(-1, 3)

    def to_bl(o):
        return Vector((o[0] * unit - offset.x, o[1] * unit - offset.y, o[2] * unit - offset.z))

    added = 0
    for z in named:
        if z.name in existing:
            continue
        p = s.pois.add()
        p.name, p.kind = z.name, "zone"
        p.center = to_bl(z.origin)
        p.radius = z.radius * unit if z.radius > 0 else s.poi_default_radius
        existing.add(p.name)
        added += 1
    # Large loot zones (radius >= 1000 units) = areas of interest
    loot = sorted((z for z in zones if z.kind == "loot" and z.radius >= 1000), key=lambda z: -z.radius)
    for i, z in enumerate(loot, start=1):
        near = ""
        if len(named_pos):
            d = np.linalg.norm(named_pos[:, :2] - np.array(z.origin[:2]), axis=1)
            j = int(d.argmin())
            if d[j] < max(z.radius * 2, 8000):
                near = f" near {named[j].name}"
        name = f"loot {z.name.replace('zone_', '')} {i}{near}"
        if name in existing:
            continue
        p = s.pois.add()
        p.name, p.kind = name, "loot"
        p.center = to_bl(z.origin)
        p.radius = z.radius * unit * 1.2
        existing.add(name)
        added += 1
    return added


def map_bounds(scene):
    """XY bounds (meters) of the map geometry, or of all props when there is no geometry."""
    lo, hi = None, None
    objs = [o for o in scene.objects if o.get("apx_world_tile") and "apx_group" not in o]
    if not objs:
        objs = [o for o in scene.objects if "apx_gn" in o]
    for o in objs:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            lo = w.copy() if lo is None else Vector(map(min, lo, w))
            hi = w.copy() if hi is None else Vector(map(max, hi, w))
    return lo, hi


def add_grid(scene, n):
    s = scene.r5_settings
    lo, hi = map_bounds(scene)
    if lo is None:
        return 0
    for i in reversed(range(len(s.pois))):
        if s.pois[i].kind == "grid":
            s.pois.remove(i)
    sx, sy = (hi.x - lo.x) / n, (hi.y - lo.y) / n
    letters = string.ascii_uppercase
    for j in range(n):            # rows from north (top) to south
        for i in range(n):
            p = s.pois.add()
            p.name = f"Grid {letters[i % 26]}{j + 1}"
            p.kind = "grid"
            p.center = (lo.x + (i + 0.5) * sx, hi.y - (j + 0.5) * sy, 0.0)
            p.radius = math.hypot(sx, sy) / 2
    return n * n


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------
class R5_OT_region_poi(bpy.types.Operator):
    """Show only the selected point of interest (everything else is hidden, nothing is re-imported)"""
    bl_idname = "r5.region_poi"
    bl_label = "Show only this POI"

    def execute(self, context):
        s = context.scene.r5_settings
        if not (0 <= s.poi_index < len(s.pois)):
            self.report({"WARNING"}, "Select a POI in the list")
            return {"CANCELLED"}
        p = s.pois[s.poi_index]
        set_region(context.scene, Vector(p.center), p.radius * s.region_scale)
        self.report({"INFO"}, f"Region: {p.name} ({p.radius * s.region_scale:.0f} m)")
        return {"FINISHED"}


class R5_OT_region_cursor(bpy.types.Operator):
    """Show only a disc around the 3D cursor"""
    bl_idname = "r5.region_cursor"
    bl_label = "Region around 3D cursor"

    def execute(self, context):
        s = context.scene.r5_settings
        set_region(context.scene, context.scene.cursor.location.copy(), s.poi_default_radius * s.region_scale)
        return {"FINISHED"}


class R5_OT_region_clear(bpy.types.Operator):
    """Show the whole map again"""
    bl_idname = "r5.region_clear"
    bl_label = "Whole map"

    def execute(self, context):
        clear_region(context.scene)
        return {"FINISHED"}


class R5_OT_poi_goto(bpy.types.Operator):
    """Move the 3D view above the selected POI"""
    bl_idname = "r5.poi_goto"
    bl_label = "Go to POI"

    def execute(self, context):
        s = context.scene.r5_settings
        if not (0 <= s.poi_index < len(s.pois)):
            return {"CANCELLED"}
        p = s.pois[s.poi_index]
        r3d = context.space_data.region_3d if context.space_data and context.space_data.type == "VIEW_3D" else None
        if r3d is None:
            for area in context.window.screen.areas:
                if area.type == "VIEW_3D":
                    r3d = area.spaces.active.region_3d
                    break
        if r3d is None:
            return {"CANCELLED"}
        r3d.view_location = Vector(p.center)
        r3d.view_distance = max(p.radius * 2.2, 30.0)
        return {"FINISHED"}


class R5_OT_poi_add(bpy.types.Operator):
    """Add a POI at the 3D cursor"""
    bl_idname = "r5.poi_add"
    bl_label = "Add POI at cursor"

    def execute(self, context):
        s = context.scene.r5_settings
        p = s.pois.add()
        p.name = f"POI {len(s.pois)}"
        p.kind = "user"
        p.center = context.scene.cursor.location
        p.radius = s.poi_default_radius
        s.poi_index = len(s.pois) - 1
        return {"FINISHED"}


class R5_OT_poi_remove(bpy.types.Operator):
    """Remove the selected POI from the list"""
    bl_idname = "r5.poi_remove"
    bl_label = "Remove POI"

    def execute(self, context):
        s = context.scene.r5_settings
        if 0 <= s.poi_index < len(s.pois):
            s.pois.remove(s.poi_index)
            s.poi_index = min(s.poi_index, len(s.pois) - 1)
        return {"FINISHED"}


class R5_OT_poi_grid(bpy.types.Operator):
    """Add grid sectors (A1, B1...) covering the map to the POI list"""
    bl_idname = "r5.poi_grid"
    bl_label = "Add grid sectors"

    def execute(self, context):
        n = add_grid(context.scene, context.scene.r5_settings.grid_size)
        if not n:
            self.report({"WARNING"}, "Load a map first")
            return {"CANCELLED"}
        self.report({"INFO"}, f"{n} grid sectors added")
        return {"FINISHED"}


class R5_UL_pois(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        icon = {"zone": "BOOKMARKS", "loot": "PACKAGE", "grid": "MESH_GRID", "user": "PINNED"}.get(item.kind, "DOT")
        row = layout.row(align=True)
        row.prop(item, "name", text="", emboss=False, icon=icon)
        row.label(text=f"{item.radius:.0f} m")


CLASSES = (R5_POI, R5_OT_region_poi, R5_OT_region_cursor, R5_OT_region_clear, R5_OT_poi_goto,
           R5_OT_poi_add, R5_OT_poi_remove, R5_OT_poi_grid, R5_UL_pois)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
