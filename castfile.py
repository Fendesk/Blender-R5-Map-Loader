"""Lecteur du format Cast (exports Legion+/RSX) en pur Python + numpy.

Aucun addon externe requis. Lit les nœuds modl / mesh / matl / file ;
les squelettes et poids sont ignorés (props statiques).

Construction de mesh Blender en bloc (foreach_set), avec :
  - fusion de plusieurs meshes Cast en un seul objet multi-matériaux,
  - soudure des sommets en numpy (les exports de map sont non indexés),
  - normales custom par coin (optionnel),
  - découpage de la géométrie d'une map en tuiles (world).
"""

import os
import re
import struct

import bpy
import numpy as np

# id de propriété -> (dtype numpy, composantes)
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


class CastMesh:
    __slots__ = ("name", "pos", "nrm", "uv", "faces", "mat")

    def __init__(self):
        self.name = ""
        self.pos = self.nrm = self.uv = self.faces = None
        self.mat = 0


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
        self.materials = {}   # hash -> CastMaterial
        self.files = {}       # hash -> chemin
        with open(path, "rb") as f:
            self._buf = f.read()
        magic, _ver, roots, _flags = struct.unpack_from("<4sIII", self._buf, 0)
        if magic != b"cast":
            raise ValueError(f"{path} n'est pas un fichier Cast")
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
        if ident == T_MESH:
            m = CastMesh()
            m.name = props.get("n", "")
            m.pos = props.get("vp")
            m.nrm = props.get("vn")
            m.uv = props.get("u0")
            f = props.get("f")
            m.faces = f.astype(np.int64) if f is not None else None
            mh = props.get("m")
            m.mat = int(mh[0]) if mh is not None and len(mh) else 0
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

    def texture_path(self, h):
        p = self.files.get(h)
        if not p:
            return None
        p = p.replace("\\", os.sep).replace("/", os.sep)
        return p if os.path.isabs(p) else os.path.join(self.dir, p)

    def free(self):
        self.meshes = []
        self._buf = None


# --------------------------------------------------------------------------
# Matériaux
# --------------------------------------------------------------------------
def _png_has_alpha(path):
    try:
        with open(path, "rb") as f:
            head = f.read(33)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return False
        return head[25] in (4, 6)   # gris+alpha, RGBA
    except OSError:
        return False


ALPHA_HINTS = ("leaf", "leaves", "foliage", "grass", "fern", "bush", "shrub", "plant", "tree", "branch",
               "ivy", "vine", "moss", "flower", "weed", "decal", "dcl", "overlay", "fence", "chainlink",
               "chain_link", "grate", "grating", "net", "alpha", "cutout", "hair", "fx", "glass")
_ALPHA_RE = re.compile(r"(?:^|[_\-\s\d])(?:" + "|".join(ALPHA_HINTS) + r")")


def _wants_alpha(name, settings):
    mode = settings.cast_alpha_mode
    if mode == "NEVER":
        return False
    if mode == "ALWAYS":
        return True
    return _ALPHA_RE.search(name.lower()) is not None


def _normal_chain(nt, img_node, bsdf, flip_green, loc):
    """Normal map Apex : canaux RG uniquement (BC5), B reconstruit, vert DirectX optionnellement inversé."""
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
    # nx = 2r-1, ny = 2g-1, nz = sqrt(max(0, 1-nx²-ny²)), b = nz*0.5+0.5
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
    dot = N.new("ShaderNodeVectorMath"); dot.operation = "DOT_PRODUCT"; dot.location = (x + 480, y)
    cmb = N.new("ShaderNodeCombineXYZ"); cmb.location = (x + 400, y - 220)
    L.new(nx, cmb.inputs[0]); L.new(ny, cmb.inputs[1])
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


def _image(path, non_color):
    if not path or not os.path.isfile(path):
        return None
    img = bpy.data.images.load(path, check_existing=True)
    if non_color:
        try:
            img.colorspace_settings.name = "Non-Color"
        except TypeError:
            pass
    return img


def get_material(cast, h, settings):
    cm = cast.materials.get(h)
    name = cm.name if cm else "apx_default"
    mat = bpy.data.materials.get(name)
    if mat is not None and mat.get("apx_cast"):
        return mat
    mat = bpy.data.materials.new(name)
    mat["apx_cast"] = True
    if cm is None or not settings.cast_load_textures:
        return mat
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return mat
    links = nt.links
    x = -900
    y = 300

    # Rôle réel de chaque texture : Legion+ range parfois l'AO dans le slot "emissive",
    # on se fie donc d'abord au suffixe du nom de fichier (…_aoTexture.png).
    roles = {}
    for slot, h2 in cm.slots.items():
        p = cast.texture_path(h2)
        role = _role_from_filename(p)
        if role is None:
            role = slot if slot != "emissive" else ("emissive" if _looks_emissive(cm.name) else "emissive?")
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
        y -= 280
        return n, cast.texture_path(h2)

    use_alpha = _wants_alpha(cm.name, settings)
    alb, alb_path = tex("albedo", False)
    if alb is None:
        alb, alb_path = tex("diffuse", False)
    spec, _ = tex("specular", False)
    if alb and spec and settings.cast_spec_to_metal:
        # Spéculaire/gloss -> métal/rugosité : une spec claire = métal, sa couleur devient la couleur de base
        bw = nt.nodes.new("ShaderNodeRGBToBW"); bw.location = (x + 300, y + 150)
        links.new(spec.outputs["Color"], bw.inputs[0])
        mr = nt.nodes.new("ShaderNodeMapRange"); mr.location = (x + 460, y + 150)
        mr.inputs["From Min"].default_value = 0.22
        mr.inputs["From Max"].default_value = 0.5
        links.new(bw.outputs[0], mr.inputs["Value"])
        mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.location = (x + 640, y + 300)
        links.new(mr.outputs["Result"], mix.inputs["Factor"])
        links.new(alb.outputs["Color"], mix.inputs["A"])
        links.new(spec.outputs["Color"], mix.inputs["B"])
        links.new(mix.outputs["Result"], bsdf.inputs["Base Color"])
        links.new(mr.outputs["Result"], bsdf.inputs["Metallic"])
    elif alb:
        links.new(alb.outputs["Color"], bsdf.inputs["Base Color"])
    if alb:
        if use_alpha and _png_has_alpha(alb_path):
            links.new(alb.outputs["Alpha"], bsdf.inputs["Alpha"])
            if hasattr(mat, "blend_method"):
                try:
                    mat.blend_method = "HASHED"
                except TypeError:
                    pass

    nrm, _ = tex("normal", True)
    if nrm:
        _normal_chain(nt, nrm, bsdf, settings.cast_flip_normal_green, (x + 280, y + 280))

    gl, _ = tex("gloss", True)
    if gl:
        inv = nt.nodes.new("ShaderNodeInvert")
        inv.location = (x + 300, y + 280)
        links.new(gl.outputs["Color"], inv.inputs["Color"])
        links.new(inv.outputs["Color"], bsdf.inputs["Roughness"])

    em, _ = tex("emissive", False)
    if em:
        key = "Emission Color" if "Emission Color" in bsdf.inputs else "Emission"
        links.new(em.outputs["Color"], bsdf.inputs[key])
        if "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = 1.0

    op, _ = tex("opacity", True)
    if op and settings.cast_alpha_mode != "NEVER":
        links.new(op.outputs["Color"], bsdf.inputs["Alpha"])

    # Les autres textures (ao, cavity, emissive douteux...) sont ajoutées sans connexion
    for role in roles:
        if role not in ("albedo", "diffuse", "normal", "gloss", "emissive", "opacity", "specular"):
            tex(role, True)
    return mat


_ROLE_SUFFIX = (
    ("albedotexture", "albedo"), ("diffusetexture", "albedo"), ("normaltexture", "normal"),
    ("glosstexture", "gloss"), ("spectexture", "specular"), ("speculartexture", "specular"),
    ("aotexture", "ao"), ("cavitytexture", "cavity"), ("emissivetexture", "emissive"),
    ("emittexture", "emissive"), ("illumtexture", "emissive"), ("opacitymultiplytexture", "opacity"),
    ("opacitytexture", "opacity"),
)
_EMISSIVE_HINTS = ("light", "lamp", "screen", "emissive", "emis", "glow", "neon", "holo", "sign", "led", "lava")


def _role_from_filename(path):
    if not path:
        return None
    b = os.path.basename(path).lower()
    for suf, role in _ROLE_SUFFIX:
        if suf in b:
            return role
    return None


def _looks_emissive(name):
    low = name.lower()
    return any(h in low for h in _EMISSIVE_HINTS)


# --------------------------------------------------------------------------
# Construction de mesh
# --------------------------------------------------------------------------
def detect_flip(cast, sample=4000):
    """Compare l'ordre des sommets aux normales stockées pour choisir le sens des faces."""
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
    """Fusionne `meshes` (CastMesh) en un seul mesh Blender. xf : matrice 3x3 (échelle+rotation)."""
    pos_l, nrm_l, uv_l, tri_l, mat_l = [], [], [], [], []
    slots, slot_of = [], {}
    base = 0
    for m in meshes:
        f = m.faces[: (len(m.faces) // 3) * 3].reshape(-1, 3)
        if flip:
            f = f[:, [0, 2, 1]]
        nv = len(m.pos)
        if len(f) == 0 or f.max() >= nv:
            continue
        si = slot_of.get(m.mat)
        if si is None:
            si = slot_of[m.mat] = len(slots)
            slots.append(m.mat)
        pos_l.append(m.pos)
        nrm_l.append(m.nrm[f.ravel()] if m.nrm is not None and len(m.nrm) == nv else
                     np.zeros((f.size, 3), np.float32))
        uv_l.append(m.uv[f.ravel()] if m.uv is not None and len(m.uv) == nv else
                    np.zeros((f.size, 2), np.float32))
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
    fmat = np.concatenate(mat_l)

    # Soudure des sommets (les exports de map sont non indexés)
    if settings.cast_weld > 0:
        key = np.round(pos / settings.cast_weld).astype(np.int64)
        _u, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
        pos = pos[first]
        tris = inv.reshape(-1)[tris]
    # Triangles dégénérés
    good = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    if not good.all():
        keep_loops = np.repeat(good, 3)
        tris, fmat = tris[good], fmat[good]
        lnrm, luv = lnrm[keep_loops], luv[keep_loops]
    if len(tris) == 0:
        return None
    # Sommets inutilisés après filtrage
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


def import_model_cast(path, settings, name=None):
    """Import d'un modèle (prop) : un seul objet fusionné, en unités du jeu (l'échelle est appliquée ensuite)."""
    cast = CastFile(path)
    flip = detect_flip(cast)
    me = build_mesh(name or os.path.splitext(os.path.basename(path))[0], cast, cast.meshes,
                    np.eye(3), settings, flip, {})
    cast.free()
    if me is None:
        return []
    obj = bpy.data.objects.new(me.name, me)
    bpy.context.scene.collection.objects.link(obj)
    return [obj]


def import_world_cast(path, settings, collection, xf, offset, report=print):
    """Import de la géométrie d'une map, découpée en tuiles carrées.

    xf : 3x3 (unités jeu -> Blender), offset : décalage en mètres retiré après transformation.
    """
    cast = CastFile(path)
    flip = detect_flip(cast)
    tile = settings.world_tile_size
    groups = {}
    for m in cast.meshes:
        c = (m.pos.mean(axis=0).astype(np.float64) @ xf.T) - offset
        key = (int(np.floor(c[0] / tile)), int(np.floor(c[1] / tile))) if tile > 0 else (0, 0)
        groups.setdefault(key, []).append(m)

    base = os.path.splitext(os.path.basename(path))[0]
    mat_cache = {}
    objs = []
    wm = bpy.context.window_manager
    wm.progress_begin(0, len(groups))
    for i, (key, ms) in enumerate(sorted(groups.items())):
        wm.progress_update(i)
        center = np.array([(key[0] + 0.5) * tile, (key[1] + 0.5) * tile, 0.0]) if tile > 0 else np.zeros(3)
        me = build_mesh(f"{base}_tile_{key[0]}_{key[1]}", cast, ms, xf, settings, flip, mat_cache,
                        origin=offset + center)
        if me is None:
            continue
        o = bpy.data.objects.new(me.name, me)
        o.location = center
        o["apx_world_tile"] = True
        collection.objects.link(o)
        objs.append(o)
    wm.progress_end()
    nmesh = len(cast.meshes)
    cast.free()
    report(f"Géométrie : {nmesh} meshes -> {len(objs)} tuiles, {len(mat_cache)} matériaux")
    return objs
