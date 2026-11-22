"""Cast file reader (RSX and Legion+ exports), pure Python + numpy.

No external add-on is required. Reads the modl / mesh / matl / file nodes;
skeletons and weights are ignored (static props).

- RSX: a single .cast can hold several modl nodes "<name>_LOD<i>" -> one object per LOD.
- Textures: the role is taken from the file name (..._albedoTexture, ..._col, ..._nml, ..._gls...)
  then from the slot name; a file copied into several folders is only loaded once.
- Overlays (decals, _b2t, _bt...) are pushed along their normals -> no z-fighting.
- Faces using tool materials (triggers, clip, blocklight...) go to separate objects tagged
  "r5_group" so they can be hidden.
"""

import os
import re
import struct
import time

import bpy
import numpy as np

from . import progress
from .placement import long_path, walk_root

# property id -> (numpy dtype, components)
_PROP = {
    0x62: ("<u1", 1),   # b
    0x68: ("<u2", 1),   # h
    0x69: ("<u4", 1),   # i
    0x6C: ("<u8", 1),   # l
    0x66: ("<f4", 1),   # f
    0x64: ("<f8", 1),   # d
    0x7632: ("<f4", 2),  # 2v
    0x7633: ("<f4", 3),  # 3v
    0x7634: ("<f4", 4),  # 4v
}
_STR = 0x73
_HDR = struct.Struct("<IIQII")
_PH = struct.Struct("<HHI")


def _tag(s):
    return struct.unpack("<I", s.encode())[0]


T_ROOT, T_MODL, T_MESH, T_MATL, T_FILE, T_BONE = (_tag(t) for t in ("root", "modl", "mesh", "matl", "file", "bone"))

# Animated models (a real skeleton: loot bins, Tridents, MRVNs, replicators, elevator doors...) are exported
# Y-up, forward +Z (Maya axes), unlike static props (one "static_prop" bone, Z-up). Measured on Olympus: loot
# bin height on Y (0..65), Trident length on Z with its brake lights at -Z, door thickness on Z. Game axes:
# x = file z (forward), y = file x, z = file y (up).
Y_UP_TO_Z_UP = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
_STATIC_BONES = {"static_prop", ""}

# Custom property names (kept from older versions so existing .blend files keep working)
GROUP_KEY = "apx_group"

# --------------------------------------------------------------------------
# Material / model classification by name
# --------------------------------------------------------------------------
GROUPS = (
    ("trigger", "Triggers"),
    ("tool", "Tool brushes (clip, blocklight, skybox...)"),
    ("fx", "Light shafts & volumes"),
    ("particle", "Particle / mist cards (vent mist, steam...)"),
    ("gamemode", "Game mode barriers"),
    ("dev", "Dev textures / placeholders"),
)
_GROUP_RE = (
    ("trigger", re.compile(r"(^|_)tools?_?trigger|trigger|fogvolume|^tools_?fog")),
    ("tool", re.compile(r"^tools|^tool_|(^|_)(nodraw|playerclip|npcclip|clip|invisible|skybox|blocklight|"
                        r"occluder|areaportal|hint|skip|caulk|origin_brush)($|_)")),
    ("fx", re.compile(r"godray|god_ray|lightshaft|light_shaft|volumetric|light_beam|lightbeam|sunbeam|sun_beam|"
                      r"light_cone|lightcone|(^|_)atmosphere(_|$)|(^|_)haze(_|$)|dust_motes")),
    # Effect cards baked into props: crossed planes with a mist / steam texture that are animated particles
    # in game (vent_climatizer_mesh_idle02 = the big mist next to the cloud-seeding climatizer vents)
    ("particle", re.compile(r"_mesh_idle|(^|_)fog_all($|_)|(^|_)(mist|steam|smoke|vapou?r|exhaust)(_|$)")),
    ("gamemode", re.compile(r"^ar_.*(barrier|border|objective)|(^|_)(oob|out_of_bounds)($|_)")),
    # dev_*_light = light panels that are visible in game: not part of the "dev" group
    ("dev", re.compile(r"^dev_(?!.*light)|^test_|maya_test|^_default$|^default$")),
)
# Whole models that are light shafts / atmosphere volumes (opaque ellipses/cones in Blender)
FX_MODEL_RE = re.compile(r"godray|god_ray|(^|_)atmosphere(_|$)|lightshaft|light_shaft|volumetric|light_beam|"
                         r"sunbeam|sun_beam|light_cone|fog_volume|fogvolume|dust_motes")
_OVERLAY_RE = re.compile(r"_b2t($|_)|_bt($|_)|transparent|decal|(^|_)dcl|overlay|stencil|grime|graffiti|puddle|"
                         r"stain|drip|splatter|(^|_)cracks?($|_)|(^|_)burn_|sticker|_ovl($|_)")
_VCTINT_RE = re.compile(r"_vc($|_)|vert_?colou?r|_vcolor|_vt($|_)")
_FOLIAGE_RE = re.compile(r"leaf|leaves|foliage|grass|fern|bush|shrub|plant|tree|branch|ivy|vine|moss_clump|flower|"
                         r"weed|bark|needle|pine|palm|lily|lotus|petal|algae|kelp|seaweed|reed|clover")
_CUTOUT_RE = re.compile(r"(?:^|[_\-\s\d])(?:leaf|leaves|foliage|grass|fern|bush|shrub|plant|tree|branch|ivy|vine|"
                        r"moss_clump|flower|weed|fence|chainlink|chain_link|grate|grating|net|alpha|cutout|hair|"
                        r"fx|glass|lily|lilypad|lotus|petal|algae|kelp|seaweed|reed|cattail|clover)")

# Water surfaces. The game draws them with a water shader (depth colour, fog, foam from the depth): their albedo
# is often an 8x8 white placeholder (district_water_01_depth, canyonlands_river_water_02_depth) or a pale texture
# tinted by the shader, and the second layer of "<water>_<water foam>_bm" is foam the game shows by depth, not
# by vertex alpha. Rebuilt as dark, glossy water (see _water_fix).
_WATER_RE = re.compile(r"water|whirlpool|ocean")
_WATER_SURFACE_RE = re.compile(r"depth|shallow|murky|pool|whirlpool|river|ocean|hellscape|canal|wild|volatile|lake|pond")
_NOT_WATER_RE = re.compile(r"underwater|under_water|puddle|stain|tiles|floor|fall|splash|drip|caustic|decal|sign|"
                           r"ingot|treatment|bottle|tank|tower|_b2t|_bt$|fountain_modern|windswept|landscape")
_GROUND_RE = re.compile(r"mud|dirt|sand|ground|concrete|rock|grass|soil|gravel|metal|wall|moss")
WATER_TINT = (0.020, 0.055, 0.060)      # linear: dark blue-green, the colour of deep water in the game
WATER_LUMA = 0.10                       # water albedos are scaled to this mean brightness


# Colour of a surface whose textures exist nowhere (retail terrain layers r5mex did not write, trims of a
# Flowstate export...), guessed from its name: better than the white of an empty material, which looks like snow.
_NAME_COLORS = (
    (r"pink", (0.30, 0.16, 0.19)), (r"blue_green|bluegreen|stasis", (0.06, 0.13, 0.11)),
    (r"moss|algae", (0.07, 0.11, 0.04)), (r"grass|meadow|clover|lawn|turf", (0.10, 0.14, 0.05)),
    (r"leaves|leaf|foliage", (0.12, 0.09, 0.04)), (r"flower", (0.16, 0.13, 0.08)),
    (r"black_?sand|blacksand", (0.05, 0.05, 0.05)), (r"sand", (0.32, 0.28, 0.20)),
    (r"mud|wet", (0.10, 0.08, 0.05)), (r"dirt|soil|gravel|road|path|track|erosion|quarry", (0.17, 0.14, 0.10)),
    (r"rock|stone|cliff|crater|moon", (0.24, 0.24, 0.23)), (r"water", WATER_TINT),
    (r"metal|trim|steel|iron", (0.35, 0.35, 0.36)), (r"concrete|cement", (0.40, 0.39, 0.37)),
)


def is_glass_like(name):
    return bool(re.search(r"glass|window|glow|holo|screen|light|emissive|neon|_ilm|vfx|fx_", name.lower()))


def name_color(name):
    low = name.lower()
    for pat, col in _NAME_COLORS:
        if re.search(pat, low):
            return col
    return (0.30, 0.30, 0.30)


def is_water_material(name):
    low = name.lower()
    if "foam" in low and "depth" not in low:
        return False            # foam / rapids (canyonlands_river_water_foam_01): light in the game too
    return bool(_WATER_RE.search(low) and _WATER_SURFACE_RE.search(low) and not _NOT_WATER_RE.search(low))


def _water_layers(name):
    """(layer 1 is water, layer 2 is water) of a blend material name ("a_to_b_bm" or "a_b_bm")."""
    low = name.lower()
    if "_to_" in low:
        a, b = low.split("_to_", 1)
    elif _GROUND_RE.match(low):
        return False, True      # "mud_01_divided_wetlands_water_depth_01_bm": ground, then water
    else:
        return True, not _GROUND_RE.search(low)
    return (bool(_WATER_RE.search(a)) and not _GROUND_RE.search(a), bool(_WATER_RE.search(b)) and not _GROUND_RE.search(b))


def _image_luma(img):
    """Mean brightness of an image (sampled), or None."""
    try:
        w, h = img.size
        if not w or not h:
            return None
        px = np.empty(w * h * 4, np.float32)
        img.pixels.foreach_get(px)
    except (RuntimeError, ValueError):
        return None
    rgb = px.reshape(-1, 4)[:: max(1, (w * h) // 20000), :3]
    return float((rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)).mean())

# Blend materials ("_bm"): two layers (albedo/albedo2, normal/normal2, gloss/gloss2...)
_BM_RE = re.compile(r"_bm($|_)")
# ... which are still real overlays when their name says so
_BM_OVERLAY_RE = re.compile(r"transparent|decal|_b2t($|_)|_bt($|_)")

MAT_VERSION = 18


def material_group(name):
    low = name.lower()
    for g, rx in _GROUP_RE:
        if rx.search(low):
            return g
    return None


def is_fx_model(key):
    return FX_MODEL_RE.search(key.lower()) is not None


def is_blend_material(name):
    return _BM_RE.search(name.lower()) is not None


def is_overlay(name):
    low = name.lower()
    if _OVERLAY_RE.search(low) is None:
        return False
    # "..._water_puddles_bm", "..._grime_bm": full surfaces blending two layers, not decals
    if _BM_RE.search(low) and not _BM_OVERLAY_RE.search(low):
        return False
    return True


def is_vctint(name):
    return _VCTINT_RE.search(name.lower()) is not None


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
POOL_DIR = "_shared_textures"
_pools = {}       # start folder -> {lowercase file name: path} (or None)


def _pool_lookup(start_dir, name):
    """Textures gathered in a "_shared_textures" folder (de-duplicated copy of a map)."""
    idx = _pools.get(start_dir, False)
    if idx is False:
        idx = None
        d = start_dir
        for _ in range(4):
            cand = os.path.join(d, POOL_DIR)
            if os.path.isdir(long_path(cand)):
                idx = {f.lower(): long_path(os.path.join(cand, f)) for f in os.listdir(walk_root(cand))}
                break
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        _pools[start_dir] = idx
    if not idx:
        return None
    low = name.lower()
    hit = idx.get(low)
    if hit is None:
        stem = os.path.splitext(low)[0]
        for ext in (".png", ".dds", ".tga"):
            hit = idx.get(stem + ext)
            if hit:
                break
    return hit


def _read_file(path, tries=4):
    """Read with retries: an external / sleeping drive can return transient errors."""
    last = None
    for i in range(tries):
        try:
            with open(long_path(path), "rb") as f:
                return f.read()
        except OSError as exc:
            last = exc
            time.sleep(0.5 * (i + 1))
    raise last


class CastMesh:
    __slots__ = ("name", "pos", "nrm", "uv", "vc", "faces", "mat", "modl")

    def __init__(self):
        self.name = ""
        self.pos = self.nrm = self.uv = self.vc = self.faces = None
        self.mat = 0
        self.modl = 0


class CastMaterial:
    __slots__ = ("name", "type", "slots")

    def __init__(self):
        self.name = ""
        self.type = ""
        self.slots = {}   # slot -> hash


class CastFile:
    def __init__(self, path):
        self.path = path
        self.dir = os.path.dirname(path)
        self.meshes = []
        self.models = []      # modl node names (RSX: "<name>_LOD<i>")
        self.materials = {}   # hash -> CastMaterial
        self.files = {}       # hash -> path
        self.bones = []       # bone names (only used to tell animated models apart)
        self.up = ""          # "y" / "z" when the exporter says it (modl property "up")
        self._cur_modl = 0
        self._buf = _read_file(path)
        magic, _ver, roots, _flags = struct.unpack_from("<4sIII", self._buf, 0)
        if magic != b"cast":
            raise ValueError(f"{path} is not a Cast file")
        off = 16
        for _ in range(roots):
            off = self._node(off)

    def _props(self, off, count):
        buf = self._buf
        out = {}
        for _ in range(count):
            pid, nlen, alen = _PH.unpack_from(buf, off)
            off += 8
            name = buf[off:off + nlen].decode("utf-8", "replace")
            off += nlen
            if pid == _STR:
                end = buf.index(b"\0", off)
                out[name] = buf[off:end].decode("utf-8", "replace")
                off = end + 1
                continue
            dt, comp = _PROP[pid]
            n = alen * comp
            arr = np.frombuffer(buf, dtype=dt, count=n, offset=off)
            if comp > 1:
                arr = arr.reshape(-1, comp)
            out[name] = arr
            off += n * np.dtype(dt).itemsize
        return out, off

    def _node(self, off):
        ident, size, h, pcount, ccount = _HDR.unpack_from(self._buf, off)
        props, coff = self._props(off + 24, pcount)
        if ident == T_MODL:
            self._cur_modl = len(self.models)
            self.models.append(props.get("n", ""))
            up = props.get("up")
            if isinstance(up, str) and up.lower() in ("y", "z"):
                self.up = up.lower()
        elif ident == T_BONE:
            n = props.get("n")
            self.bones.append(n if isinstance(n, str) else "")
        elif ident == T_MESH:
            m = CastMesh()
            m.name = props.get("n", "")
            m.pos = props.get("vp")
            m.nrm = props.get("vn")
            m.uv = props.get("u0")
            vc = props.get("vc")
            if vc is not None and vc.dtype == np.uint32 and m.pos is not None and len(vc) == len(m.pos):
                m.vc = vc
            f = props.get("f")
            m.faces = f.astype(np.int64) if f is not None else None
            mh = props.get("m")
            m.mat = int(mh[0]) if mh is not None and len(mh) else 0
            m.modl = self._cur_modl
            if m.pos is not None and m.faces is not None and len(m.faces) >= 3:
                self.meshes.append(m)
        elif ident == T_MATL:
            if h not in self.materials:
                mat = CastMaterial()
                mat.name = props.get("n", f"mat_{h:x}")
                mat.type = props.get("t", "")
                for k, v in props.items():
                    if k not in ("n", "t") and isinstance(v, np.ndarray) and v.dtype == np.uint64 and len(v):
                        mat.slots[k] = int(v[0])
                self.materials[h] = mat
        elif ident == T_FILE:
            p = props.get("p")
            if p:
                self.files[h] = p
        for _ in range(ccount):
            coff = self._node(coff)
        return off + size

    def mat_name(self, h):
        cm = self.materials.get(h)
        return cm.name if cm else "r5_default"

    @property
    def y_up(self):
        """Animated model exported Y-up (see Y_UP_TO_Z_UP)."""
        if self.up:
            return self.up == "y"
        return any(b.lower() not in _STATIC_BONES for b in self.bones)

    def texture_path(self, h):
        p = self.files.get(h)
        if not p:
            return None
        p = p.replace("\\", os.sep).replace("/", os.sep)
        p = p if os.path.isabs(p) else os.path.join(self.dir, p)
        if os.path.isfile(long_path(p)):
            return long_path(p)
        # RSX: the reference may have no extension, or the export may be .dds/.tga
        base = os.path.splitext(p)[0]
        for ext in (".png", ".dds", ".tga", ".PNG", ".DDS"):
            if os.path.isfile(long_path(base + ext)):
                return long_path(base + ext)
            if os.path.isfile(long_path(p + ext)):
                return long_path(p + ext)
        pooled = _pool_lookup(self.dir, os.path.basename(p))
        return pooled or p

    def free(self):
        self.meshes = []
        self._buf = None


# --------------------------------------------------------------------------
# Textures
# --------------------------------------------------------------------------
_ROLE_KEYS = (
    # (pattern at the end of the file name / slot name, role). Order = priority.
    ("albedo2texture", "albedo2"), ("gloss2texture", "gloss2"), ("spec2texture", "specular2"),
    ("specular2texture", "specular2"), ("detailtexture", "detail"),
    ("albedotexture", "albedo"), ("diffusetexture", "albedo"), ("basecolor", "albedo"),
    ("normal2texture", "normal2"), ("detailnormaltexture", "detailnormal"),
    ("normaltexture", "normal"), ("glosstexture", "gloss"), ("speculartexture", "specular"),
    ("spectexture", "specular"), ("aotexture", "ao"), ("cavitytexture", "cavity"),
    ("emissivemultiplytexture", "emissivemul"), ("emissivetexture", "emissive"), ("emittexture", "emissive"),
    ("illumtexture", "emissive"), ("opacitymultiplytexture", "opacity"), ("opacitytexture", "opacity"),
    ("layerblendtexture", "layerblend"),
)
# RSX short suffixes (s_TextureTypeMap)
_SHORT = {"col": "albedo", "nml": "normal", "spc": "specular", "gls": "gloss", "ao": "ao", "cav": "cavity",
          "ehl": "emissive", "ehm": "emissivemul", "ilm": "emissive", "opa": "opacity", "msk": "mask",
          "det": "detail", "tnt": "tint", "sctr": "scatter", "thk": "thickness"}
_SLOT = {"albedo": "albedo", "diffuse": "albedo", "color": "albedo", "normal": "normal", "gloss": "gloss",
         "specular": "specular", "spec": "specular", "ao": "ao", "cavity": "cavity", "emissive": "emissive",
         "emission": "emissive", "opacity": "opacity", "roughness": "roughness", "metallic": "metallic",
         "metal": "metallic"}
# WHOLE words of a material name that indicate a real emissive (Legion+ often puts the AO in the
# "emissive" slot: "rock_lava_*" is volcanic rock, not glowing lava).
_EMISSIVE_WORDS = {"light", "lights", "lamp", "lamps", "screen", "screens", "emissive", "emis", "glow", "neon",
                   "holo", "led", "leds", "monitor", "illum", "lit", "lightstrip"}
_EMISSIVE_RE = re.compile(r"lava_(river|flow|fall|pool|stream)|magma|molten")
# Legion+ names the slots by POSITION (albedo, normal, gloss, specular, emissive, ao, cavity), whatever
# the shader. Blend materials store [albedo, albedo2, normal, normal2, gloss, gloss2, spec]:
_BM_SLOT_ROLES = {"albedo": "albedo", "normal": "albedo2", "gloss": "normal", "specular": "normal2",
                  "emissive": "gloss", "ao": "gloss2", "cavity": "specular"}


def texture_roles(cast, cm, name):
    """role -> texture hash. Roles found in the file names win; slot names (positional) only fill
    the roles still missing, using the blend-material layout when needed.
    Returns (roles, unnamed slots [(slot, hash)])."""
    named, unnamed = {}, []
    for slot, h2 in cm.slots.items():
        if isinstance(h2, str) and h2.startswith("lib:"):
            # texture found by material name (_fill_from_library): the slot name is its role. The file may be
            # layer 2 of a blend material ("..._to_<this material>_bm_albedo2Texture.png") used as layer 1 here
            role = role_from_filename(f"lib_{slot}.png")
        else:
            role = role_from_filename(cast.texture_path(h2))
        if role is None:
            unnamed.append((slot, h2))
        else:
            named.setdefault(role, h2)
    blend = is_blend_material(name) or any(r in named for r in ("albedo2", "normal2", "gloss2"))
    roles = dict(named)
    for slot, h2 in unnamed:
        key = slot.lower().replace("texture", "")
        if blend:
            role = _BM_SLOT_ROLES.get(key, key)
        else:
            role = _SLOT.get(key, key)
            if role == "emissive" and not _looks_emissive(name):
                role = "emissive?"
        roles.setdefault(role, h2)
    return roles, (unnamed if not blend else [])


def _mask_like(img):
    """Grey-scale image with some variation (opacity / blend mask), i.e. not a normal map or a colour map."""
    try:
        w, h = img.size
        if not w or not h:
            return False
        px = np.empty(w * h * 4, np.float32)
        img.pixels.foreach_get(px)
    except (RuntimeError, ValueError):
        return False
    rgb = px.reshape(-1, 4)[:: max(1, (w * h) // 20000), :3]
    grey = np.abs(rgb[:, 0] - rgb[:, 1]).mean() < 0.02 and np.abs(rgb[:, 1] - rgb[:, 2]).mean() < 0.02
    return bool(grey and rgb[:, 0].std() > 0.05)


def role_from_filename(path):
    if not path:
        return None
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    stem = re.sub(r"_(\d{3}|level\d+)$", "", stem)   # RSX arrays / mips
    for key, role in _ROLE_KEYS:
        if stem.endswith(key) or ("_" + key) in stem:
            return role
    tail = stem.rsplit("_", 1)[-1]
    if "_" in stem and tail in _SHORT:
        return _SHORT[tail]
    return None


def _looks_emissive(name):
    low = name.lower()
    if _EMISSIVE_RE.search(low):
        return True
    return any(t in _EMISSIVE_WORDS for t in re.split(r"[_\d\-\s]+", low))


def _png_has_alpha(path):
    try:
        with open(long_path(path), "rb") as f:
            head = f.read(33)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return True   # dds/tga: assume there may be alpha
        return head[25] in (4, 6)
    except OSError:
        return False


_img_cache = {}
_img_missing = set()


def clear_image_cache():
    _img_cache.clear()
    _img_missing.clear()
    _light_cache.clear()


# Texture libraries: folders searched by MATERIAL NAME when a material has no texture file of its own
# (RSX model exports without textures, R5Flowstate collision materials...): "<material>_<slot>Texture.png"
# files of the game caches (_shared_textures) and of Legion+ exports (_models/*/_images).
_tex_libs = []
_tex_index = {"idx": None}
_TEX_EXT = (".png", ".dds", ".tga")


def set_texture_libraries(dirs):
    seen, libs = set(), []
    for d in dirs:
        if d and os.path.isdir(d) and os.path.normcase(d) not in seen:
            seen.add(os.path.normcase(d))
            libs.append(d)
    if libs != _tex_libs:
        _tex_libs[:] = libs
        _tex_index["idx"] = None


def material_textures(name):
    """{"albedoTexture": path, ...} found for this material name in the texture libraries."""
    if not _tex_libs or not name:
        return {}
    idx = _tex_index["idx"]
    if idx is None:
        idx = {}
        for d in _tex_libs:
            for root, _dirs, files in os.walk(walk_root(d)):
                for f in files:
                    low = f.lower()
                    if not low.endswith(_TEX_EXT):
                        continue
                    stem = os.path.splitext(f)[0]
                    cut = stem.rfind("_")
                    if cut <= 0 or not stem[cut + 1:].lower().endswith("texture"):
                        continue
                    idx.setdefault(stem[:cut].lower(), {}).setdefault(stem[cut + 1:], long_path(os.path.join(root, f)))
        # Layers of the blend materials "<a>_to_<b>_bm": albedo/normal/gloss/spec = material a, albedo2/normal2...
        # = material b. Retail terrain layers (Broken Moon: divided_cracked_moon_sand_01, the grasses...) are
        # written without their own texture files, but every transition between two layers holds both.
        derived = {}
        for mat, slots in idx.items():
            m = _BM_PAIR_RE.match(mat)
            if not m:
                continue
            for slot, path in slots.items():
                base = slot[:-len("texture")]
                if base.endswith("2"):
                    derived.setdefault(m.group(2), {}).setdefault(base[:-1] + slot[-7:], path)
                else:
                    derived.setdefault(m.group(1), {}).setdefault(slot, path)
        for mat, slots in derived.items():
            if mat not in idx:
                idx[mat] = slots
        _tex_index["idx"] = idx
    return idx.get(name.lower().rsplit("/", 1)[-1], {})


_BM_PAIR_RE = re.compile(r"^(.+?)_to_(.+)_bm$")


def _fill_from_library(cast, cm, name):
    """When none of the material's own textures exists, take the textures of the same material name from the
    texture libraries."""
    if cm is None or not _tex_libs:
        return
    for h2 in cm.slots.values():
        p = cast.texture_path(h2)
        if p and os.path.isfile(long_path(p)):
            return
    lib = material_textures(name)
    if not lib:
        return
    cm.slots = {}
    for slot, path in lib.items():
        key = "lib:" + path
        cast.files[key] = path
        cm.slots[slot] = key


def _exists(path, tries=3):
    """isfile with retries: external / sleeping drives sometimes report existing files as missing."""
    if path in _img_missing:
        return False
    for i in range(tries):
        if os.path.isfile(long_path(path)):
            return True
        time.sleep(0.2 * (i + 1))
    _img_missing.add(path)
    print(f"[R5] texture not found: {path}")
    return False


def _load_image(path, tries=3):
    for i in range(tries):
        try:
            return bpy.data.images.load(long_path(path), check_existing=True)
        except RuntimeError:
            time.sleep(0.3 * (i + 1))
    print(f"[R5] could not read texture: {path}")
    return None


def _image(path, non_color):
    """Load an image only once, even if it is copied into several folders."""
    if not path or not _exists(path):
        return None
    try:
        key = f"{os.path.basename(path).lower()}|{os.path.getsize(long_path(path))}"
    except OSError:
        return None
    img = _img_cache.get(key)
    if img is not None:
        try:
            img.name  # noqa: B018  (still valid?)
            return img
        except ReferenceError:
            pass
    img = next((i for i in bpy.data.images if i.get("apx_key") == key), None)
    if img is None:
        img = _load_image(path)
        if img is None:
            return None
        img["apx_key"] = key
    if non_color:
        try:
            img.colorspace_settings.name = "Non-Color"
        except TypeError:
            pass
    _img_cache[key] = img
    return img


# --------------------------------------------------------------------------
# Materials
# --------------------------------------------------------------------------
_light_cache = {}


def _light_albedo(img):
    """True for a near-uniform white / light grey colour map (a texture meant to be tinted)."""
    if img is None:
        return False
    key = img.get("apx_key") or img.name
    v = _light_cache.get(key)
    if v is not None:
        return v
    v = False
    small = None
    try:
        w, h = img.size
        if w and h:
            small = img.copy()
            small.scale(16, 16)
            px = np.empty(16 * 16 * 4, np.float32)
            small.pixels.foreach_get(px)
            rgb = px.reshape(-1, 4)[:, :3]
            v = bool(rgb.mean() > 0.78 and (rgb.max(1) - rgb.min(1)).mean() < 0.06 and rgb.std() < 0.08)
    except (RuntimeError, ValueError, AttributeError):
        v = False
    finally:
        if small is not None:
            try:
                bpy.data.images.remove(small)
            except (RuntimeError, ReferenceError):
                pass
    _light_cache[key] = v
    return v


def _sock(sockets, identifier):
    """The Mix node has several sockets named "A"/"B"/"Result" (float, vector, color):
    pick by identifier, otherwise the link goes to a disabled socket and is ignored."""
    for sk in sockets:
        if sk.identifier == identifier:
            return sk
    base = identifier.split("_")[0]
    return next(sk for sk in sockets if sk.name == base and sk.enabled)


def _mix_rgba(nt, loc, blend):
    n = nt.nodes.new("ShaderNodeMix")
    n.data_type = "RGBA"
    n.blend_type = blend
    n.location = loc
    return n


def _set_render_method(mat, method):
    if hasattr(mat, "surface_render_method"):
        try:
            mat.surface_render_method = method     # 'DITHERED' / 'BLENDED' (EEVEE 4.2+)
        except TypeError:
            pass
    if hasattr(mat, "blend_method"):
        try:
            mat.blend_method = "BLEND" if method == "BLENDED" else "HASHED"
        except TypeError:
            pass


def _reset_tree(mat):
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (300, 0)
    b = nt.nodes.new("ShaderNodeBsdfPrincipled")
    nt.links.new(b.outputs[0], out.inputs["Surface"])
    return b


def make_additive(mat, strength=1.0):
    """Turn a material into an additive light effect (transparent + emission), the way light
    shafts / atmosphere volumes are drawn in game. Works in EEVEE and Cycles."""
    if mat is None or mat.get("r5_additive"):
        return
    mat.use_nodes = True
    nt = mat.node_tree
    img_node = next((n for n in nt.nodes if n.type == "TEX_IMAGE" and n.label == "albedo"), None)
    img = img_node.image if img_node else None
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (600, 0)
    add = nt.nodes.new("ShaderNodeAddShader"); add.location = (400, 0)
    tr = nt.nodes.new("ShaderNodeBsdfTransparent"); tr.location = (200, 100)
    em = nt.nodes.new("ShaderNodeEmission"); em.location = (200, -100)
    em.inputs["Strength"].default_value = strength
    if img is not None:
        t = nt.nodes.new("ShaderNodeTexImage"); t.image = img; t.label = "albedo"; t.location = (-300, -100)
        mul = nt.nodes.new("ShaderNodeMath"); mul.operation = "MULTIPLY"; mul.location = (0, -250)
        nt.links.new(t.outputs["Color"], em.inputs["Color"])
        nt.links.new(t.outputs["Alpha"], mul.inputs[0])
        mul.inputs[1].default_value = strength
        nt.links.new(mul.outputs[0], em.inputs["Strength"])
        nt.nodes.active = t
    nt.links.new(tr.outputs[0], add.inputs[0])
    nt.links.new(em.outputs[0], add.inputs[1])
    nt.links.new(add.outputs[0], out.inputs["Surface"])
    _set_render_method(mat, "BLENDED")
    mat.use_backface_culling = False
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True
    mat["r5_additive"] = True


def make_unlit(mat):
    """Sky dome materials (sky, stars, far ground of the 3D skybox): drawn without lighting in game. The base
    colour becomes an emission (its alpha kept), so the sky shows its own colours, whatever the lights."""
    if mat is None or not mat.use_nodes or mat.get("r5_unlit"):
        return False
    nt = mat.node_tree
    N, L = nt.nodes, nt.links
    outs = [n for n in N if n.type == "OUTPUT_MATERIAL"]
    out = next((n for n in outs if n.is_active_output), outs[0] if outs else None)
    bsdf = next((n for n in N if n.type == "BSDF_PRINCIPLED"), None)
    if out is None or bsdf is None:
        return False
    em = N.new("ShaderNodeEmission"); em.name = "R5_Unlit"; em.location = (bsdf.location.x, bsdf.location.y + 250)
    base = bsdf.inputs["Base Color"]
    if base.links:
        L.new(base.links[0].from_socket, em.inputs["Color"])
    else:
        em.inputs["Color"].default_value = base.default_value
    shader = em.outputs[0]
    alpha = bsdf.inputs["Alpha"]
    if alpha.links:
        tr = N.new("ShaderNodeBsdfTransparent"); tr.location = (em.location.x, em.location.y + 150)
        mix = N.new("ShaderNodeMixShader"); mix.name = "R5_Unlit_Alpha"; mix.location = (em.location.x + 200, em.location.y)
        L.new(alpha.links[0].from_socket, mix.inputs[0])
        L.new(tr.outputs[0], mix.inputs[1])
        L.new(em.outputs[0], mix.inputs[2])
        shader = mix.outputs[0]
    L.new(shader, out.inputs["Surface"])
    mat.use_backface_culling = False           # seen from inside
    mat["r5_unlit"] = True
    return True


def is_vfx_model(key):
    """Effect shells drawn by a VFX shader in game (vfx_moon_mu1_z07_dome: the energy dome of Broken Moon,
    a/b/c_olympus_energy_dome_03: the energy dome of Olympus)."""
    low = key.lower()
    return low.startswith("vfx_") or "energy_dome" in low


# Materials drawn by an energy shader in game: the storm of the Olympus energy dome, electric rings, zipline
# magnets, hover pads... Their albedo is a flat grey; the electric pattern is a grey-scale texture in an
# unnamed slot.
_ENERGY_RE = re.compile(r"storm|(^|_)energy(_|$)(?!tower)|electric_mat|plasma|force_?field")


def is_energy_material(name):
    return _ENERGY_RE.search(name.lower()) is not None


# Additive light effects: holograms (E-District statues, lotus, star drips, mall holograms), the beams and the
# energy of the jump launchers and zipline magnets. In game: a white emissive texture tinted by the vertex colour,
# faded by the vertex alpha / opacity texture, added on top of the scene.
_GLOW_RE = re.compile(r"hologram|(^|_)holo(_|$)|holo_(cast|fill|glow|scan)|projection|neon_?glow|(^|_)glow(_|$)|"
                      r"magnet_(beam|energy|glow)|(hover|siphon|magnet)_energy|gravity_cannon|electric_mat")
_NOT_GLOW_RE = re.compile(r"projector|_frame|_stand|monitor_arm|_base_metal")


def is_glow_material(name):
    low = name.lower()
    return _GLOW_RE.search(low) is not None and _NOT_GLOW_RE.search(low) is None


def _build_glow(mat, nt, cast, roles):
    """Hologram / energy glow: transparent + emission (emissive x vertex colour), strength from the opacity
    texture (or the emissive brightness) x vertex alpha. Returns False when no texture could be loaded."""
    em_role = "emissive" if "emissive" in roles else "albedo"
    img = _image(cast.texture_path(roles[em_role]), False)
    if img is None:
        return False
    op = _image(cast.texture_path(roles["opacity"]), True) if "opacity" in roles else None
    nt.nodes.clear()
    N, L = nt.nodes, nt.links
    out = N.new("ShaderNodeOutputMaterial"); out.location = (900, 0)
    add = N.new("ShaderNodeAddShader"); add.location = (700, 0)
    tr = N.new("ShaderNodeBsdfTransparent"); tr.location = (450, 150)
    em = N.new("ShaderNodeEmission"); em.location = (450, -100)
    t = N.new("ShaderNodeTexImage"); t.image = img; t.label = "emissive"; t.location = (-600, 0)
    vc = N.new("ShaderNodeVertexColor"); vc.layer_name = "Col"; vc.location = (-600, -300)
    tint = _mix_rgba(nt, (0, -50), "MULTIPLY")
    _sock(tint.inputs, "Factor_Float").default_value = 1.0
    L.new(t.outputs["Color"], _sock(tint.inputs, "A_Color"))
    L.new(vc.outputs["Color"], _sock(tint.inputs, "B_Color"))
    L.new(_sock(tint.outputs, "Result_Color"), em.inputs["Color"])
    if op is not None:
        o = N.new("ShaderNodeTexImage"); o.image = op; o.label = "opacity"; o.location = (-600, 300)
        src = o.outputs["Color"]
    else:
        bw = N.new("ShaderNodeRGBToBW"); bw.location = (-250, 250)
        L.new(t.outputs["Color"], bw.inputs[0])
        src = bw.outputs[0]
    a = N.new("ShaderNodeMath"); a.operation = "MULTIPLY"; a.location = (0, 250)
    L.new(src, a.inputs[0]); L.new(vc.outputs["Alpha"], a.inputs[1])
    s = N.new("ShaderNodeMath"); s.operation = "MULTIPLY"; s.location = (200, 250)
    s.inputs[1].default_value = 3.0
    L.new(a.outputs[0], s.inputs[0])
    L.new(s.outputs[0], em.inputs["Strength"])
    L.new(tr.outputs[0], add.inputs[0])
    L.new(em.outputs[0], add.inputs[1])
    L.new(add.outputs[0], out.inputs["Surface"])
    nt.nodes.active = t
    _set_render_method(mat, "BLENDED")
    mat.use_backface_culling = False
    if hasattr(mat, "use_transparency_overlap"):
        mat.use_transparency_overlap = False
    if hasattr(mat, "shadow_method"):
        try:
            mat.shadow_method = "NONE"
        except TypeError:
            pass
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True
    mat.diffuse_color = (0.5, 0.8, 1.0, 0.3)
    mat["r5_glow"] = True
    mat["r5_vctint"] = True          # build_mesh gives the meshes the "Col" attribute (white when they have none)
    return True


def make_vfx(mat):
    """See-through, faintly glowing surface for the materials of the vfx_* models: a mix of transparent and
    emission, weighted by the opacity texture (otherwise a low constant). The game tints them with shader
    constants that are not in the exports: the textures are grey-scale, so the glow is a light blue."""
    if mat is None or not mat.use_nodes or mat.get("r5_vfx"):
        return
    nt = mat.node_tree
    imgs = {n.label: n.image for n in nt.nodes if n.type == "TEX_IMAGE" and n.image is not None}
    nt.nodes.clear()
    N, L = nt.nodes, nt.links
    out = N.new("ShaderNodeOutputMaterial"); out.location = (700, 0)
    mix = N.new("ShaderNodeMixShader"); mix.location = (500, 0)
    tr = N.new("ShaderNodeBsdfTransparent"); tr.location = (250, 120)
    em = N.new("ShaderNodeEmission"); em.location = (250, -80)
    em.inputs["Strength"].default_value = 2.0
    tint = _mix_rgba(nt, (50, -80), "MULTIPLY")
    _sock(tint.inputs, "Factor_Float").default_value = 1.0
    _sock(tint.inputs, "B_Color").default_value = (0.55, 0.8, 1.0, 1.0)
    _sock(tint.inputs, "A_Color").default_value = (1.0, 1.0, 1.0, 1.0)
    pat = imgs.get("pattern")
    if pat is not None:
        # Energy (storm of the Olympus dome...): the grey-scale electric pattern gives both the glow and the
        # opacity: bright veins on an almost clear surface
        t = N.new("ShaderNodeTexImage"); t.image = pat; t.label = "pattern"; t.location = (-300, -80)
        boost = N.new("ShaderNodeMath"); boost.operation = "MULTIPLY_ADD"; boost.location = (-50, 80)
        boost.inputs[1].default_value = 0.85
        boost.inputs[2].default_value = 0.04
        L.new(t.outputs["Color"], boost.inputs[0])
        L.new(t.outputs["Color"], _sock(tint.inputs, "A_Color"))
        L.new(boost.outputs[0], mix.inputs["Fac"])
        em.inputs["Strength"].default_value = 4.0
        L.new(_sock(tint.outputs, "Result_Color"), em.inputs["Color"])
        mat["r5_energy"] = True
    col = None if pat is not None else (imgs.get("emissive") or imgs.get("albedo"))
    if col is not None:
        t = N.new("ShaderNodeTexImage"); t.image = col; t.label = "albedo"; t.location = (-300, -80)
        L.new(t.outputs["Color"], _sock(tint.inputs, "A_Color"))
    L.new(_sock(tint.outputs, "Result_Color"), em.inputs["Color"])
    op = imgs.get("opacity")
    if pat is not None:
        pass
    elif op is not None:
        o = N.new("ShaderNodeTexImage"); o.image = op; o.label = "opacity"; o.location = (-300, 220)
        k = N.new("ShaderNodeMath"); k.operation = "MULTIPLY"; k.location = (250, 300)
        k.inputs[1].default_value = 0.6
        L.new(o.outputs["Color"], k.inputs[0])
        L.new(k.outputs[0], mix.inputs["Fac"])
    else:
        mix.inputs["Fac"].default_value = 0.12
    L.new(tr.outputs[0], mix.inputs[1])
    L.new(em.outputs[0], mix.inputs[2])
    L.new(mix.outputs[0], out.inputs["Surface"])
    _set_render_method(mat, "BLENDED")
    mat.use_backface_culling = False
    if hasattr(mat, "use_transparency_overlap"):
        mat.use_transparency_overlap = False
    if hasattr(mat, "shadow_method"):
        try:
            mat.shadow_method = "NONE"
        except TypeError:
            pass
    if hasattr(mat, "use_transparent_shadow"):
        mat.use_transparent_shadow = True
    mat.diffuse_color = (0.55, 0.8, 1.0, 0.15)
    nt.nodes.active = mix
    mat["r5_vfx"] = True


UP_ATTR = "r5_up"


def _upward(nt, loc, lo=0.7, hi=0.95):
    """0..1: how much the surface looks up in the world (instances included)."""
    N, L = nt.nodes, nt.links
    x, y = loc
    geo = N.new("ShaderNodeNewGeometry"); geo.location = (x - 360, y)
    sep = N.new("ShaderNodeSeparateXYZ"); sep.location = (x - 180, y)
    L.new(geo.outputs["Normal"], sep.inputs[0])
    up = N.new("ShaderNodeMapRange"); up.location = (x, y)
    up.interpolation_type = "SMOOTHSTEP"
    up.inputs["From Min"].default_value = lo
    up.inputs["From Max"].default_value = hi
    L.new(sep.outputs["Z"], up.inputs["Value"])
    return up.outputs["Result"]


def _layer_weight(nt, alpha, loc):
    """Weight of layer 2: the vertex alpha, or, on meshes flagged UP_ATTR (props without vertex colours), the
    faces looking up (moss on top of the rocks instead of moss-covered rocks)."""
    N, L = nt.nodes, nt.links
    x, y = loc
    flag = N.new("ShaderNodeAttribute"); flag.attribute_name = UP_ATTR; flag.location = (x - 200, y + 200)
    mix = N.new("ShaderNodeMix"); mix.data_type = "FLOAT"; mix.location = (x + 200, y + 100)
    L.new(flag.outputs["Fac"], _sock(mix.inputs, "Factor_Float"))
    L.new(alpha, _sock(mix.inputs, "A_Float"))
    L.new(_upward(nt, (x, y + 400)), _sock(mix.inputs, "B_Float"))
    return _sock(mix.outputs, "Result_Float")


def get_material(cast, h, settings):
    cm = cast.materials.get(h)
    name = cm.name if cm else "r5_default"
    mat = bpy.data.materials.get(name)
    if mat is not None and mat.get("apx_cast") == MAT_VERSION:
        return mat
    if mat is None:
        mat = bpy.data.materials.new(name)
    elif mat.use_nodes and mat.node_tree:
        _reset_tree(mat)      # material from an older add-on version: rebuilt in place
        for k in ("r5_additive", "r5_vfx", "r5_energy", "r5_anim", "r5_lodfade", "r5_glow", "r5_vctint", "r5_water", "r5_name_color"):
            if k in mat:
                del mat[k]
    mat["apx_cast"] = MAT_VERSION
    mat["r5_blend"] = False
    group = material_group(name)
    if group:
        mat[GROUP_KEY] = group
        mat.diffuse_color = (1.0, 0.5, 0.0, 0.35)
    if cm is None or not settings.cast_load_textures:
        return mat
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return mat
    links = nt.links
    x = -1100
    y = 400

    _fill_from_library(cast, cm, name)
    if is_ice_material(name) and _build_ice(mat, nt, bsdf, cast, cm, settings):
        return mat
    # Role of each texture: file names first, then the (positional) slot names
    roles, unnamed = texture_roles(cast, cm, name)
    if is_energy_material(name) and group is None:
        for _slot, h2 in unnamed:
            img = _image(cast.texture_path(h2), False)
            if img is not None and _mask_like(img):
                roles["pattern"] = h2
                break
        if "pattern" in roles:
            tex_n = nt.nodes.new("ShaderNodeTexImage")
            tex_n.image = _image(cast.texture_path(roles["pattern"]), False)
            tex_n.label = "pattern"
            make_vfx(mat)
            return mat
    if is_glow_material(name) and group is None and ("emissive" in roles or "albedo" in roles):
        if _build_glow(mat, nt, cast, roles):
            return mat
    overlay = is_overlay(name)
    if overlay and "opacity" not in roles:
        # Decals keep their opacity in a separate grey-scale texture (opacityMultiply). With hashed file
        # names, look for it in the extra slots.
        for slot, h2 in unnamed:
            # Legion+ positional names, or r5mex's unnamed "slot<n>"
            if slot.lower() in ("emissive", "ao", "cavity") or re.fullmatch(r"slot\d+", slot.lower()):
                img = _image(cast.texture_path(h2), True)
                if img is not None and _mask_like(img):
                    roles["opacity"] = h2
                    break

    def tex(role, non_color):
        nonlocal y
        h2 = roles.get(role)
        img = _image(cast.texture_path(h2), non_color) if h2 else None
        if img is None:
            return None, None
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = img
        n.location = (x, y)
        n.label = role
        y -= 300
        return n, cast.texture_path(h2)

    alb, alb_path = tex("albedo", False)
    if alb is not None:
        nt.nodes.active = alb       # Solid > Texture shading shows the active image node
    elif "albedo" in roles:
        mat["apx_cast"] = 0         # texture unreadable right now (drive?): rebuilt on the next import
    elif not overlay and group is None and not is_glass_like(name):
        # no albedo anywhere (not in the export, not in the other exports): a colour from the name, not white
        col = name_color(name)
        bsdf.inputs["Base Color"].default_value = (*col, 1.0)
        mat.diffuse_color = (*col, 1.0)
        mat["r5_name_color"] = True
    # Blend materials (walls, ground): the last exported slot is not always the specular of layer 1
    # (bright masks -> white chrome); they are never metals anyway.
    blend = "albedo2" in roles or is_blend_material(name)
    # Vertex colour tint: the game multiplies the albedo by the model's vertex colour. It is how one grey
    # material gives a blue container, a red crate or a yellow pipe (slums_plastic_container_01,
    # crate_plastic_medium_01, imc_painted_metal_01...), not only the "_vc" materials. Meshes without vertex
    # colours get a white attribute (no change). Blend materials use them as a blend mask: not tinted.
    # Foliage keeps wind / ambient-occlusion data in its vertex colours: not tinted either.
    vctint = is_vctint(name) or (alb is not None and not blend and group not in ("fx", "particle", "tool", "trigger")
                                 and not _FOLIAGE_RE.search(name.lower()))
    mat["r5_vctint"] = bool(vctint)
    # Two layers: the vertex ALPHA is the weight of layer 2 (measured on World's Edge: where two blend
    # materials meet, alpha is 0 when they share layer 1 and 255 when they share layer 2). Without it the
    # ground showed hard borders between meshes.
    alb2 = tex("albedo2", False)[0] if blend and alb is not None and not overlay else None
    water = is_water_material(name) and group is None and alb is not None
    w1 = w2 = False
    if water:
        w1, w2 = _water_layers(name) if alb2 is not None else (True, False)
        if alb2 is not None and w1 and w2:
            alb2 = None             # "<water>_<water foam>_bm": the game draws the foam by depth, not vertex alpha
        vctint = False
        mat["r5_vctint"] = False
        mat["r5_water"] = True
    mat["r5_blend"] = alb2 is not None
    col_attr = None
    if overlay or vctint or alb2 is not None:
        col_attr = nt.nodes.new("ShaderNodeVertexColor")
        col_attr.layer_name = "Col"
        col_attr.location = (x, y + 300)
    layer_w = _layer_weight(nt, col_attr.outputs["Alpha"], (x - 300, y + 300)) if alb2 is not None else None

    def layered(a_out, b_out, loc):
        m = _mix_rgba(nt, loc, "MIX")
        links.new(layer_w, _sock(m.inputs, "Factor_Float"))
        links.new(a_out, _sock(m.inputs, "A_Color"))
        links.new(b_out, _sock(m.inputs, "B_Color"))
        return _sock(m.outputs, "Result_Color")

    spec = None if blend or water else tex("specular", False)[0]

    def water_col(node, loc):
        """Water layer colour: the deep-water tint for a placeholder albedo, else the texture darkened to the
        brightness of water (pale textures are tinted by the game's water shader)."""
        img = node.image
        if max(img.size) <= 16:
            rgb = nt.nodes.new("ShaderNodeRGB"); rgb.location = loc
            rgb.outputs[0].default_value = (*WATER_TINT, 1.0)
            return rgb.outputs[0]
        f = min(1.0, WATER_LUMA / max(_image_luma(img) or WATER_LUMA, 1e-3))
        m = _mix_rgba(nt, loc, "MULTIPLY")
        _sock(m.inputs, "Factor_Float").default_value = 1.0
        links.new(node.outputs["Color"], _sock(m.inputs, "A_Color"))
        _sock(m.inputs, "B_Color").default_value = (f, f, f, 1.0)
        return _sock(m.outputs, "Result_Color")

    base_out = alb.outputs["Color"] if alb else None
    if base_out is not None and water and w1:
        base_out = water_col(alb, (x + 150, y + 750))
    if alb2 is not None:
        b2 = water_col(alb2, (x + 150, y + 900)) if water and w2 else alb2.outputs["Color"]
        base_out = layered(base_out, b2, (x + 300, y + 600))
    if base_out is not None and vctint and col_attr is not None:
        mul = _mix_rgba(nt, (x + 300, y + 450), "MULTIPLY")
        _sock(mul.inputs, "Factor_Float").default_value = 1.0
        links.new(base_out, _sock(mul.inputs, "A_Color"))
        links.new(col_attr.outputs["Color"], _sock(mul.inputs, "B_Color"))
        base_out = _sock(mul.outputs, "Result_Color")
    if base_out is not None and spec and settings.cast_spec_to_metal:
        # Specular/gloss -> metal/roughness. Only a very bright specular (F0 > ~0.45) is a real metal;
        # below that the surface stays dielectric, otherwise rocks/concrete get iridescent reflections.
        bw = nt.nodes.new("ShaderNodeRGBToBW"); bw.location = (x + 300, y + 150)
        links.new(spec.outputs["Color"], bw.inputs[0])
        mr = nt.nodes.new("ShaderNodeMapRange"); mr.location = (x + 460, y + 150)
        mr.inputs["From Min"].default_value = 0.45
        mr.inputs["From Max"].default_value = 0.75
        links.new(bw.outputs[0], mr.inputs["Value"])
        if "Specular IOR Level" in bsdf.inputs:
            # Dielectric F0: 0.04 -> level 0.5 (Principled default)
            lv = nt.nodes.new("ShaderNodeMapRange"); lv.location = (x + 460, y - 50)
            lv.inputs["From Min"].default_value = 0.0
            lv.inputs["From Max"].default_value = 0.08
            links.new(bw.outputs[0], lv.inputs["Value"])
            links.new(lv.outputs["Result"], bsdf.inputs["Specular IOR Level"])
        mix = _mix_rgba(nt, (x + 640, y + 300), "MIX")
        links.new(mr.outputs["Result"], _sock(mix.inputs, "Factor_Float"))
        links.new(base_out, _sock(mix.inputs, "A_Color"))
        links.new(spec.outputs["Color"], _sock(mix.inputs, "B_Color"))
        links.new(_sock(mix.outputs, "Result_Color"), bsdf.inputs["Base Color"])
        links.new(mr.outputs["Result"], bsdf.inputs["Metallic"])
    elif base_out is not None:
        links.new(base_out, bsdf.inputs["Base Color"])

    # Transparency
    mode = settings.cast_alpha_mode
    op, _ = tex("opacity", True)
    if alb and mode != "NEVER":
        if overlay:
            # Overlay: albedo alpha x opacity texture x vertex color alpha, smooth blending
            def mul(a, b, dy):
                m = nt.nodes.new("ShaderNodeMath"); m.operation = "MULTIPLY"; m.location = (x + 300, y + dy)
                links.new(a, m.inputs[0]); links.new(b, m.inputs[1])
                return m.outputs[0]
            a = alb.outputs["Alpha"]
            if op is not None:
                a = mul(a, op.outputs["Color"], 750)
            if col_attr is not None:
                a = mul(a, col_attr.outputs["Alpha"], 600)
            links.new(a, bsdf.inputs["Alpha"])
            _set_render_method(mat, "BLENDED")
            mat.use_backface_culling = True
            if hasattr(mat, "use_transparency_overlap"):
                mat.use_transparency_overlap = False    # depth-sorted: no see-through on overlapping layers
            if op is not None:
                # Solid mode cannot apply the separate opacity mask: the decal would be an opaque patch
                # (white grime squares on buildings). Show it as a fully transparent viewport colour instead.
                nt.nodes.active = bsdf
                mat.diffuse_color = (0.5, 0.5, 0.5, 0.0)
        elif (mode == "ALWAYS" or _CUTOUT_RE.search(name.lower())) and _png_has_alpha(alb_path):
            # Hard cutout (alpha clip): no noise / flickering in the viewport
            clip = nt.nodes.new("ShaderNodeMath"); clip.operation = "GREATER_THAN"; clip.location = (x + 300, y + 600)
            clip.inputs[1].default_value = 0.5
            links.new(alb.outputs["Alpha"], clip.inputs[0])
            links.new(clip.outputs[0], bsdf.inputs["Alpha"])
            _set_render_method(mat, "DITHERED")

    nrm, _ = tex("normal", True)
    if nrm:
        n_out = nrm.outputs["Color"]
        nrm2 = tex("normal2", True)[0] if alb2 is not None else None
        if nrm2 is not None:
            n_out = layered(n_out, nrm2.outputs["Color"], (x + 150, y + 450))
        _normal_chain(nt, n_out, bsdf, settings.cast_flip_normal_green, (x + 300, y + 300))

    gl, _ = tex("gloss", True)
    if gl:
        g_out = gl.outputs["Color"]
        gl2 = tex("gloss2", True)[0] if alb2 is not None else None
        if gl2 is not None:
            g_out = layered(g_out, gl2.outputs["Color"], (x + 150, y + 450))
        inv = nt.nodes.new("ShaderNodeInvert")
        inv.location = (x + 300, y + 300)
        links.new(g_out, inv.inputs["Color"])
        links.new(inv.outputs["Color"], bsdf.inputs["Roughness"])
    else:
        rough, _ = tex("roughness", True)
        if rough:
            links.new(rough.outputs["Color"], bsdf.inputs["Roughness"])
    if water and w1 and (alb2 is None or w2):
        # still water: a glossy surface (the gloss maps of the water shaders are flat and pale). Not a perfect
        # mirror: at grazing angles it would reflect the bright sky / studio light as white water
        for lk in list(bsdf.inputs["Roughness"].links):
            links.remove(lk)
        bsdf.inputs["Roughness"].default_value = 0.1

    em, _ = tex("emissive", False)
    if em:
        key = "Emission Color" if "Emission Color" in bsdf.inputs else "Emission"
        e_out = em.outputs["Color"]
        if vctint and col_attr is not None:
            # the vertex colour tints the glow too: lamps, neon tubes and signs use one white emissive texture
            # coloured per vertex in game (otherwise every light of the map glows the same white)
            et = _mix_rgba(nt, (x + 300, y - 150), "MULTIPLY")
            _sock(et.inputs, "Factor_Float").default_value = 1.0
            links.new(e_out, _sock(et.inputs, "A_Color"))
            links.new(col_attr.outputs["Color"], _sock(et.inputs, "B_Color"))
            e_out = _sock(et.outputs, "Result_Color")
        links.new(e_out, bsdf.inputs[key])
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = 1.0

    if op and mode != "NEVER" and not overlay:
        links.new(op.outputs["Color"], bsdf.inputs["Alpha"])
        _set_render_method(mat, "DITHERED")

    if settings.cast_extra_textures:
        for role in roles:
            if role not in ("albedo", "normal", "gloss", "emissive", "opacity", "specular", "roughness"):
                tex(role, True)
    if group == "fx":
        make_additive(mat)
    elif group == "particle":
        make_additive(mat, 0.6)     # shown on demand: a soft glowing mist instead of an opaque grey sheet
    return mat


# --------------------------------------------------------------------------
# Ice
# --------------------------------------------------------------------------
# "desertlands_ice_03_wld_dark_to_desertlands_snow_04_wld_bm" (ice spikes of Ground Zero, icicles...): the
# albedo is a flat dark grey, the look of the ice comes from the game's ice shader. The material also holds
# the ice colour (bluish cloudy texture, last slot), the ice normal map and a gloss mask: rebuilt as ice.
_ICE_RE = re.compile(r"^(?:[a-z]+_)?ice_\d+")
_ICE_COLOR = (0.66, 0.80, 0.90)


def is_ice_material(name):
    low = name.lower()
    return _ICE_RE.search(low) is not None and not is_overlay(low)


def _set_input(node, names, value):
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return True
    return False


def _img_stats(img, size=32):
    """(mean RGB, luminance std) of an image, on a small copy."""
    if img is None:
        return None
    small = None
    try:
        if not img.size[0]:
            return None
        small = img.copy()
        small.scale(size, size)
        px = np.empty(size * size * 4, np.float32)
        small.pixels.foreach_get(px)
        rgb = px.reshape(-1, 4)[:, :3]
        return rgb.mean(0), float((rgb @ np.array([0.3, 0.59, 0.11], np.float32)).std())
    except (RuntimeError, ValueError, AttributeError):
        return None
    finally:
        if small is not None:
            try:
                bpy.data.images.remove(small)
            except (RuntimeError, ReferenceError):
                pass


def _ice_detail(cast, cm, roles):
    """The ice colour texture: among the spec texture and the unnamed extra textures of the ice shader, the
    blue-ish one with the most detail (desertlands_ice_03_wld_bright...: blue ice with cracks, slot 8; the spec
    texture is only a cloudy tint, which gave a flat colour)."""
    cands = [roles["specular"]] if roles.get("specular") else []
    cands += [h for s, h in cm.slots.items() if re.fullmatch(r"slot\d+", s.lower())]
    best, score = None, -1.0
    for h in cands:
        st = _img_stats(_image(cast.texture_path(h), False))
        if st is None:
            continue
        mean, std = st
        if mean[2] - mean[0] < 0.04 or mean.mean() > 0.75:      # not bluish, or a white snow map
            continue
        if std > score:
            best, score = h, std
    return best


def _build_ice(mat, nt, bsdf, cast, cm, settings):
    """Ice shader (+ its snow layer 2). Returns False when the textures are not the expected ones."""
    slots = list(cm.slots.items())
    alb2_h = normal2_h = None
    if any(role_from_filename(cast.texture_path(h2)) for _s, h2 in slots):
        roles = texture_roles(cast, cm, mat.name)[0]
        tint_h = _ice_detail(cast, cm, roles) or roles.get("specular")
        normal_h, gloss_h = roles.get("normal"), roles.get("gloss")
        alb2_h, normal2_h = roles.get("albedo2"), roles.get("normal2")
    elif len(slots) >= 7:
        # two-layer layout [albedo, albedo2, normal, normal2, gloss, gloss2, ice colour]
        tint_h, normal_h, gloss_h = slots[6][1], slots[2][1], slots[4][1]
    else:
        return False
    tint_img = _image(cast.texture_path(tint_h), False) if tint_h else None
    if tint_img is None:
        return False
    N, L = nt.nodes, nt.links
    x, y = -1100, 300
    tint = N.new("ShaderNodeTexImage"); tint.image = tint_img; tint.label = "albedo"; tint.location = (x, y)
    # blue-grey ice texture (mean ~0.35/0.44/0.51) -> brighter, then pulled a little towards a light ice blue
    hsv = N.new("ShaderNodeHueSaturation"); hsv.location = (x + 300, y)
    hsv.inputs["Value"].default_value = 1.5
    L.new(tint.outputs["Color"], hsv.inputs["Color"])
    mix = N.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.location = (x + 500, y)
    _sock(mix.inputs, "Factor_Float").default_value = 0.2
    L.new(hsv.outputs["Color"], _sock(mix.inputs, "A_Color"))
    _sock(mix.inputs, "B_Color").default_value = (*_ICE_COLOR, 1.0)
    color_out = _sock(mix.outputs, "Result_Color")
    nt.nodes.active = tint

    # Layer 2 (snow) where the vertex alpha says so, like the other two-layer materials
    snow_img = _image(cast.texture_path(alb2_h), False) if alb2_h else None
    w = None
    if snow_img is not None:
        col = N.new("ShaderNodeVertexColor"); col.layer_name = "Col"; col.location = (x, y + 300)
        w = _layer_weight(nt, col.outputs["Alpha"], (x - 300, y + 300))
        if "zup" in mat.name.lower():
            # "..._zup_bm": the snow only settles on faces looking up (the ice walls of the spikes stay ice,
            # their vertex alpha alone is 255)
            wm = N.new("ShaderNodeMath"); wm.operation = "MULTIPLY"; wm.location = (x + 240, y + 450)
            L.new(w, wm.inputs[0]); L.new(_upward(nt, (x + 60, y + 900), 0.5, 0.85), wm.inputs[1])
            w = wm.outputs[0]
        s = N.new("ShaderNodeTexImage"); s.image = snow_img; s.label = "albedo2"; s.location = (x, y + 600)
        m2 = _mix_rgba(nt, (x + 700, y), "MIX")
        L.new(w, _sock(m2.inputs, "Factor_Float"))
        L.new(color_out, _sock(m2.inputs, "A_Color"))
        L.new(s.outputs["Color"], _sock(m2.inputs, "B_Color"))
        color_out = _sock(m2.outputs, "Result_Color")
        mat["r5_blend"] = True
    L.new(color_out, bsdf.inputs["Base Color"])

    rough_out = None
    gloss_img = _image(cast.texture_path(gloss_h), True) if gloss_h else None
    if gloss_img is not None:
        g = N.new("ShaderNodeTexImage"); g.image = gloss_img; g.label = "gloss"; g.location = (x, y - 300)
        mr = N.new("ShaderNodeMapRange"); mr.location = (x + 300, y - 300)
        mr.inputs["To Min"].default_value = 0.35    # gloss 0 (cracks)
        mr.inputs["To Max"].default_value = 0.04    # gloss 1 (smooth ice)
        L.new(g.outputs["Color"], mr.inputs["Value"])
        rough_out = mr.outputs["Result"]
    if w is not None:
        rm = N.new("ShaderNodeMix"); rm.data_type = "FLOAT"; rm.location = (x + 500, y - 300)
        L.new(w, _sock(rm.inputs, "Factor_Float"))
        if rough_out is not None:
            L.new(rough_out, _sock(rm.inputs, "A_Float"))
        else:
            _sock(rm.inputs, "A_Float").default_value = 0.08
        _sock(rm.inputs, "B_Float").default_value = 0.7       # snow
        rough_out = _sock(rm.outputs, "Result_Float")
    if rough_out is not None:
        L.new(rough_out, bsdf.inputs["Roughness"])
    else:
        bsdf.inputs["Roughness"].default_value = 0.08

    normal_img = _image(cast.texture_path(normal_h), True) if normal_h else None
    if normal_img is not None:
        n = N.new("ShaderNodeTexImage"); n.image = normal_img; n.label = "normal"; n.location = (x, y - 600)
        n_out = n.outputs["Color"]
        normal2_img = _image(cast.texture_path(normal2_h), True) if (normal2_h and w is not None) else None
        if normal2_img is not None:
            n2 = N.new("ShaderNodeTexImage"); n2.image = normal2_img; n2.label = "normal2"; n2.location = (x, y - 900)
            nm = _mix_rgba(nt, (x + 150, y - 750), "MIX")
            L.new(w, _sock(nm.inputs, "Factor_Float"))
            L.new(n_out, _sock(nm.inputs, "A_Color"))
            L.new(n2.outputs["Color"], _sock(nm.inputs, "B_Color"))
            n_out = _sock(nm.outputs, "Result_Color")
        _normal_chain(nt, n_out, bsdf, settings.cast_flip_normal_green, (x + 300, y - 600))

    bsdf.inputs["Metallic"].default_value = 0.0
    _set_input(bsdf, ("IOR",), 1.31)
    _set_input(bsdf, ("Specular IOR Level",), 0.6)
    _set_input(bsdf, ("Subsurface Weight", "Subsurface"), 0.3)
    _set_input(bsdf, ("Subsurface Radius",), (0.35, 0.65, 1.0))
    _set_input(bsdf, ("Subsurface Scale",), 0.08)
    _set_input(bsdf, ("Coat Weight",), 0.6)
    _set_input(bsdf, ("Coat Roughness",), 0.03)
    mat.diffuse_color = (*_ICE_COLOR, 1.0)
    mat["r5_ice"] = True
    return True


def _normal_chain(nt, img_node, bsdf, flip_green, loc):
    """Apex normal maps: RG channels only (BC5), B rebuilt, DirectX green optionally flipped."""
    N, L = nt.nodes, nt.links
    x, y = loc
    sep = N.new("ShaderNodeSeparateColor"); sep.location = (x, y)
    # an image node, or a colour socket (two blended normal maps)
    L.new(img_node.outputs["Color"] if hasattr(img_node, "outputs") else img_node, sep.inputs[0])
    g = sep.outputs[1]
    if flip_green:
        inv = N.new("ShaderNodeMath"); inv.operation = "SUBTRACT"; inv.location = (x + 160, y - 60)
        inv.inputs[0].default_value = 1.0
        L.new(g, inv.inputs[1])
        g = inv.outputs[0]

    def ma(a, b, c, loc2):
        n = N.new("ShaderNodeMath"); n.operation = "MULTIPLY_ADD"; n.location = loc2
        for i, v in enumerate((a, b, c)):
            if isinstance(v, float):
                n.inputs[i].default_value = v
            else:
                L.new(v, n.inputs[i])
        return n.outputs[0]
    nx = ma(sep.outputs[0], 2.0, -1.0, (x + 320, y + 60))
    ny = ma(g, 2.0, -1.0, (x + 320, y - 80))
    cmb = N.new("ShaderNodeCombineXYZ"); cmb.location = (x + 400, y - 220)
    L.new(nx, cmb.inputs[0]); L.new(ny, cmb.inputs[1])
    dot = N.new("ShaderNodeVectorMath"); dot.operation = "DOT_PRODUCT"; dot.location = (x + 480, y)
    L.new(cmb.outputs[0], dot.inputs[0]); L.new(cmb.outputs[0], dot.inputs[1])
    one_minus = N.new("ShaderNodeMath"); one_minus.operation = "SUBTRACT"; one_minus.location = (x + 640, y)
    one_minus.inputs[0].default_value = 1.0
    one_minus.use_clamp = True
    L.new(dot.outputs["Value"], one_minus.inputs[1])
    sq = N.new("ShaderNodeMath"); sq.operation = "SQRT"; sq.location = (x + 800, y)
    L.new(one_minus.outputs[0], sq.inputs[0])
    b = ma(sq.outputs[0], 0.5, 0.5, (x + 960, y))
    comb = N.new("ShaderNodeCombineColor"); comb.location = (x + 1120, y)
    L.new(sep.outputs[0], comb.inputs[0]); L.new(g, comb.inputs[1]); L.new(b, comb.inputs[2])
    nmap = N.new("ShaderNodeNormalMap"); nmap.location = (x + 1280, y)
    L.new(comb.outputs[0], nmap.inputs["Color"])
    L.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])


# --------------------------------------------------------------------------
# Mesh building
# --------------------------------------------------------------------------
def detect_flip(cast, sample=4000):
    """Compare the vertex order with the stored normals to pick the face winding."""
    score, n = 0.0, 0
    for m in cast.meshes:
        if m.nrm is None:
            continue
        f = m.faces[: (len(m.faces) // 3) * 3].reshape(-1, 3)[:sample]
        p = m.pos.astype(np.float64)
        gn = np.cross(p[f[:, 1]] - p[f[:, 0]], p[f[:, 2]] - p[f[:, 0]])
        vn = m.nrm[f[:, 0]] + m.nrm[f[:, 1]] + m.nrm[f[:, 2]]
        score += float(np.sign((gn * vn).sum(1)).sum())
        n += len(f)
        if n >= sample:
            break
    return score < 0


def _alpha_inverted(pos, f, alpha):
    """Terrain blends ("_b2t", "_bt" decals) store the TERRAIN weight in the vertex alpha: 255 on the open border
    of the patch (only terrain), lower inside. Used as opacity they ended in hard-edged patches. True when the
    border vertices are clearly more opaque than the inside ones (measured on World's Edge: border median 255,
    inside ~150 for volcanic_road_tracks_b2t; normal decals have the opposite)."""
    if len(alpha) < 16 or len(f) < 4:
        return False
    key = np.round(pos.astype(np.float64) * 8).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    inv = inv.ravel()
    ff = inv[f]
    e = np.sort(np.concatenate([ff[:, [0, 1]], ff[:, [1, 2]], ff[:, [2, 0]]]), axis=1)
    eu, cnt = np.unique(e, axis=0, return_counts=True)
    border = np.zeros(len(uniq), bool)
    border[eu[cnt == 1].ravel()] = True
    vb = border[inv]
    if vb.sum() < 8 or (~vb).sum() < 8:
        return False
    b, i = float(np.median(alpha[vb])), float(np.median(alpha[~vb]))
    return b >= 200 and b >= i + 25


def build_mesh(name, cast, meshes, xf, settings, flip, mat_cache, origin=None):
    """Merge `meshes` (CastMesh) into one Blender mesh. xf: 3x3 matrix (scale + rotation)."""
    pos_l, nrm_l, uv_l, col_l, tri_l, mat_l, up_l = [], [], [], [], [], [], []
    slots, slot_of = [], {}
    need_col = need_up = False
    base = 0
    off = settings.decal_offset
    for m in meshes:
        f = m.faces[: (len(m.faces) // 3) * 3].reshape(-1, 3)
        if flip:
            f = f[:, [0, 2, 1]]
        nv = len(m.pos)
        if len(f) == 0 or f.max() >= nv:
            continue
        mname = cast.mat_name(m.mat)
        si = slot_of.get(m.mat)
        if si is None:
            si = slot_of[m.mat] = len(slots)
            slots.append(m.mat)
            mat = mat_cache.get(m.mat)
            if mat is None:
                mat = mat_cache[m.mat] = get_material(cast, m.mat, settings)
            # the material decides whether it reads the vertex colours (tint / decal alpha): then every
            # mesh using it needs the attribute, white when the mesh has none (a missing attribute is black)
            if is_overlay(mname) or is_vctint(mname) or (mat is not None and (mat.get("r5_vctint")
                                                                               or mat.get("r5_blend"))):
                need_col = True
        has_n = m.nrm is not None and len(m.nrm) == nv
        pos = m.pos
        if off > 0 and has_n and is_overlay(mname):
            # Push the overlay above the surface -> no z-fighting
            k = 1.0 + (m.mat % 4) * 0.25
            pos = pos.astype(np.float64) + m.nrm.astype(np.float64) * (off * k)
        pos_l.append(pos)
        nrm_l.append(m.nrm[f.ravel()] if has_n else np.zeros((f.size, 3), np.float32))
        uv_l.append(m.uv[f.ravel()] if m.uv is not None and len(m.uv) == nv else
                    np.zeros((f.size, 2), np.float32))
        # blend material: the alpha is the layer weight, 0 (layer 1 only) is a real value
        blend_m = bool(mat_cache.get(m.mat) is not None and mat_cache[m.mat].get("r5_blend"))
        if m.vc is not None:
            vc = m.vc.view(np.uint8).reshape(-1, 4)
            fix_a = not blend_m and vc[:, 3].max() == 0
            if fix_a or vc[:, :3].max() < 10:
                # Unused data for this mesh (alpha all 0 / black RGB): otherwise the decal becomes
                # invisible or the tint black in Rendered mode
                vc = vc.copy()
                if fix_a:
                    vc[:, 3] = 255
                if vc[:, :3].max() < 10:
                    vc[:, :3] = 255
            if is_overlay(mname) and _alpha_inverted(m.pos, f, vc[:, 3]):
                vc = vc.copy()
                vc[:, 3] = 255 - vc[:, 3]
            if blend_m and vc[:, 3].min() == 255:
                # Prop without vertex colours (the exporter writes white): the layer weight would be 1 everywhere,
                # i.e. moss-covered rocks (icelandic_rock_cluster_01_moss_01_wld_bm). Flagged: the material puts
                # layer 2 (moss, snow...) on the faces looking up in the world instead.
                up_l.append(np.ones(f.size, np.float32))
                need_up = True
            else:
                up_l.append(np.zeros(f.size, np.float32))
            col_l.append(vc[f.ravel()])
        else:
            col_l.append(np.full((f.size, 4), (255, 255, 255, 0) if blend_m else 255, np.uint8))
            up_l.append(np.zeros(f.size, np.float32))
        tri_l.append(f + base)
        mat_l.append(np.full(len(f), si, np.int32))
        base += nv
    if not pos_l:
        return None

    pos = np.concatenate(pos_l).astype(np.float64) @ xf.T
    if origin is not None:
        pos -= origin
    tris = np.concatenate(tri_l)
    lnrm = np.concatenate(nrm_l).astype(np.float64) @ xf.T
    ln = np.linalg.norm(lnrm, axis=1, keepdims=True)
    lnrm = np.divide(lnrm, ln, out=np.zeros_like(lnrm), where=ln > 0)
    luv = np.concatenate(uv_l).astype(np.float32)
    luv[:, 1] = 1.0 - luv[:, 1]
    lcol = np.concatenate(col_l) if need_col else None
    lup = np.concatenate(up_l) if need_up else None
    fmat = np.concatenate(mat_l)

    # Weld vertices (map exports are not indexed)
    if settings.cast_weld > 0:
        unit = max(abs(float(np.linalg.det(xf))) ** (1.0 / 3.0), 1e-9)     # scale of xf (also when rotated)
        key = np.round(pos / (settings.cast_weld * unit)).astype(np.int64)
        _u, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
        pos = pos[first]
        tris = inv.reshape(-1)[tris]
    good = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    if not good.all():
        keep_loops = np.repeat(good, 3)
        tris, fmat = tris[good], fmat[good]
        lnrm, luv = lnrm[keep_loops], luv[keep_loops]
        if lcol is not None:
            lcol = lcol[keep_loops]
        if lup is not None:
            lup = lup[keep_loops]
    if len(tris) == 0:
        return None
    used = np.unique(tris)
    if len(used) != len(pos):
        remap = np.full(len(pos), -1, np.int64)
        remap[used] = np.arange(len(used))
        pos = pos[used]
        tris = remap[tris]

    nt, nl = len(tris), tris.size
    me = bpy.data.meshes.new(name)
    me.vertices.add(len(pos))
    me.vertices.foreach_set("co", pos.astype(np.float32).ravel())
    me.loops.add(nl)
    me.loops.foreach_set("vertex_index", tris.astype(np.int32).ravel())
    me.polygons.add(nt)
    me.polygons.foreach_set("loop_start", np.arange(0, nl, 3, dtype=np.int32))
    me.polygons.foreach_set("material_index", fmat.astype(np.int32))
    uvl = me.uv_layers.new(name="UVMap")
    uvl.data.foreach_set("uv", luv.ravel())
    if lcol is not None:
        ca = me.color_attributes.new("Col", "BYTE_COLOR", "CORNER")
        cf = lcol.astype(np.float32) / 255.0
        try:
            ca.data.foreach_set("color_srgb", cf.ravel())
        except (AttributeError, TypeError):
            ca.data.foreach_set("color", cf.ravel())
    if lup is not None:
        ua = me.attributes.new(UP_ATTR, "FLOAT", "CORNER")
        ua.data.foreach_set("value", lup)
    me.update(calc_edges=True)

    if settings.cast_custom_normals and len(me.loops) == nl:
        me.polygons.foreach_set("use_smooth", np.ones(nt, dtype=bool))
        me.normals_split_custom_set(lnrm.astype(np.float32))
    elif settings.cast_smooth_by_angle:
        me.polygons.foreach_set("use_smooth", np.ones(nt, dtype=bool))

    for h in slots:
        me.materials.append(mat_cache[h])
    return me


def _split_by_group(cast, meshes):
    """{group|None: [meshes]} based on the material name."""
    out = {}
    for m in meshes:
        out.setdefault(material_group(cast.mat_name(m.mat)), []).append(m)
    return out


_LOD_NAME_RE = re.compile(r"lod[_\-]?(\d+)", re.IGNORECASE)


def import_model_cast(path, settings, name=None):
    """Import a model (prop). Returns objects named "<name>_LOD<i>[_<group>]",
    in game units (the library applies the scale afterwards)."""
    cast = CastFile(path)
    flip = detect_flip(cast)
    stem = name or os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r"[_\-\. ]?lod[_\-]?\d+$", "", stem, flags=re.IGNORECASE)
    file_lod = _LOD_NAME_RE.search(os.path.splitext(os.path.basename(path))[0][-8:])

    # One modl node per LOD (RSX); otherwise everything belongs to the file's LOD
    by_lod = {}
    for m in cast.meshes:
        lod = None
        if len(cast.models) > 1 and m.modl < len(cast.models):
            mm = _LOD_NAME_RE.search(cast.models[m.modl])
            lod = int(mm.group(1)) if mm else m.modl
        if lod is None:
            lod = int(file_lod.group(1)) if file_lod else 0
        by_lod.setdefault(lod, []).append(m)

    objs = []
    mat_cache = {}
    xf = Y_UP_TO_Z_UP if cast.y_up else np.eye(3)
    for lod, ms in sorted(by_lod.items()):
        for group, gms in _split_by_group(cast, ms).items():
            oname = f"{stem}_LOD{lod}" + (f"_{group}" if group else "")
            me = build_mesh(oname, cast, gms, xf, settings, flip, mat_cache)
            if me is None:
                continue
            obj = bpy.data.objects.new(oname, me)
            if group:
                obj[GROUP_KEY] = group
            bpy.context.scene.collection.objects.link(obj)
            objs.append(obj)
    cast.free()
    return objs


_BRUSH_MESH_RE = re.compile(r"^model(\d+)_")


def place_brush_models(cast, brush_origins):
    """BSP brush models (meshes "model<N>_<material>": breakable glass, func_brush signs, fog volumes, triggers)
    are exported around the origin; the entity with "model" "*N" gives their position. Their vertices keep
    the world orientation (measured: the panes of breachable_glass already face their yaw), so they are only
    moved. Returns (moved, without entity)."""
    moved, orphan = 0, 0
    for m in cast.meshes:
        g = _BRUSH_MESH_RE.match(m.name)
        if not g:
            continue
        o = (brush_origins or {}).get(int(g.group(1)))
        if o is None:
            orphan += 1
            continue
        m.pos = m.pos + np.asarray(o, dtype=np.float32)
        moved += 1
    return moved, orphan


def world_z_samples(cast, xf, brush_origins=None):
    """Heights of the visible map geometry vertices (Blender units, before the offset): tool / trigger / fx /
    dev faces and brush models without an entity are left out."""
    out = []
    for m in cast.meshes:
        if material_group(cast.mat_name(m.mat)):
            continue
        g = _BRUSH_MESH_RE.match(m.name)
        if g and int(g.group(1)) not in (brush_origins or {}):
            continue
        out.append((m.pos.astype(np.float64) @ xf[2])[:].astype(np.float32))
    return np.concatenate(out) if out else np.zeros(0, np.float32)


def crop_meshes(cast, area):
    """Keep only the geometry inside a vertical cylinder: area = (x, y, radius), game units. A triangle is kept
    when one of its vertices is inside, so the edge of the area is not cut through the triangles."""
    cx, cy, r = area
    r2 = float(r) * float(r)
    keep = []
    for m in cast.meshes:
        nv = len(m.pos)
        f = m.faces[: (len(m.faces) // 3) * 3].reshape(-1, 3)
        if len(f) == 0 or f.max() >= nv:
            continue
        p = m.pos.astype(np.float64)
        inside = (p[:, 0] - cx) ** 2 + (p[:, 1] - cy) ** 2 <= r2
        if inside.all():
            keep.append(m)
            continue
        tri = inside[f].any(axis=1)
        if not tri.any():
            continue
        f = f[tri]
        used = np.unique(f)
        remap = np.full(nv, -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        m.faces = remap[f].ravel()
        m.pos = m.pos[used]
        for attr in ("nrm", "uv", "vc"):
            a = getattr(m, attr)
            if a is not None and len(a) == nv:
                setattr(m, attr, a[used])
        keep.append(m)
    cast.meshes = keep
    return len(keep)


def import_world_cast(path, settings, collection, xf, offset, report=print, cast=None, brush_origins=None,
                      separate=None, area=None):
    """Import a map's geometry, optionally split into square tiles.

    xf: 3x3 (game units -> Blender), offset: shift in meters removed after the transform.
    Trigger / tool / dev faces go to separate sub-collections.
    cast: an already read CastFile of `path` (optional). brush_origins: {N: origin} of the brush entities.
    separate: brush models N that get their own object (moved by a rotator, see motion.py), tagged
    "apx_rotor_brush".
    area: (x, y, radius) in game units: only the geometry of that area is imported (see crop_meshes).
    """
    if cast is None:
        cast = CastFile(path)
        place_brush_models(cast, brush_origins)
    flip = detect_flip(cast)          # (on the whole map: a small area may hold too few faces to tell)
    if area is not None:
        crop_meshes(cast, area)
    split = settings.world_split
    tile = settings.world_tile_size if split == "TILES" else 0.0
    groups = {}
    separate = set(separate or ())
    for m in cast.meshes:
        progress.keep_alive()
        g = material_group(cast.mat_name(m.mat))
        bm = _BRUSH_MESH_RE.match(m.name)
        if bm and int(bm.group(1)) in separate and not g:
            groups.setdefault(("", ("brush", int(bm.group(1)))), []).append(m)
            continue
        if split == "MATERIAL":
            key = (0, m.mat)
        else:
            c = (m.pos.mean(axis=0).astype(np.float64) @ xf.T) - offset
            key = (int(np.floor(c[0] / tile)), int(np.floor(c[1] / tile))) if tile > 0 else (0, 0)
        groups.setdefault((g or "", key), []).append(m)

    base = os.path.splitext(os.path.basename(path))[0]
    mat_cache = {}
    objs = []
    sub = {}
    wm = bpy.context.window_manager
    wm.progress_begin(0, len(groups))
    for i, ((g, key), ms) in enumerate(sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1])))):
        wm.progress_update(i)
        progress.update(i / max(len(groups), 1))
        rotor = key[0] == "brush"
        center = np.array([(key[0] + 0.5) * tile, (key[1] + 0.5) * tile, 0.0]) if tile > 0 and not rotor \
            else np.zeros(3)
        if rotor:
            oname = f"{base}_mover_{key[1]}"
        elif split == "MATERIAL":
            oname = f"{base}_{cast.mat_name(key[1])}"
        else:
            oname = f"{base}_{g + '_' if g else ''}tile_{key[0]}_{key[1]}"
        me = build_mesh(oname, cast, ms, xf, settings, flip, mat_cache, origin=offset + center)
        if me is None:
            continue
        o = bpy.data.objects.new(me.name, me)
        o.location = center
        o["apx_world_tile"] = True
        if rotor:
            o["apx_rotor_brush"] = key[1]
        if g:
            o[GROUP_KEY] = g
            col = sub.get(g)
            if col is None:
                col = sub[g] = bpy.data.collections.new(f"{base}_{g}")
                collection.children.link(col)
            col.objects.link(o)
        else:
            collection.objects.link(o)
        objs.append(o)
    wm.progress_end()
    nmesh = len(cast.meshes)
    cast.free()
    report(f"Geometry: {nmesh} meshes -> {len(objs)} objects, {len(mat_cache)} materials")
    return objs
