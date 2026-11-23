"""Sky / skybox for the map.

What the game data gives us: in R5Reloaded VPKs, the entities that define the sky and the sun
(light_environment, sky_camera...) are in <map>_env.ent, which is LZHAM-compressed, and the BSP
lumps are compressed too. When an uncompressed env .ent is provided, its light_environment is
used for the sun direction and color. Sky sources:
  - PROCEDURAL: Blender's physical sky (Nishita) + a sun lamp,
  - IMAGE: an equirectangular panorama / HDRI,
  - CUBEMAP: 6 face images (e.g. a sky cubemap exported with RSX: name_000..name_005,
    or px/nx/py/ny/pz/nz, right/left/up/down/front/back) converted to a panorama,
  - DOME: the in-game sky dome texture (square image, round sky, zenith in the centre, horizon on
    the circle), mapped live with shader nodes (EEVEE and Cycles).

On top of an image sky: an optional physical atmosphere layer (air / dust / ozone densities of
Blender's Nishita sky, strongest near the horizon) and an optional volumetric fog (world volume).
The sun lamp has its own colour. Value changes are applied live, without rebuilding the world.
"""

import json
import math
import re
import os

import bpy
import numpy as np
from mathutils import Euler, Vector

WORLD = "R5_Sky"
SUN = "R5_Sun"
PANO = "R5_Sky_Panorama"
DEFAULT_SUN_COLOR = (1.0, 0.96, 0.9)

# Mean radiance of the upper hemisphere of Blender's Nishita sky (air = dust = ozone = 1, altitude 500 m),
# measured per sun elevation (degrees). Used to bring the atmosphere layer to the brightness of the image.
_NISHITA_BASE = ((-10, 0.3), (5, 1.51), (15, 2.55), (35, 2.94), (60, 3.14), (85, 3.64), (90, 3.7))


def _nishita_base(elev_rad):
    e = math.degrees(elev_rad)
    pts = _NISHITA_BASE
    if e <= pts[0][0]:
        return pts[0][1]
    for (e0, v0), (e1, v1) in zip(pts, pts[1:]):
        if e <= e1:
            return v0 + (v1 - v0) * (e - e0) / (e1 - e0)
    return pts[-1][1]


def _to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


# --------------------------------------------------------------------------
# Sun
# --------------------------------------------------------------------------
def sun_from_entities(sun_ent):
    """light_environment -> (elevation, azimuth, color) in radians, or None."""
    if not sun_ent:
        return None
    ang = [float(v) for v in sun_ent.get("angles", "0 0 0").split()[:3]] + [0, 0, 0]
    pitch = float(sun_ent.get("pitch", ang[0]))
    yaw = ang[1]
    col = DEFAULT_SUN_COLOR
    lv = sun_ent.get("_light", "").split()
    if len(lv) >= 3:
        try:
            rgb = [float(v) / 255.0 for v in lv[:3]]
            mx = max(rgb) or 1.0
            col = tuple(c / mx for c in rgb)
        except ValueError:
            pass
    # Source: pitch is negative when the light points down
    return math.radians(-pitch if pitch < 0 else pitch), math.radians(yaw + 180.0), col


def _map_sun(s):
    if s.map_sun_json:
        try:
            return sun_from_entities(json.loads(s.map_sun_json))
        except (ValueError, TypeError):
            return None
    return None


def sun_angles(s):
    """(elevation, azimuth) in radians: the map's light_environment when available and wanted."""
    if s.sky_use_map_sun:
        got = _map_sun(s)
        if got:
            return got[0], got[1]
    return math.radians(s.sky_sun_elevation), math.radians(s.sky_sun_rotation)


def default_sun_color(s):
    got = _map_sun(s)
    return tuple(got[2]) if got else DEFAULT_SUN_COLOR


def _sun(scene, s):
    sun = bpy.data.objects.get(SUN)
    if not s.sky_add_sun:
        if sun is not None and sun.name in scene.collection.objects:
            scene.collection.objects.unlink(sun)
        return None
    if sun is None:
        sun = bpy.data.objects.new(SUN, bpy.data.lights.new(SUN, "SUN"))
    if sun.name not in scene.collection.objects:
        scene.collection.objects.link(sun)
    elev, azim = sun_angles(s)
    sun.data.energy = s.sky_sun_strength
    sun.data.color = tuple(s.sky_sun_color)
    sun.data.angle = math.radians(0.6)
    # A sun lamp shines along its local -Z: tilt it down from the zenith
    sun.rotation_euler = Euler((math.radians(90.0) - elev, 0.0, azim + math.radians(90.0)), "XYZ")
    return sun


# --------------------------------------------------------------------------
# World building blocks
# --------------------------------------------------------------------------
def _world(scene):
    w = bpy.data.worlds.get(WORLD) or bpy.data.worlds.new(WORLD)
    w.use_nodes = True
    w["r5_sky_mode"] = ""
    scene.world = w
    nt = w.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputWorld"); out.location = (1300, 0)
    bg = nt.nodes.new("ShaderNodeBackground"); bg.location = (1100, 0); bg.name = "R5_Bg"
    nt.links.new(bg.outputs[0], out.inputs["Surface"])
    return w, nt, bg


def _set_nishita(node, s, sun_disc):
    elev, azim = sun_angles(s)
    # Blender 5.0+ renamed NISHITA to SINGLE_SCATTERING (same model, keeps the brightness calibration
    # of _NISHITA_BASE) and dust_density to aerosol_density.
    types = {e.identifier for e in node.bl_rna.properties["sky_type"].enum_items}
    sky_type = "NISHITA" if "NISHITA" in types else "SINGLE_SCATTERING"
    values = (("sky_type", sky_type), ("sun_elevation", elev), ("sun_rotation", azim), ("altitude", 500.0),
              ("air_density", s.sky_air_density), ("dust_density", s.sky_dust_density),
              ("aerosol_density", s.sky_dust_density), ("ozone_density", s.sky_ozone_density),
              ("sun_disc", sun_disc))
    for attr, val in values:
        if hasattr(node, attr):
            try:
                setattr(node, attr, val)
            except (AttributeError, TypeError, ValueError):
                pass


def _mix_rgba(nt, factor, a, b, loc, name=""):
    mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.location = loc
    if name:
        mix.name = name
    for sk in mix.inputs:
        if sk.identifier == "Factor_Float":
            if isinstance(factor, float):
                sk.default_value = factor
            else:
                nt.links.new(factor, sk)
        elif sk.identifier == "A_Color":
            nt.links.new(a, sk) if not isinstance(a, (tuple, list)) else setattr(sk, "default_value", a)
        elif sk.identifier == "B_Color":
            nt.links.new(b, sk) if not isinstance(b, (tuple, list)) else setattr(sk, "default_value", b)
    return next(sk for sk in mix.outputs if sk.identifier == "Result_Color")


def _atmosphere(nt, color, s, mean_lum):
    """Physical sky (air / dust / ozone) blended over the image, strongest towards the horizon."""
    N, L = nt.nodes, nt.links
    sky = N.new("ShaderNodeTexSky"); sky.name = "R5_Atmo_Sky"; sky.location = (500, -400)
    _set_nishita(sky, s, sun_disc=False)
    sc = N.new("ShaderNodeVectorMath"); sc.operation = "SCALE"; sc.name = "R5_Atmo_Scale"; sc.location = (700, -400)
    sc.inputs["Scale"].default_value = mean_lum / _nishita_base(sun_angles(s)[0])
    L.new(sky.outputs[0], sc.inputs[0])
    tc = N.new("ShaderNodeTexCoord"); tc.location = (100, -650)
    sep = N.new("ShaderNodeSeparateXYZ"); sep.location = (250, -650)
    L.new(tc.outputs["Generated"], sep.inputs[0])

    def m(op, a, b, loc, name=""):
        n = N.new("ShaderNodeMath"); n.operation = op; n.location = loc
        if name:
            n.name = name
        for i, v in enumerate((a, b)):
            if isinstance(v, float):
                n.inputs[i].default_value = v
            else:
                L.new(v, n.inputs[i])
        return n
    z = m("MAXIMUM", sep.outputs[2], 0.0, (400, -650)).outputs[0]
    one_minus = m("SUBTRACT", 1.0, z, (550, -650)).outputs[0]
    p = m("POWER", one_minus, 3.0, (700, -650)).outputs[0]
    wgt = N.new("ShaderNodeMath"); wgt.operation = "MULTIPLY_ADD"; wgt.location = (850, -650)
    L.new(p, wgt.inputs[0]); wgt.inputs[1].default_value = 0.65; wgt.inputs[2].default_value = 0.35
    f = m("MULTIPLY", wgt.outputs[0], s.sky_atmosphere_strength, (1000, -650), "R5_Atmo_Strength")
    f.use_clamp = True
    return _mix_rgba(nt, f.outputs[0], color, sc.outputs["Vector"], (900, -150), "R5_Atmo_Mix")


FOG = "R5_Fog"
FOG_VERSION = 4


def _fog_bounds(scene):
    """Box around the map geometry / props (meters), with some margin and height for the haze."""
    lo = hi = None
    for o in scene.objects:
        if not (o.get("apx_world_tile") or "apx_gn" in o) or o.type != "MESH" or o.get("apx_sky"):
            continue
        for c in o.bound_box:
            wv = o.matrix_world @ Vector(c)
            lo = wv.copy() if lo is None else Vector(map(min, lo, wv))
            hi = wv.copy() if hi is None else Vector(map(max, hi, wv))
    if lo is None:
        return (-2000.0, -2000.0, -50.0), (2000.0, 2000.0, 600.0)
    return (lo.x - 400, lo.y - 400, lo.z - 30), (hi.x + 400, hi.y + 400, hi.z + 500)


def _remove_fog():
    ob = bpy.data.objects.get(FOG)
    if ob is not None:
        me = ob.data
        bpy.data.objects.remove(ob)
        if me is not None and me.users == 0:
            bpy.data.meshes.remove(me)
    mat = bpy.data.materials.get(FOG)
    if mat is not None and mat.users == 0:
        bpy.data.materials.remove(mat)


def _fog_material(scene):
    """Height fog: dense near the ground, thinning out with height (halved every *Thickness* metres), broken
    into slowly drifting patches by a noise, faded out at the sides and top of its box (no visible wall)."""
    mat = bpy.data.materials.get(FOG)
    if mat is not None and mat.get("r5_fog_ver") == FOG_VERSION and mat.node_tree:
        return mat
    if mat is None:
        mat = bpy.data.materials.new(FOG)
    mat.use_nodes = True
    nt = mat.node_tree
    if nt.animation_data:
        nt.animation_data_clear()
    nt.nodes.clear()
    N, L = nt.nodes, nt.links

    def val(name, x, y, v=0.0):
        n = N.new("ShaderNodeValue")
        n.name = n.label = name
        n.location = (x, y)
        n.outputs[0].default_value = v
        return n.outputs[0]

    def op(kind, a, b, x, y, clamp=False):
        n = N.new("ShaderNodeMath")
        n.operation = kind
        n.location = (x, y)
        n.use_clamp = clamp
        for i, v in enumerate((a, b)):
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            elif v is not None:
                L.new(v, n.inputs[i])
        return n.outputs[0]

    out = N.new("ShaderNodeOutputMaterial"); out.location = (1500, 0)
    vol = N.new("ShaderNodeVolumePrincipled"); vol.name = "R5_Fog"; vol.location = (1200, 0)
    L.new(vol.outputs[0], out.inputs["Volume"])
    geo = N.new("ShaderNodeNewGeometry"); geo.location = (-1600, 0)
    sep = N.new("ShaderNodeSeparateXYZ"); sep.location = (-1400, 200)
    L.new(geo.outputs["Position"], sep.inputs[0])
    # height: 0.5 ^ (max(z - base, 0) / thickness)
    base, thick = val("R5_Fog_Base", -1400, 450), val("R5_Fog_Thickness", -1400, 350, 60.0)
    h = op("SUBTRACT", sep.outputs["Z"], base, -1200, 450)
    h = op("MAXIMUM", h, 0.0, -1050, 450)
    h = op("DIVIDE", h, thick, -900, 450)
    hf = op("POWER", 0.5, h, -750, 450)
    # above the layer the fog does not vanish: it keeps "Above the layer" x the base density (as in game)
    floor = val("R5_Fog_Floor", -750, 600, 0.15)
    hf = op("MULTIPLY", hf, op("SUBTRACT", 1.0, floor, -600, 600), -600, 450)
    hf = op("ADD", hf, floor, -450, 450)
    # patches: 4D noise of the world position, carried by the wind
    t = val("R5_Fog_Time", -1600, -400)
    wind = val("R5_Fog_Wind", -1600, -500, 1.5)
    size = val("R5_Fog_Size", -1600, -600, 120.0)
    drift = op("MULTIPLY", t, wind, -1400, -450)
    cx = N.new("ShaderNodeCombineXYZ"); cx.location = (-1200, -450)
    L.new(drift, cx.inputs["X"])
    L.new(op("MULTIPLY", drift, 0.35, -1300, -550), cx.inputs["Y"])
    sub = N.new("ShaderNodeVectorMath"); sub.operation = "SUBTRACT"; sub.location = (-1000, -300)
    L.new(geo.outputs["Position"], sub.inputs[0])
    L.new(cx.outputs[0], sub.inputs[1])
    div = N.new("ShaderNodeVectorMath"); div.operation = "DIVIDE"; div.location = (-800, -300)
    L.new(sub.outputs[0], div.inputs[0])
    cs = N.new("ShaderNodeCombineXYZ"); cs.location = (-1000, -550)
    for k in ("X", "Y"):
        L.new(size, cs.inputs[k])
    L.new(op("MULTIPLY", size, 0.45, -1150, -650), cs.inputs["Z"])      # flatter patches, like real mist
    L.new(cs.outputs[0], div.inputs[1])
    nz = N.new("ShaderNodeTexNoise"); nz.location = (-600, -300); nz.name = "R5_Fog_Noise"
    nz.noise_dimensions = "4D"
    nz.inputs["Scale"].default_value = 1.0
    nz.inputs["Detail"].default_value = 4.0
    nz.inputs["Roughness"].default_value = 0.55
    L.new(div.outputs[0], nz.inputs["Vector"])
    L.new(op("MULTIPLY", t, 0.015, -800, -500), nz.inputs["W"])
    mr = N.new("ShaderNodeMapRange"); mr.location = (-400, -300)
    mr.inputs["From Min"].default_value = 0.32
    mr.inputs["From Max"].default_value = 0.68
    L.new(nz.outputs["Fac"], mr.inputs["Value"])
    patch = val("R5_Fog_Patch", -400, -550, 0.5)
    # noise factor: 1 - patch + 2 * patch * n  (mean 1: the overall density stays the same)
    nf = op("MULTIPLY", mr.outputs["Result"], 2.0, -200, -300)
    nf = op("SUBTRACT", nf, 1.0, -50, -300)
    nf = op("MULTIPLY", nf, patch, 100, -300)
    nf = op("ADD", nf, 1.0, 250, -300)
    nf = op("MAXIMUM", nf, 0.0, 400, -300)
    # soft borders of the box: no visible wall where the fog ends
    edge = None
    for i, (k, lo_n, hi_n) in enumerate((("X", "R5_Fog_X0", "R5_Fog_X1"), ("Y", "R5_Fog_Y0", "R5_Fog_Y1"))):
        lo_v, hi_v = val(lo_n, -1400, -800 - 200 * i), val(hi_n, -1400, -880 - 200 * i)
        a = op("SUBTRACT", sep.outputs[k], lo_v, -1200, -800 - 200 * i)
        b = op("SUBTRACT", hi_v, sep.outputs[k], -1200, -880 - 200 * i)
        m = op("MINIMUM", a, b, -1050, -840 - 200 * i)
        edge = m if edge is None else op("MINIMUM", edge, m, -900, -900)
    edge = op("DIVIDE", edge, 350.0, -750, -900, clamp=True)
    top = op("SUBTRACT", val("R5_Fog_Z1", -1400, -1300), sep.outputs["Z"], -1200, -1300)
    top = op("DIVIDE", top, 250.0, -1050, -1300, clamp=True)
    edge = op("MULTIPLY", edge, top, -600, -1000)
    dens = op("MULTIPLY", val("R5_Fog_Density", 400, 300, 0.0003), hf, 600, 300)
    dens = op("MULTIPLY", dens, nf, 750, 150)
    dens = op("MULTIPLY", dens, edge, 900, 0)
    L.new(dens, vol.inputs["Density"])
    # self-lit: the fog tends towards its colour x glow by itself (emission = density x colour x glow), as the
    # game's fog colour does, whatever the lighting (the night fog of E-District)
    L.new(op("MULTIPLY", dens, val("R5_Fog_Glow", 900, -200), 1050, -200), vol.inputs["Emission Strength"])
    mat["r5_fog_ver"] = FOG_VERSION
    if hasattr(mat, "cycles"):
        try:
            mat.cycles.homogeneous_volume = False
        except AttributeError:
            pass
    return mat


def _fog_object(scene, s):
    """Volumetric height fog in a volume box around the map. A world volume would be infinite: EEVEE then
    absorbs the whole sky and the sun (black image)."""
    ob = bpy.data.objects.get(FOG)
    if not s.sky_fog:
        if ob is not None:
            _remove_fog()
        return None
    if not scene.get("r5_fog_init"):
        scene["r5_fog_init"] = True
        fog_from_map(scene)              # first time on: the map's own fog, when the export has it
    (x0, y0, z0), (x1, y1, z1) = _fog_bounds(scene)
    box = (x0, y0, z0, x1, y1, z1)
    if ob is None:
        ob = bpy.data.objects.new(FOG, bpy.data.meshes.new(FOG))
        ob.display_type = "BOUNDS"
        ob.hide_select = True
        ob["r5_fog"] = True
    me = ob.data
    if tuple(round(v, 3) for v in ob.get("r5_box", ())) != tuple(round(v, 3) for v in box):
        verts = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
        faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
        me.clear_geometry()
        me.from_pydata(verts, [], faces)
        me.update()
        ob["r5_box"] = box
    mat = _fog_material(scene)
    if not me.materials:
        me.materials.append(mat)
    elif me.materials[0] != mat:
        me.materials[0] = mat
    if ob.name not in scene.collection.objects:
        scene.collection.objects.link(ob)
    _set_fog_values(mat, s, box)
    tn = mat.node_tree.nodes.get("R5_Fog_Time")
    ad = mat.node_tree.animation_data
    if tn is not None and not (ad and ad.drivers):
        from .motion import _drive
        _drive(tn.outputs[0], scene, "frame / fps", [("fps", "render.fps")])
    _fog_range(scene, s)
    return ob


def _fog(scene, nt, s):
    _fog_object(scene, s)


def _set_fog_values(mat, s, box):
    nodes = mat.node_tree.nodes

    def put(name, v):
        n = nodes.get(name)
        if n is not None and abs(n.outputs[0].default_value - v) > 1e-9:
            n.outputs[0].default_value = v
    x0, y0, z0, x1, y1, z1 = box
    for k, v in (("R5_Fog_X0", x0), ("R5_Fog_X1", x1), ("R5_Fog_Y0", y0), ("R5_Fog_Y1", y1), ("R5_Fog_Z1", z1),
                 ("R5_Fog_Density", s.sky_fog_density), ("R5_Fog_Base", s.sky_fog_base),
                 ("R5_Fog_Thickness", max(s.sky_fog_thickness, 0.1)), ("R5_Fog_Patch", s.sky_fog_patchiness),
                 ("R5_Fog_Size", max(s.sky_fog_patch_size, 0.1)), ("R5_Fog_Wind", s.sky_fog_wind),
                 ("R5_Fog_Glow", s.sky_fog_glow),
                 ("R5_Fog_Floor", s.sky_fog_above)):
        put(k, v)
    vol = nodes.get("R5_Fog")
    if vol is not None:
        vol.inputs["Color"].default_value = tuple(s.sky_fog_color) + (1.0,)
        vol.inputs["Anisotropy"].default_value = s.sky_fog_anisotropy
        if "Emission Color" in vol.inputs:
            vol.inputs["Emission Color"].default_value = tuple(s.sky_fog_color) + (1.0,)


def _fog_range(scene, s):
    ee = getattr(scene, "eevee", None)
    if ee is None:
        return
    # volume shadows: the sun makes light shafts through the fog, and the fog is dark in the shade
    for attr, v in (("volumetric_end", s.sky_fog_distance), ("use_volumetric_shadows", True)):
        if hasattr(ee, attr):
            try:
                setattr(ee, attr, v)
            except (AttributeError, TypeError):
                pass


# ---- the map's own fog (env_fog_controller) ----
_FOG_SKIP_RE = re.compile(r"podium|drop|skydive|interior|inside|phaserunner|bunker|cave|tunnel")


def pick_map_fog(controllers, volumes):
    """The fog the player sees over the map: the env_fog_controller of the fog_volume with the lowest priority
    (the big one), never the drop ship / skydive / victory podium / interior fogs."""
    by_name = {c.get("targetname", ""): c for c in controllers}
    cands = []
    for v in volumes:
        tgt = v.get("target", "")
        if tgt in by_name and not _FOG_SKIP_RE.search(tgt.lower()):
            try:
                pr = float(v.get("priority", "10"))
            except ValueError:
                pr = 10.0
            cands.append((pr, tgt))
    if cands:
        return by_name[min(cands)[1]]
    ok = [c for c in controllers if not _FOG_SKIP_RE.search(c.get("targetname", "").lower())]
    main = [c for c in ok if re.search(r"main|level", c.get("targetname", "").lower())]
    return (main or ok or [None])[0]


def _floats(text, n, default):
    try:
        v = [float(x) for x in str(text).split()[:n]]
    except ValueError:
        return list(default)
    return v + list(default[len(v):]) if len(v) < n else v


def map_fog_record(kv, unit, offset_z):
    """Game fog parameters converted to meters (Blender Z), for fog_from_map."""
    def f(k, d=0.0):
        try:
            return float(kv.get(k, d))
        except (TypeError, ValueError):
            return d
    rgb = [max(0.0, min(255.0, c)) / 255.0 for c in _floats(kv.get("fogcolor", ""), 3, (200.0, 200.0, 200.0))]
    lin = [c ** 2.2 for c in rgb]
    m = max(max(lin), 1e-4)
    return {"name": kv.get("targetname", ""), "max": max(0.0, min(1.0, f("fogdensity", 0.3))),
            "z_bottom": f("fogzbottom") * unit + offset_z, "z_top": f("fogztop") * unit + offset_z,
            "half_bottom": max(f("foghalfdistbottom", 2000.0), 1.0) * unit,
            "half_top": max(f("foghalfdisttop", 2000.0), 1.0) * unit,
            "start": max(f("fogdistoffset"), 0.0) * unit,
            "color": [0.92 * c / m for c in lin],
            # brightness of the game's fog colour (colour x colour strength), relative to "color"
            "glow": m * max(0.0, f("fogcolorstrength", 1.0)) / 0.92}


def fog_from_map(scene):
    """Density, colour, height and thickness from the map's fog. Returns False without map fog.
    The game fog is a screen effect that stops at its *fogdensity* (40% on E-District); a real volume keeps
    thickening with distance, so the density is matched to the game at the distance across the map, at the
    height where the fog is densest."""
    import json
    raw = scene.get("r5_map_fog")
    if not raw:
        return False
    try:
        fog = json.loads(raw)
    except ValueError:
        return False
    s = scene.r5_settings
    (x0, y0, z0), (x1, y1, z1) = _fog_bounds(scene)
    ground = z0 + 30.0
    span = max(500.0, min(3000.0, 0.5 * math.hypot(x1 - x0, y1 - y0)))
    zb, zt, hb, ht = fog["z_bottom"], fog["z_top"], fog["half_bottom"], fog["half_top"]
    if zb > zt:
        zb, zt, hb, ht = zt, zb, ht, hb          # (Storm Point: "bottom" above "top")

    def half(z):                                 # half distance at a height, interpolated as in game
        if zt - zb < 1e-3:
            return hb
        k = max(0.0, min(1.0, (z - zb) / (zt - zb)))
        return hb + (ht - hb) * k
    base = max(ground, zb) if hb < ht else ground
    hd = half(base)
    # matched between where the game fog is nearly full (start + 3 half distances) and the distance across
    # the map: thinner than the game up close, thicker far away
    near = max(100.0, min(1500.0, fog["start"] + 3.0 * hd))
    ref = math.sqrt(near * max(span, near))
    frac = fog["max"] * (1.0 - 2.0 ** (-max(ref - fog["start"], 0.0) / hd))
    frac = max(0.0, min(0.95, frac))
    s["sky_fog_density"] = -math.log(1.0 - frac) / ref if frac > 0 else 0.0
    s["sky_fog_glow"] = fog.get("glow", 0.0)
    if hb < ht * 0.95 and zt - zb > 1.0:
        # the game thins its fog between its two heights, then keeps the top density
        thick, above = 0.35 * (zt - zb), hb / ht
    else:
        thick, above = 2000.0, 1.0              # as dense higher up: an even layer
    s["sky_fog_thickness"] = max(10.0, min(2000.0, thick))
    s["sky_fog_above"] = max(0.0, min(1.0, above))
    s["sky_fog_base"] = base
    s["sky_fog_color"] = fog["color"]
    return True


def _finish(scene, w, nt, bg, color, mean_lum, mode):
    s = scene.r5_settings
    if s.sky_atmosphere and mode != "PROCEDURAL":
        color = _atmosphere(nt, color, s, mean_lum)
    nt.links.new(color, bg.inputs["Color"])
    bg.inputs["Strength"].default_value = s.sky_strength
    _fog(scene, nt, s)
    w["r5_sky_mode"] = mode
    w["r5_mean"] = float(mean_lum)
    _sun(scene, s)
    return w


def _mean_luminance(img, circle=None):
    """Mean linear luminance of an image (inside the sky disc for a dome). Cached on the image."""
    if "r5_mean" in img:
        return float(img["r5_mean"])
    w, h = img.size
    if not w or not h:
        return 0.5
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    step = max(1, max(w, h) // 512)
    a = px.reshape(h, w, 4)[::step, ::step, :3]
    if circle is not None:
        cx, cy, r = circle
        hh, ww = a.shape[:2]
        yy, xx = np.mgrid[0:hh, 0:ww]
        a = a[((xx + 0.5) / ww - cx) ** 2 + ((yy + 0.5) / hh - cy) ** 2 < r * r]
    a = a.reshape(-1, 3)
    if not img.is_float:
        a = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    mean = float((a @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean()) if len(a) else 0.5
    img["r5_mean"] = mean
    return mean


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------
def setup_procedural(scene):
    s = scene.r5_settings
    w, nt, bg = _world(scene)
    sky = nt.nodes.new("ShaderNodeTexSky"); sky.location = (800, 0); sky.name = "R5_Sky_Nishita"
    _set_nishita(sky, s, sun_disc=True)
    return _finish(scene, w, nt, bg, sky.outputs[0], 1.0, "PROCEDURAL")


def _setup_pano(scene, img, mode, rotation_deg=0.0):
    s = scene.r5_settings
    w, nt, bg = _world(scene)
    env = nt.nodes.new("ShaderNodeTexEnvironment"); env.location = (600, 0)
    env.image = img
    tc = nt.nodes.new("ShaderNodeTexCoord"); tc.location = (200, 0)
    mp = nt.nodes.new("ShaderNodeMapping"); mp.location = (400, 0); mp.name = "R5_Rot_Map"
    mp.inputs["Rotation"].default_value[2] = math.radians(rotation_deg)
    nt.links.new(tc.outputs["Generated"], mp.inputs["Vector"])
    nt.links.new(mp.outputs[0], env.inputs["Vector"])
    return _finish(scene, w, nt, bg, env.outputs[0], _mean_luminance(img), mode)


def setup_image(scene, path):
    img = bpy.data.images.load(path, check_existing=True)
    return _setup_pano(scene, img, "IMAGE", scene.r5_settings.sky_image_rotation)


# ---- in-game sky dome (round image: zenith in the centre, horizon on the circle) ----
def find_dome_circle(img):
    """Centre and radius of the sky disc in UV units: the area that differs from the corner colour.
    Cached on the image."""
    if "r5_circle" in img:
        return tuple(img["r5_circle"])
    w, h = img.size
    if not w or not h:
        raise ValueError("Cannot read the sky image")
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    step = max(1, max(w, h) // 512)
    a = px.reshape(h, w, 4)[::step, ::step, :3]
    hh, ww = a.shape[:2]
    corners = np.concatenate([a[:4, :4].reshape(-1, 3), a[:4, -4:].reshape(-1, 3),
                              a[-4:, :4].reshape(-1, 3), a[-4:, -4:].reshape(-1, 3)])
    fill = np.median(corners, axis=0)
    img["r5_fill"] = [float(c) for c in fill]
    diff = np.abs(a - fill).max(axis=2) > 0.04
    ys, xs = np.nonzero(diff)
    if len(xs) < 100:
        circle = (0.5, 0.5, 0.5)
    else:
        x0, x1 = np.percentile(xs, [0.2, 99.8])
        y0, y1 = np.percentile(ys, [0.2, 99.8])
        cx, cy = (x0 + x1 + 1) / 2 / ww, (y0 + y1 + 1) / 2 / hh
        r = max((x1 - x0 + 1) / ww, (y1 - y0 + 1) / hh) / 2
        circle = (float(cx), float(cy), float(min(max(r, 0.1), 0.75)))
    img["r5_circle"] = circle
    return circle


def is_dome_image(img):
    w, h = img.size
    return w > 0 and abs(w - h) <= max(2, w // 100)


def setup_dome(scene, path):
    """World shader mapping a round sky image onto the sky: view direction -> angle from the zenith
    -> radius in the disc (equidistant / equisolid / orthographic), azimuth -> angle in the disc."""
    s = scene.r5_settings
    w, nt, bg = _world(scene)
    N, L = nt.nodes, nt.links
    img = bpy.data.images.load(path, check_existing=True)
    cx, cy, rad = find_dome_circle(img)
    k = rad * s.sky_dome_horizon          # disc radius at the horizon (UV units)

    def mth(op, a, b=None, loc=(0, 0)):
        n = N.new("ShaderNodeMath"); n.operation = op; n.location = loc
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                L.new(v, n.inputs[i])
        return n.outputs[0]

    tc = N.new("ShaderNodeTexCoord"); tc.location = (-1600, 0)
    rot = N.new("ShaderNodeVectorRotate"); rot.location = (-1400, 0); rot.name = "R5_Rot"
    rot.rotation_type = "Z_AXIS"
    rot.inputs["Angle"].default_value = math.radians(s.sky_image_rotation)
    L.new(tc.outputs["Generated"], rot.inputs["Vector"])
    sep = N.new("ShaderNodeSeparateXYZ"); sep.location = (-1200, 0)
    L.new(rot.outputs[0], sep.inputs[0])
    x, y, z = sep.outputs[0], sep.outputs[1], sep.outputs[2]
    cz = N.new("ShaderNodeClamp"); cz.location = (-1000, -200)
    cz.inputs["Min"].default_value, cz.inputs["Max"].default_value = -1.0, 1.0
    L.new(z, cz.inputs["Value"])
    theta = mth("ARCCOSINE", cz.outputs[0], loc=(-850, -200))            # 0 = zenith, pi/2 = horizon
    half_pi = math.pi / 2
    if s.sky_dome_projection == "EQUISOLID":
        r = mth("MULTIPLY", mth("SINE", mth("MULTIPLY", theta, 0.5, (-700, -200)), loc=(-550, -200)),
                k / math.sin(math.pi / 4), (-400, -200))
    elif s.sky_dome_projection == "ORTHO":
        above = mth("SINE", mth("MINIMUM", theta, half_pi, (-700, -150)), loc=(-550, -150))
        below = mth("DIVIDE", mth("MAXIMUM", mth("SUBTRACT", theta, half_pi, (-700, -300)), 0.0,
                                  (-550, -300)), half_pi, (-400, -300))
        r = mth("MULTIPLY", mth("ADD", above, below, (-300, -200)), k, (-200, -200))
    else:   # EQUIDISTANT
        r = mth("MULTIPLY", theta, k / half_pi, (-550, -200))
    # azimuth: unit horizontal direction
    ln = mth("SQRT", mth("ADD", mth("MULTIPLY", x, x, (-1000, 200)), mth("MULTIPLY", y, y, (-1000, 100)),
                         (-850, 150)), loc=(-700, 150))
    ln = mth("MAXIMUM", ln, 1e-6, (-550, 150))
    dx = mth("DIVIDE", x, ln, (-400, 250))
    dy = mth("DIVIDE", y, ln, (-400, 100))
    if s.sky_dome_mirror:
        dx = mth("MULTIPLY", dx, -1.0, (-250, 250))
    u = mth("ADD", mth("MULTIPLY", dx, r, (-100, 250)), cx, (50, 250))
    v = mth("ADD", mth("MULTIPLY", dy, r, (-100, 100)), cy, (50, 100))
    cmb = N.new("ShaderNodeCombineXYZ"); cmb.location = (200, 150)
    L.new(u, cmb.inputs[0]); L.new(v, cmb.inputs[1])
    tex = N.new("ShaderNodeTexImage"); tex.location = (400, 150)
    tex.image = img
    tex.extension = "EXTEND"
    tex.interpolation = "Linear"
    L.new(cmb.outputs[0], tex.inputs["Vector"])
    # Outside the disc (below the horizon): the image's background colour, not the stretched border
    outside = mth("GREATER_THAN", r, rad * 0.995, (200, -100))
    fill = list(img.get("r5_fill", (0.5, 0.5, 0.5)))[:3]
    if not img.is_float:   # 8-bit pixels are sRGB encoded, the node colour is linear
        fill = [_to_linear(c) for c in fill]
    res = _mix_rgba(nt, outside, tex.outputs["Color"], tuple(fill) + (1.0,), (650, 150))
    return _finish(scene, w, nt, bg, res, _mean_luminance(img, (cx, cy, rad)), "DOME")


# ---- cubemap -> panorama ----
_FACE_TAGS = (
    ("+X", ("_000", "px", "posx", "right", "_rt")),
    ("-X", ("_001", "nx", "negx", "left", "_lf")),
    ("+Y", ("_002", "py", "posy", "up", "_up")),
    ("-Y", ("_003", "ny", "negy", "down", "_dn")),
    ("+Z", ("_004", "pz", "posz", "front", "_ft")),
    ("-Z", ("_005", "nz", "negz", "back", "_bk")),
)


def find_cube_faces(any_face_path):
    """Find the 6 faces next to one of them. Returns {"+X": path, ...} or raises ValueError."""
    folder = os.path.dirname(any_face_path)
    stem = os.path.splitext(os.path.basename(any_face_path))[0].lower()
    ext_ok = (".png", ".jpg", ".jpeg", ".tga", ".dds", ".exr", ".hdr", ".tif", ".tiff", ".bmp", ".webp")
    files = [f for f in os.listdir(folder) if f.lower().endswith(ext_ok)]
    all_tags = sorted({t.strip("_") for _k, tags in _FACE_TAGS for t in tags}, key=len, reverse=True)
    # common prefix = the stem without its face tag
    prefix = stem
    for t in all_tags:
        if stem.endswith(t):
            prefix = stem[: -len(t)].rstrip("_-. ")
            break
    faces = {}
    for key, tags in _FACE_TAGS:
        wanted = {t.strip("_") for t in tags}
        for f in files:
            fs = os.path.splitext(f)[0].lower()
            if fs.startswith(prefix) and fs[len(prefix):].strip("_-. ") in wanted:
                faces[key] = os.path.join(folder, f)
                break
    if len(faces) != 6:
        raise ValueError(f"Found {len(faces)}/6 cubemap faces next to {os.path.basename(any_face_path)} "
                         f"(expected name_000..name_005, px/nx/py/ny/pz/nz or right/left/up/down/front/back)")
    return faces


def _load_rgba(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)[::-1]          # top row first


def cubemap_to_panorama(faces, width=2048, rotation_deg=0.0, y_up=True):
    """Convert 6 cube faces (D3D/OpenGL convention) into an equirectangular image (numpy HxWx4)."""
    data = {k: _load_rgba(p) for k, p in faces.items()}
    h = width // 2
    u = (np.arange(width) + 0.5) / width
    v = (np.arange(h) + 0.5) / h
    lon = (u - 0.5) * 2 * math.pi + math.radians(rotation_deg)
    lat = (0.5 - v) * math.pi                  # top row = +90 degrees
    lon, lat = np.meshgrid(lon, lat)
    # Blender equirect: u = atan2(y, -x) / 2pi + 0.5
    bx = -np.cos(lat) * np.cos(lon)
    by = np.cos(lat) * np.sin(lon)
    bz = np.sin(lat)
    if y_up:   # cube faces authored Y-up (D3D / RSX texture arrays): Blender (x, y, z) -> (x, z, y)
        X, Y, Z = bx, bz, by
    else:
        X, Y, Z = bx, by, bz
    ax, ay, az = np.abs(X), np.abs(Y), np.abs(Z)
    out = np.zeros((h, width, 4), np.float32)
    rules = (
        ("+X", (ax >= ay) & (ax >= az) & (X > 0), -Z, -Y, ax),
        ("-X", (ax >= ay) & (ax >= az) & (X <= 0), Z, -Y, ax),
        ("+Y", (ay > ax) & (ay >= az) & (Y > 0), X, Z, ay),
        ("-Y", (ay > ax) & (ay >= az) & (Y <= 0), X, -Z, ay),
        ("+Z", (az > ax) & (az > ay) & (Z > 0), X, -Y, az),
        ("-Z", (az > ax) & (az > ay) & (Z <= 0), -X, -Y, az),
    )
    for key, mask, sc, tc, ma in rules:
        img = data[key]
        fh, fw = img.shape[:2]
        s_ = (sc[mask] / ma[mask] + 1) * 0.5
        t_ = (tc[mask] / ma[mask] + 1) * 0.5
        xi = np.clip((s_ * fw).astype(np.int32), 0, fw - 1)
        yi = np.clip((t_ * fh).astype(np.int32), 0, fh - 1)
        out[mask] = img[yi, xi]
    out[..., 3] = 1.0
    return out


def setup_cubemap(scene, any_face_path):
    s = scene.r5_settings
    faces = find_cube_faces(any_face_path)
    pano = cubemap_to_panorama(faces, int(s.sky_panorama_width), s.sky_image_rotation, s.sky_cubemap_y_up)
    h, w = pano.shape[:2]
    img = bpy.data.images.get(PANO)
    if img is not None and tuple(img.size) != (w, h):
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(PANO, w, h, alpha=False, float_buffer=True)
    img.pixels.foreach_set(pano[::-1].ravel())
    img.pack()
    if "r5_mean" in img:
        del img["r5_mean"]
    return _setup_pano(scene, img, "CUBEMAP")       # rotation already baked into the panorama


# --------------------------------------------------------------------------
# Live updates
# --------------------------------------------------------------------------
def _current(scene):
    w = bpy.data.worlds.get(WORLD)
    if w is None or scene.world != w or not w.get("r5_sky_mode"):
        return None, None
    return w, w["r5_sky_mode"]


def refresh(scene):
    """Rebuild the current sky with the current settings (structure changes)."""
    w, mode = _current(scene)
    if w is None:
        return
    s = scene.r5_settings
    path = bpy.path.abspath(s.sky_image) if s.sky_image else ""
    if mode == "PROCEDURAL":
        setup_procedural(scene)
    elif mode == "DOME" and os.path.isfile(path):
        setup_dome(scene, path)
    elif mode == "IMAGE" and os.path.isfile(path):
        setup_image(scene, path)
    elif mode == "CUBEMAP" and bpy.data.images.get(PANO) is not None:
        _setup_pano(scene, bpy.data.images[PANO], "CUBEMAP")


def update_live(scene):
    """Apply value changes in place (no shader rebuild); rebuild only when the structure changes."""
    s = scene.r5_settings
    w, mode = _current(scene)
    if w is None:
        _sun_if_present(scene, s)
        _fog_object(scene, s)
        return
    nodes = w.node_tree.nodes
    want_atmo = s.sky_atmosphere and mode != "PROCEDURAL"
    if ("R5_Atmo_Sky" in nodes) != want_atmo:
        refresh(scene)
        return
    _fog_object(scene, s)
    for name, disc in (("R5_Sky_Nishita", True), ("R5_Atmo_Sky", False)):
        n = nodes.get(name)
        if n is not None:
            _set_nishita(n, s, sun_disc=disc)
    n = nodes.get("R5_Atmo_Scale")
    if n is not None:
        n.inputs["Scale"].default_value = float(w.get("r5_mean", 0.5)) / _nishita_base(sun_angles(s)[0])
    n = nodes.get("R5_Atmo_Strength")
    if n is not None:
        n.inputs[1].default_value = s.sky_atmosphere_strength
    n = nodes.get("R5_Bg")
    if n is not None:
        n.inputs["Strength"].default_value = s.sky_strength
    n = nodes.get("R5_Rot")
    if n is not None:
        n.inputs["Angle"].default_value = math.radians(s.sky_image_rotation)
    n = nodes.get("R5_Rot_Map")
    if n is not None and mode == "IMAGE":
        n.inputs["Rotation"].default_value[2] = math.radians(s.sky_image_rotation)
    _sun(scene, s)


def _sun_if_present(scene, s):
    if bpy.data.objects.get(SUN) is not None:
        _sun(scene, s)


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------
class R5_OT_setup_sky(bpy.types.Operator):
    """Create the world sky (and a sun lamp) for the map"""
    bl_idname = "r5.setup_sky"
    bl_label = "Apply sky"

    def execute(self, context):
        scene = context.scene
        s = scene.r5_settings
        try:
            if s.sky_mode == "PROCEDURAL":
                setup_procedural(scene)
            elif s.sky_mode in ("IMAGE", "DOME"):
                path = bpy.path.abspath(s.sky_image)
                if not os.path.isfile(path):
                    raise ValueError("Choose a sky image")
                img = bpy.data.images.load(path, check_existing=True)
                if s.sky_mode == "IMAGE" and is_dome_image(img):
                    # square image = in-game dome texture, not a 2:1 panorama
                    s.sky_mode = "DOME"
                    self.report({"INFO"}, "Square image: used as an in-game sky dome")
                if s.sky_mode == "DOME":
                    setup_dome(scene, path)
                else:
                    setup_image(scene, path)
            else:
                path = bpy.path.abspath(s.sky_image)
                if not os.path.isfile(path):
                    raise ValueError("Choose one of the 6 cubemap face images")
                setup_cubemap(scene, path)
        except (ValueError, RuntimeError, OSError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Sky applied ({s.sky_mode.lower()})")
        return {"FINISHED"}


class R5_OT_sun_color_reset(bpy.types.Operator):
    """Reset the sun colour (to the map's light_environment colour when one was loaded)"""
    bl_idname = "r5.sun_color_reset"
    bl_label = "Reset sun colour"

    def execute(self, context):
        s = context.scene.r5_settings
        s.sky_sun_color = default_sun_color(s)
        return {"FINISHED"}


class R5_OT_fog_from_map(bpy.types.Operator):
    """Set the fog from the map's own fog (env_fog_controller of the export): colour, density, base height and
    thickness"""
    bl_idname = "r5.fog_from_map"
    bl_label = "Use the map's fog"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        if not fog_from_map(scene):
            self.report({"WARNING"}, "No fog in the entities of this map")
            return {"CANCELLED"}
        s = scene.r5_settings
        if not s.sky_fog:
            s.sky_fog = True
        else:
            update_live(scene)
        self.report({"INFO"}, f"Map fog: {s.sky_fog_density * 1000:.2f} per km, thickness {s.sky_fog_thickness:.0f} m")
        return {"FINISHED"}


CLASSES = (R5_OT_setup_sky, R5_OT_sun_color_reset, R5_OT_fog_from_map)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
