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


T_ROOT, T_MODL, T_MESH, T_MATL, T_FILE = (_tag(t) for t in ("root", "modl", "mesh", "matl", "file"))

# Custom property names (kept from older versions so existing .blend files keep working)
GROUP_KEY = "apx_group"

# --------------------------------------------------------------------------
# Material / model classification by name
# --------------------------------------------------------------------------
GROUPS = (
    ("trigger", "Triggers"),
    ("tool", "Tool brushes (clip, blocklight, skybox...)"),
    ("fx", "Light shafts & atmosphere volumes"),
    ("gamemode", "Game mode barriers"),
    ("dev", "Dev textures / placeholders"),
)
_GROUP_RE = (
    ("trigger", re.compile(r"(^|_)tools?_?trigger|trigger|fogvolume|^tools_?fog")),
    ("tool", re.compile(r"^tools|^tool_|(^|_)(nodraw|playerclip|npcclip|clip|invisible|skybox|blocklight|"
                        r"occluder|areaportal|hint|skip|caulk|origin_brush)($|_)")),
    ("fx", re.compile(r"godray|god_ray|lightshaft|light_shaft|volumetric|light_beam|lightbeam|sunbeam|sun_beam|"
                      r"light_cone|lightcone|(^|_)atmosphere(_|$)|(^|_)haze(_|$)|dust_motes")),
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
_CUTOUT_RE = re.compile(r"(?:^|[_\-\s\d])(?:leaf|leaves|foliage|grass|fern|bush|shrub|plant|tree|branch|ivy|vine|"
                        r"moss_clump|flower|weed|fence|chainlink|chain_link|grate|grating|net|alpha|cutout|hair|"
                        r"fx|glass)")

MAT_VERSION = 6


def material_group(name):
    low = name.lower()
    for g, rx in _GROUP_RE:
        if rx.search(low):
            return g
    return None


def is_fx_model(key):
    return FX_MODEL_RE.search(key.lower()) is not None


def is_overlay(name):
    return _OVERLAY_RE.search(name.lower()) is not None


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
            if os.path.isdir(cand):
                idx = {f.lower(): os.path.join(cand, f) for f in os.listdir(cand)}
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
            with open(path, "rb") as f:
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

    def texture_path(self, h):
        p = self.files.get(h)
        if not p:
            return None
        p = p.replace("\\", os.sep).replace("/", os.sep)
        p = p if os.path.isabs(p) else os.path.join(self.dir, p)
        if os.path.isfile(p):
            return p
        # RSX: the reference may have no extension, or the export may be .dds/.tga
        base = os.path.splitext(p)[0]
        for ext in (".png", ".dds", ".tga", ".PNG", ".DDS"):
            if os.path.isfile(base + ext):
                return base + ext
            if os.path.isfile(p + ext):
                return p + ext
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
        with open(path, "rb") as f:
            head = f.read(33)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return True   # dds/tga: assume there may be alpha
        return head[25] in (4, 6)
    except OSError:
        return False


_img_cache = {}


def clear_image_cache():
    _img_cache.clear()


def _image(path, non_color):
    """Load an image only once, even if it is copied into several folders."""
    if not path or not os.path.isfile(path):
        return None
    try:
        key = f"{os.path.basename(path).lower()}|{os.path.getsize(path)}"
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
        img = bpy.data.images.load(path, check_existing=True)
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
        if "r5_additive" in mat:
            del mat["r5_additive"]
    mat["apx_cast"] = MAT_VERSION
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

    # Role of each texture: file name first (Legion+ puts the AO in "emissive"), then slot name
    roles = {}
    for slot, h2 in cm.slots.items():
        p = cast.texture_path(h2)
        role = role_from_filename(p)
        if role is None:
            role = _SLOT.get(slot.lower().replace("texture", ""), slot.lower())
            if role == "emissive" and not _looks_emissive(name):
                role = "emissive?"
        roles.setdefault(role, h2)

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

    overlay = is_overlay(name)
    vctint = is_vctint(name)
    col_attr = None
    if overlay or vctint:
        col_attr = nt.nodes.new("ShaderNodeVertexColor")
        col_attr.layer_name = "Col"
        col_attr.location = (x, y + 300)

    alb, alb_path = tex("albedo", False)
    if alb is not None:
        nt.nodes.active = alb       # Solid > Texture shading shows the active image node
    spec, _ = tex("specular", False)
    base_out = alb.outputs["Color"] if alb else None
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
    if alb and mode != "NEVER":
        if overlay:
            # Overlay: texture alpha x vertex color alpha, smooth blending
            a = alb.outputs["Alpha"]
            if col_attr is not None:
                m = nt.nodes.new("ShaderNodeMath"); m.operation = "MULTIPLY"; m.location = (x + 300, y + 600)
                links.new(a, m.inputs[0]); links.new(col_attr.outputs["Alpha"], m.inputs[1])
                a = m.outputs[0]
            links.new(a, bsdf.inputs["Alpha"])
            _set_render_method(mat, "BLENDED")
            mat.use_backface_culling = True
        elif (mode == "ALWAYS" or _CUTOUT_RE.search(name.lower())) and _png_has_alpha(alb_path):
            # Hard cutout (alpha clip): no noise / flickering in the viewport
            clip = nt.nodes.new("ShaderNodeMath"); clip.operation = "GREATER_THAN"; clip.location = (x + 300, y + 600)
            clip.inputs[1].default_value = 0.5
            links.new(alb.outputs["Alpha"], clip.inputs[0])
            links.new(clip.outputs[0], bsdf.inputs["Alpha"])
            _set_render_method(mat, "DITHERED")

    nrm, _ = tex("normal", True)
    if nrm:
        _normal_chain(nt, nrm, bsdf, settings.cast_flip_normal_green, (x + 300, y + 300))

    gl, _ = tex("gloss", True)
    if gl:
        inv = nt.nodes.new("ShaderNodeInvert")
        inv.location = (x + 300, y + 300)
        links.new(gl.outputs["Color"], inv.inputs["Color"])
        links.new(inv.outputs["Color"], bsdf.inputs["Roughness"])
    else:
        rough, _ = tex("roughness", True)
        if rough:
            links.new(rough.outputs["Color"], bsdf.inputs["Roughness"])

    em, _ = tex("emissive", False)
    if em:
        key = "Emission Color" if "Emission Color" in bsdf.inputs else "Emission"
        links.new(em.outputs["Color"], bsdf.inputs[key])
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = 1.0

    op, _ = tex("opacity", True)
    if op and mode != "NEVER" and not overlay:
        links.new(op.outputs["Color"], bsdf.inputs["Alpha"])
        _set_render_method(mat, "DITHERED")

    if settings.cast_extra_textures:
        for role in roles:
            if role not in ("albedo", "normal", "gloss", "emissive", "opacity", "specular", "roughness"):
                tex(role, True)
    if group == "fx":
        make_additive(mat)
    return mat


def _normal_chain(nt, img_node, bsdf, flip_green, loc):
    """Apex normal maps: RG channels only (BC5), B rebuilt, DirectX green optionally flipped."""
    N, L = nt.nodes, nt.links
    x, y = loc
    sep = N.new("ShaderNodeSeparateColor"); sep.location = (x, y)
    L.new(img_node.outputs["Color"], sep.inputs[0])
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


def build_mesh(name, cast, meshes, xf, settings, flip, mat_cache, origin=None):
    """Merge `meshes` (CastMesh) into one Blender mesh. xf: 3x3 matrix (scale + rotation)."""
    pos_l, nrm_l, uv_l, col_l, tri_l, mat_l = [], [], [], [], [], []
    slots, slot_of = [], {}
    need_col = False
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
            if is_overlay(mname) or is_vctint(mname):
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
        if m.vc is not None:
            vc = m.vc.view(np.uint8).reshape(-1, 4)
            if vc[:, 3].max() == 0 or vc[:, :3].max() < 10:
                # Unused data for this mesh (alpha all 0 / black RGB): otherwise the decal becomes
                # invisible or the tint black in Rendered mode
                vc = vc.copy()
                if vc[:, 3].max() == 0:
                    vc[:, 3] = 255
                if vc[:, :3].max() < 10:
                    vc[:, :3] = 255
            col_l.append(vc[f.ravel()])
        else:
            col_l.append(np.full((f.size, 4), 255, np.uint8))
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
    fmat = np.concatenate(mat_l)

    # Weld vertices (map exports are not indexed)
    if settings.cast_weld > 0:
        key = np.round(pos / (settings.cast_weld * max(abs(xf[0, 0]), 1e-9))).astype(np.int64)
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
    me.update(calc_edges=True)

    if settings.cast_custom_normals and len(me.loops) == nl:
        me.polygons.foreach_set("use_smooth", np.ones(nt, dtype=bool))
        me.normals_split_custom_set(lnrm.astype(np.float32))
    elif settings.cast_smooth_by_angle:
        me.polygons.foreach_set("use_smooth", np.ones(nt, dtype=bool))

    for h in slots:
        mat = mat_cache.get(h)
        if mat is None:
            mat = mat_cache[h] = get_material(cast, h, settings)
        me.materials.append(mat)
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
    for lod, ms in sorted(by_lod.items()):
        for group, gms in _split_by_group(cast, ms).items():
            oname = f"{stem}_LOD{lod}" + (f"_{group}" if group else "")
            me = build_mesh(oname, cast, gms, np.eye(3), settings, flip, mat_cache)
            if me is None:
                continue
            obj = bpy.data.objects.new(oname, me)
            if group:
                obj[GROUP_KEY] = group
            bpy.context.scene.collection.objects.link(obj)
            objs.append(obj)
    cast.free()
    return objs


def import_world_cast(path, settings, collection, xf, offset, report=print):
    """Import a map's geometry, optionally split into square tiles.

    xf: 3x3 (game units -> Blender), offset: shift in meters removed after the transform.
    Trigger / tool / dev faces go to separate sub-collections.
    """
    cast = CastFile(path)
    flip = detect_flip(cast)
    split = settings.world_split
    tile = settings.world_tile_size if split == "TILES" else 0.0
    groups = {}
    for m in cast.meshes:
        g = material_group(cast.mat_name(m.mat))
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
    for i, ((g, key), ms) in enumerate(sorted(groups.items())):
        wm.progress_update(i)
        center = np.array([(key[0] + 0.5) * tile, (key[1] + 0.5) * tile, 0.0]) if tile > 0 else np.zeros(3)
        if split == "MATERIAL":
            oname = f"{base}_{cast.mat_name(key[1])}"
        else:
            oname = f"{base}_{g + '_' if g else ''}tile_{key[0]}_{key[1]}"
        me = build_mesh(oname, cast, ms, xf, settings, flip, mat_cache, origin=offset + center)
        if me is None:
            continue
        o = bpy.data.objects.new(me.name, me)
        o.location = center
        o["apx_world_tile"] = True
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
