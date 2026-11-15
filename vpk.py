"""Respawn VPK reader (v2.3, R5Reloaded / Apex): lists the files and extracts the entries that
are stored without compression (this is the case of the *_script / *_snd / *_spawn .ent files
in R5Reloaded server VPKs). Compressed (LZHAM) entries are reported and skipped.

The file to open is the directory:  vpk/englishserver_<map>.bsp.pak000_dir.vpk
The data lives in:                  vpk/server_<map>.bsp.pak000_000.vpk
"""

import os
import re
import struct

_LANG = re.compile(r"^(english|french|german|italian|japanese|korean|polish|portuguese|russian|spanish|"
                   r"tchinese|schinese|mspanish)")


class VpkEntry:
    __slots__ = ("path", "pack", "chunks")

    def __init__(self, path, pack, chunks):
        self.path = path          # "maps/mp_rr_desertlands_hu_script.ent"
        self.pack = pack
        self.chunks = chunks      # [(offset, compressed, uncompressed)]

    @property
    def compressed(self):
        return any(c != u for _o, c, u in self.chunks)

    @property
    def size(self):
        return sum(u for _o, _c, u in self.chunks)


def read_dir(dir_path):
    with open(dir_path, "rb") as f:
        b = f.read()
    magic, major, minor, _dsize, _ssize = struct.unpack_from("<IHHII", b, 0)
    if magic != 0x55AA1234:
        raise ValueError(f"{os.path.basename(dir_path)} is not a VPK")
    if (major, minor) != (2, 3):
        raise ValueError(f"VPK version {major}.{minor} is not supported (expected Respawn 2.3)")
    off = 16

    def rs():
        nonlocal off
        e = b.index(b"\0", off)
        s = b[off:e].decode("utf-8", "replace")
        off = e + 1
        return s

    entries = []
    while True:
        ext = rs()
        if not ext:
            break
        while True:
            path = rs()
            if not path:
                break
            while True:
                name = rs()
                if not name:
                    break
                _crc, _preload, pack = struct.unpack_from("<IHH", b, off)
                off += 8
                chunks = []
                while True:
                    _lf, _tf, ao, cs, us = struct.unpack_from("<IHQQQ", b, off)
                    off += 30
                    chunks.append((ao, cs, us))
                    marker, = struct.unpack_from("<H", b, off)
                    off += 2
                    if marker == 0xFFFF:
                        break
                full = f"{name}.{ext}" if path.strip() in ("", " ") else f"{path}/{name}.{ext}"
                entries.append(VpkEntry(full, pack, chunks))
    return entries


def archive_path(dir_path, pack):
    d, base = os.path.split(dir_path)
    base = _LANG.sub("", base)
    return os.path.join(d, base.replace("_dir.vpk", f"_{pack:03d}.vpk"))


def read_entry(dir_path, entry):
    if entry.compressed:
        raise ValueError(f"{entry.path} is compressed in the VPK (LZHAM): extract it with a VPK tool")
    out = bytearray()
    with open(archive_path(dir_path, entry.pack), "rb") as f:
        for ao, cs, _us in entry.chunks:
            f.seek(ao)
            out += f.read(cs)
    return bytes(out)


def read_ents(dir_path, kinds=("script",)):
    """Return [(name, text)] for the requested .ent files, and the list of skipped (compressed) ones."""
    found, skipped = [], []
    for e in read_dir(dir_path):
        if not e.path.lower().endswith(".ent"):
            continue
        stem = os.path.splitext(os.path.basename(e.path))[0].lower()
        if kinds and not any(stem.endswith("_" + k) for k in kinds):
            continue
        if e.compressed:
            skipped.append(e.path)
            continue
        found.append((e.path, read_entry(dir_path, e).decode("utf-8", "replace")))
    return found, skipped
