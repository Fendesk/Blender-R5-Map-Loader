"""Minimal Cast file writer: static meshes + materials + texture files (what castfile.py reads).

Node:     u32 identifier, u32 node size (header + properties + children), u64 hash, u32 property count,
          u32 child count, properties, children.
Property: u16 type, u16 name length, u32 element count, name, data.
"""

import struct

import numpy as np

_HDR = struct.Struct("<IIQII")
_PH = struct.Struct("<HHI")


def _tag(s):
    return struct.unpack("<I", s.encode())[0]


def _prop_str(name, value):
    n = name.encode()
    v = value.encode("utf-8") + b"\0"
    return _PH.pack(0x73, len(n), 1) + n + v


def _prop_arr(name, arr, kind):
    n = name.encode()
    a = np.ascontiguousarray(arr)
    if kind == "u64":
        return _PH.pack(0x6C, len(n), a.size) + n + a.astype("<u8").tobytes()
    if kind == "u32":
        return _PH.pack(0x69, len(n), a.size) + n + a.astype("<u4").tobytes()
    if kind == "u16":
        return _PH.pack(0x68, len(n), a.size) + n + a.astype("<u2").tobytes()
    if kind == "u8":
        return _PH.pack(0x62, len(n), a.size) + n + a.astype("<u1").tobytes()
    if kind == "v2":
        return _PH.pack(0x7632, len(n), len(a)) + n + a.astype("<f4").tobytes()
    if kind == "v3":
        return _PH.pack(0x7633, len(n), len(a)) + n + a.astype("<f4").tobytes()
    raise ValueError(kind)


def _node(ident, h, props, children=()):
    body = b"".join(props)
    kids = b"".join(children)
    size = _HDR.size + len(body) + len(kids)
    return _HDR.pack(_tag(ident), size, h, len(props), len(children)) + body + kids


def write_static(path, name, meshes, materials):
    """meshes: [(mesh name, positions (N,3), normals (N,3), uvs (N,2), colours (N,) u32 RGBA, triangles (M*3,),
                 material index)]
    materials: [(material name, {slot name: texture path})]  (paths relative to the .cast or absolute)."""
    h = 1

    def nh():
        nonlocal h
        h += 1
        return h

    mat_nodes, mat_hash = [], []
    for mname, slots in materials:
        props = [_prop_str("n", mname), _prop_str("t", "pbr")]
        files = []
        for slot, tex in slots.items():
            fh = nh()
            files.append(_node("file", fh, [_prop_str("p", tex)]))
            props.append(_prop_arr(slot, np.array([fh], np.uint64), "u64"))
        mh = nh()
        mat_hash.append(mh)
        mat_nodes.append(_node("matl", mh, props, files))

    mesh_nodes = []
    for mesh_name, pos, nrm, uv, col, tri, mi in meshes:
        nv = len(pos)
        fk = "u16" if nv <= 0xFFFF else "u32"
        props = [_prop_str("n", mesh_name),
                 _prop_arr("vp", pos, "v3"),
                 _prop_arr("vn", nrm, "v3"),
                 _prop_arr("u0", uv, "v2"),
                 _prop_arr("f", tri, fk),
                 _prop_arr("m", np.array([mat_hash[mi]], np.uint64), "u64")]
        if col is not None:
            props.insert(4, _prop_arr("vc", col, "u32"))
        mesh_nodes.append(_node("mesh", nh(), props))

    model = _node("modl", nh(), [_prop_str("n", name)], mesh_nodes + mat_nodes)
    root = _node("root", nh(), [], [model])
    with open(path, "wb") as f:
        f.write(struct.pack("<4sIII", b"cast", 1, 1, 0))
        f.write(root)


def write_mprt(path, entries):
    """Legion+ .mprt v3: [(model name, origin xyz, angles pitch/yaw/roll, scale)]."""
    rec = struct.Struct("<7f")
    out = [struct.pack("<4sII", b"mprt", 3, len(entries))]
    for name, o, a, s in entries:
        out.append(name.encode("utf-8") + b"\0")
        # stored as (roll, pitch, yaw)
        out.append(rec.pack(o[0], o[1], o[2], a[2], a[0], a[1], s))
    with open(path, "wb") as f:
        f.write(b"".join(out))
