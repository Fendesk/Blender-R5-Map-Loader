"""Respawn BSP reader (Apex Legends / R5Reloaded, "rBSP" version 47), pure Python + numpy.

Reads what a map import needs, from a .bsp and its .bsp_lump files (as unpacked from a VPK):
  - world geometry: model 0 meshes, grouped by material (positions, normals, UVs, vertex colours),
  - material names (texture data + surface names),
  - static props (game lump "prps"): model path, origin, angles, scale.

Layouts (found on R5Reloaded World's Edge, checked against Legion+ exports):
  TEXTURE_DATA   0x02  16 B  (name offset in SURFACE_NAMES, width, height, flags)
  VERTICES       0x03  float3
  MODELS         0x0E  64 B  (mins, maxs, first mesh, mesh count, ...)
  SURFACE_NAMES  0x0F  NUL-separated strings
  VERTEX_NORMALS 0x1E  float3
  GAME_LUMP      0x23  "prps" v47: names[128 B], 3 ints (prop count first), props 64 B
  VERTEX_UNLIT   0x47  20 B  (position index, normal index, uv, colour)
  VERTEX_LIT_FLAT 0x48 24 B  (same start)
  VERTEX_LIT_BUMP 0x49 32 B  (same start, + lightmap uv...)
  VERTEX_UNLIT_TS 0x4A 24 B  (same start)
  MESH_INDICES   0x4F  uint16, relative to the material sort's vertex offset
  MESHES         0x50  28 B  (first index, triangle count, ..., material sort @22, flags @24)
  MATERIAL_SORT  0x52  12 B  (texture data index @0, vertex offset @8)
Mesh flags & 0x600 = vertex type: 0x000 lit flat, 0x200 lit bump, 0x400 unlit, 0x600 unlit TS.
"""

import os
import struct

import numpy as np

LUMP_TEXDATA = 0x02
LUMP_VERTICES = 0x03
LUMP_MODELS = 0x0E
LUMP_SURFACE_NAMES = 0x0F
LUMP_SURFACE_PROPS = 0x11      # 8 B: (flags..., name offset in SURFACE_NAMES @4)
LUMP_BVH_NODES = 0x12          # 64 B collision BVH4 nodes
LUMP_BVH_LEAF = 0x13           # uint32 words
LUMP_NORMALS = 0x1E
LUMP_GAME = 0x23
LUMP_MESH_INDICES = 0x4F
LUMP_MESHES = 0x50
LUMP_MATERIAL_SORT = 0x52
VERTEX_LUMPS = {0x000: (0x48, 24), 0x200: (0x49, 32), 0x400: (0x47, 20), 0x600: (0x4A, 24)}


class BspError(Exception):
    pass


class Bsp:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            head = f.read(16 + 128 * 16)
        if len(head) < 16 or head[:4] != b"rBSP":
            raise BspError(f"{os.path.basename(path)} is not a Respawn BSP")
        self.version = struct.unpack_from("<I", head, 4)[0]
        self._dir = []
        for i in range(128):
            off, ln, ver, _cc = struct.unpack_from("<IIII", head, 16 + i * 16) if len(head) >= 32 + i * 16 \
                else (0, 0, 0, 0)
            self._dir.append((off, ln, ver))
        self._cache = {}

    # ------------------------------------------------------------------
    def lump(self, i):
        if i in self._cache:
            return self._cache[i]
        ext = f"{self.path}.{i:04x}.bsp_lump"
        if os.path.isfile(ext):
            with open(ext, "rb") as f:
                data = f.read()
        else:
            off, ln, _v = self._dir[i]
            data = b""
            if ln:
                with open(self.path, "rb") as f:
                    f.seek(off)
                    data = f.read(ln)
        self._cache[i] = data
        return data

    def has_geometry(self):
        return len(self.lump(LUMP_MESHES)) > 0 and len(self.lump(LUMP_VERTICES)) > 0

    def free(self):
        self._cache.clear()

    # ------------------------------------------------------------------
    def material_names(self):
        """Texture data index -> material path ("world\\dev\\dev_white_512")."""
        names = self.lump(LUMP_SURFACE_NAMES)
        td = np.frombuffer(self.lump(LUMP_TEXDATA), dtype="<i4").reshape(-1, 4)
        out = []
        for off in td[:, 0]:
            end = names.find(b"\0", int(off))
            out.append(names[int(off): end if end >= 0 else len(names)].decode("latin1"))
        return out

    def models(self):
        mo = self.lump(LUMP_MODELS)
        n = len(mo) // 64
        out = []
        for k in range(n):
            v = struct.unpack_from("<6fii", mo, k * 64)
            out.append({"mins": v[0:3], "maxs": v[3:6], "first_mesh": v[6], "num_meshes": v[7]})
        return out

    # ------------------------------------------------------------------
    def world_geometry(self, model_index=0):
        """Meshes of a model, merged per material.
        Returns [(material name, positions (N,3) f4, normals (N,3) f4, uvs (N,2) f4, colours (N,) u4,
                  triangles (M*3,) u4)] in game units."""
        mats = self.material_names()
        meshes = np.frombuffer(self.lump(LUMP_MESHES), dtype=np.uint8).reshape(-1, 28)
        first_index = meshes[:, 0:4].copy().view("<u4").ravel().astype(np.int64)
        num_tris = meshes[:, 4:6].copy().view("<u2").ravel().astype(np.int64)
        msort = meshes[:, 22:24].copy().view("<u2").ravel().astype(np.int64)
        flags = meshes[:, 24:28].copy().view("<u4").ravel()
        sorts = np.frombuffer(self.lump(LUMP_MATERIAL_SORT), dtype=np.uint8).reshape(-1, 12)
        sort_tex = sorts[:, 0:2].copy().view("<i2").ravel().astype(np.int64)
        sort_voff = sorts[:, 8:12].copy().view("<i4").ravel().astype(np.int64)
        indices = np.frombuffer(self.lump(LUMP_MESH_INDICES), dtype="<u2").astype(np.int64)
        pos_all = np.frombuffer(self.lump(LUMP_VERTICES), dtype="<f4").reshape(-1, 3)
        nrm_all = np.frombuffer(self.lump(LUMP_NORMALS), dtype="<f4").reshape(-1, 3)

        vtx = {}
        for vt, (lump, stride) in VERTEX_LUMPS.items():
            b = self.lump(lump)
            if not b or len(b) % stride:
                continue
            a = np.frombuffer(b, dtype=np.uint8).reshape(-1, stride)
            vtx[vt] = (a[:, 0:4].copy().view("<i4").ravel(), a[:, 4:8].copy().view("<i4").ravel(),
                       a[:, 8:16].copy().view("<f4").reshape(-1, 2), a[:, 16:20].copy().view("<u4").ravel())

        model = self.models()[model_index] if self.models() else {"first_mesh": 0, "num_meshes": len(meshes)}
        sel = np.arange(model["first_mesh"], model["first_mesh"] + model["num_meshes"])
        sel = sel[(sel < len(meshes)) & (msort[sel] < len(sorts)) & (num_tris[sel] > 0)]
        # group meshes by (material, vertex type)
        groups = {}
        for m in sel:
            vt = int(flags[m] & 0x600)
            if vt not in vtx:
                continue
            tex = int(sort_tex[msort[m]])
            groups.setdefault((tex, vt), []).append(m)

        out = []
        for (tex, vt), ms in groups.items():
            ms = np.array(ms, dtype=np.int64)
            parts = [indices[first_index[m]: first_index[m] + num_tris[m] * 3] + sort_voff[msort[m]] for m in ms]
            ids = np.concatenate(parts)
            pidx, nidx, uv, col = vtx[vt]
            ids = ids[ids < len(pidx)]
            ids = ids[: len(ids) - len(ids) % 3]
            if ids.size == 0:
                continue
            uniq, tri = np.unique(ids, return_inverse=True)
            p = pidx[uniq]
            n = nidx[uniq]
            ok_p = (p >= 0) & (p < len(pos_all))
            ok_n = (n >= 0) & (n < len(nrm_all))
            positions = np.zeros((len(uniq), 3), np.float32)
            positions[ok_p] = pos_all[p[ok_p]]
            normals = np.zeros((len(uniq), 3), np.float32)
            normals[:, 2] = 1.0
            normals[ok_n] = nrm_all[n[ok_n]]
            name = mats[tex] if 0 <= tex < len(mats) else f"texdata_{tex}"
            out.append((name, positions, normals, uv[uniq].astype(np.float32), col[uniq].astype(np.uint32),
                        tri.astype(np.uint32)))
        return out

    # ------------------------------------------------------------------
    # Collision geometry (server BSPs: R5Flowstate ships no render geometry)
    # ------------------------------------------------------------------
    def has_collision(self):
        return len(self.lump(LUMP_BVH_NODES)) >= 64 and len(self.lump(LUMP_BVH_LEAF)) > 0 \
            and len(self.lump(LUMP_VERTICES)) > 0

    def surface_property_names(self):
        names = self.lump(LUMP_SURFACE_NAMES)
        sp = self.lump(LUMP_SURFACE_PROPS)
        out = []
        for i in range(len(sp) // 8):
            off = struct.unpack_from("<I", sp, i * 8 + 4)[0]
            end = names.find(b"\0", off) if off < len(names) else -1
            out.append(names[off: end].decode("latin1") if end >= 0 else "")
        return out

    def collision_triangles(self, model_index=0):
        """World collision (BVH4) of a model -> (positions (N,3) f4, triangles (M,3) i8, surface index (M,)).

        Node 64 B: 24 int16 bounds, then 4 dwords: low byte = contents mask (dword 0) / child types
        (dwords 2, 3: one nibble per child), high 24 bits = child index (node or leaf word).
        Child types: 0 node, 1 empty, 3 bundle, 4 triangles, 5 packed triangles, 6 quads, 7 packed quads,
        8 "star" collision, 9 static prop.
        Triangle / quad leaf: header word = surface property (12 bits) | count-1 (4 bits) | base / 1024 (16 bits);
        vertices = model.first_vertex + base*1024 + running offset. Triangle word: A delta (11 bits, from
        the previous triangle's A), B-A-1 (9), C-A-1 (9), edge bits (3). Quad word: A delta (10), B-A-1 (9),
        C-A-1 (9), edge bits (4); the 4th corner is B + C - A (parallelogram).
        Bundle: count, then one descriptor per child (low byte contents, type nibble at bit 8, length in
        words << 16), then the children data one after the other."""
        mo = self.lump(LUMP_MODELS)
        if len(mo) < 64 * (model_index + 1):
            return np.zeros((0, 3), np.float32), np.zeros((0, 3), np.int64), np.zeros(0, np.int32)
        m = struct.unpack_from("<6f2I4i4f", mo, model_index * 64)
        root, first_vert = m[8], m[10]
        leaf = np.frombuffer(self.lump(LUMP_BVH_LEAF), "<u4")
        nodes = np.frombuffer(self.lump(LUMP_BVH_NODES), "<u4").reshape(-1, 16)[:, 12:16]
        V = np.frombuffer(self.lump(LUMP_VERTICES), "<f4").reshape(-1, 3)
        nleaf, nv = len(leaf), len(V)
        tris, surf, quads = [], [], []

        def prims(idx, quad):
            if idx >= nleaf:
                return
            h = int(leaf[idx])
            n = ((h >> 12) & 0xF) + 1
            s = h & 0xFFF
            a = first_vert + (h >> 16) * 1024
            for i in range(n):
                if idx + 1 + i >= nleaf:
                    return
                w = int(leaf[idx + 1 + i])
                if quad:
                    a += w & 0x3FF
                    b = a + ((w >> 10) & 0x1FF) + 1
                    c = a + ((w >> 19) & 0x1FF) + 1
                    quads.append((a, b, c, s))
                else:
                    a += w & 0x7FF
                    tris.append((a, a + ((w >> 11) & 0x1FF) + 1, a + ((w >> 20) & 0x1FF) + 1))
                    surf.append(s)

        def do_leaf(t, idx):
            if t == 4:
                prims(idx, False)
            elif t == 6:
                prims(idx, True)
            elif t == 3 and idx < nleaf:
                cnt = int(leaf[idx])
                if cnt > 256:
                    return
                p = idx + 1
                descs = [int(x) for x in leaf[p: p + cnt]]
                p += cnt
                for d in descs:
                    st = (d >> 8) & 0xF
                    if st in (4, 6):
                        do_leaf(st, p)
                    p += d >> 16

        stack, seen = [root], set()
        nn = len(nodes)
        while stack:
            ni = stack.pop()
            if ni in seen or ni >= nn:
                continue
            seen.add(ni)
            d = nodes[ni]
            d2, d3 = int(d[2]), int(d[3])
            types = (d2 & 0xF, (d2 >> 4) & 0xF, d3 & 0xF, (d3 >> 4) & 0xF)
            for k in range(4):
                t = types[k]
                idx = int(d[k]) >> 8
                if t == 0:
                    stack.append(idx)
                elif t in (3, 4, 6):
                    do_leaf(t, idx)

        T = np.array(tris, dtype=np.int64).reshape(-1, 3)
        S = np.array(surf, dtype=np.int32)
        P = V
        if quads:
            Q = np.array(quads, dtype=np.int64)
            ok = (Q[:, :3] < nv).all(1)
            Q = Q[ok]
            extra = V[Q[:, 1]] + V[Q[:, 2]] - V[Q[:, 0]]
            d_idx = nv + np.arange(len(Q))
            P = np.concatenate([V, extra.astype(np.float32)])
            T = np.concatenate([T, Q[:, [0, 1, 2]], np.stack([Q[:, 1], d_idx, Q[:, 2]], 1)])
            S = np.concatenate([S, Q[:, 3].astype(np.int32), Q[:, 3].astype(np.int32)])
        ok = (T >= 0).all(1) & (T < len(P)).all(1)
        return P, T[ok], S[ok]

    def collision_geometry(self, model_index=0, uv_scale=1.0 / 512.0, skip_tools=True):
        """Collision triangles grouped per surface material, in the world_geometry() format. UVs are a box
        projection (no UVs in the collision data): uv = position / 512 on the plane facing the normal."""
        P, T, S = self.collision_triangles(model_index)
        names = self.surface_property_names()
        out = []
        if len(T) == 0:
            return out
        for si in np.unique(S):
            name = names[si] if 0 <= si < len(names) else f"surface_{si}"
            up = name.upper()
            if skip_tools and (not name or up.startswith("TOOLS")):
                continue
            tri = T[S == si]
            uniq, inv = np.unique(tri.ravel(), return_inverse=True)
            pos = P[uniq].astype(np.float32)
            t3 = inv.reshape(-1, 3)
            # area-weighted vertex normals
            e1 = pos[t3[:, 1]] - pos[t3[:, 0]]
            e2 = pos[t3[:, 2]] - pos[t3[:, 0]]
            fn = np.cross(e1, e2)
            vn = np.zeros_like(pos)
            for j in range(3):
                np.add.at(vn, t3[:, j], fn)
            ln = np.linalg.norm(vn, axis=1, keepdims=True)
            vn = np.divide(vn, ln, out=np.tile(np.float32([0, 0, 1]), (len(vn), 1)), where=ln > 1e-12)
            ax = np.abs(vn).argmax(1)
            uv = np.where(ax[:, None] == 2, pos[:, [0, 1]], np.where(ax[:, None] == 0, pos[:, [1, 2]],
                                                                      pos[:, [0, 2]])) * uv_scale
            col = np.full(len(pos), 0xFFFFFFFF, np.uint32)
            out.append((name, pos, vn.astype(np.float32), uv.astype(np.float32), col,
                        inv.astype(np.uint32)))
        return out

    # ------------------------------------------------------------------
    def static_props(self):
        """Returns (model paths list, origins (N,3), angles (N,3) pitch/yaw/roll in degrees, scales (N,),
        model indices (N,)) from the "prps" game lump. Empty arrays when the map has none."""
        g = self.lump(LUMP_GAME)
        empty = ([], np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros(0, np.float32),
                 np.zeros(0, np.int64))
        if len(g) < 4:
            return empty
        count = struct.unpack_from("<i", g, 0)[0]
        base = None
        for k in range(count):
            gid, _flags, _ver, _off, glen = struct.unpack_from("<4sHHii", g, 4 + k * 16)
            if gid in (b"prps", b"sprp"):
                base = 4 + count * 16
                # several game lumps: their data follow each other in the order of the directory
                for j in range(k):
                    base += struct.unpack_from("<4sHHii", g, 4 + j * 16)[4]
                break
        if base is None:
            return empty
        nn = struct.unpack_from("<i", g, base)[0]
        names = []
        o = base + 4
        for _ in range(nn):
            names.append(g[o: o + 128].split(b"\0", 1)[0].decode("latin1"))
            o += 128
        nprops = struct.unpack_from("<i", g, o)[0]
        start = o + 12                       # prop count + 2 ints
        stride = 64
        nprops = max(0, min(nprops, (len(g) - start) // stride))
        a = np.frombuffer(g, dtype=np.uint8, count=nprops * stride, offset=start).reshape(nprops, stride)
        f = a[:, 0:28].copy().view("<f4").reshape(nprops, 7)
        midx = a[:, 28:30].copy().view("<u2").ravel().astype(np.int64)
        return names, f[:, 0:3], f[:, 3:6], f[:, 6], midx
