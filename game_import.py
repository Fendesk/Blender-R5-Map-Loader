"""Import a map straight from an R5Reloaded / R5Flowstate install.

Pipeline (each map is built once into a cache folder that looks like a Legion+ export, then the regular
importer loads it):
  1. revpk (R5Reloaded SDK tool) unpacks the map VPK: .bsp + .bsp_lump files + .ent files (LZHAM).
  2. bsp.py reads the static props  -> <map>_LOD0.mprt
                 and the world meshes -> <map>_LOD0.cast (materials point to the exported textures).
  3. Legion+ (command line) exports the models and materials of the map rpak -> _models / materials.
     Only what the map uses is kept; identical textures are stored once in _shared_textures.
  4. The .ent files give the dynamic props, POIs and the map's sun (light_environment in _env.ent).

R5Reloaded maps have a client VPK with the full BSP (geometry + props). R5Flowstate "_hu" maps only ship a
server VPK: props and entities are imported, the world surfaces are rebuilt from the collision BVH of the
server BSP (bsp.collision_geometry) and the models are exported with RSX (rsx_nogui.exe, Oodle paks).
"""

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

import bpy

from . import bsp as bsplib
from . import castwriter, placement

CACHE_VERSION = 1
_HASH_NAME = re.compile(r"^0x[0-9a-fA-F]+$")
_CLIENT_RE = re.compile(r"^english(client|server)_(.+)\.bsp\.pak000_dir\.vpk$", re.IGNORECASE)
_SKIP_MAPS = {"mp_common", "frontend", "mp_lobby"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --------------------------------------------------------------------------
# Installs, maps, tools
# --------------------------------------------------------------------------
def _home():
    return os.path.expanduser("~")


def find_game_dirs():
    """Folders holding r5apex.exe + vpk + paks (R5Reloaded library channels, R5Flowstate...)."""
    pats = [r"C:\Program Files\R5Reloaded\R5R Library\*", r"C:\Program Files (x86)\R5Reloaded\R5R Library\*",
            os.path.join(_home(), "Games", "*"), r"C:\Games\*", r"D:\Games\*", r"D:\R5Reloaded\*",
            os.path.join(_home(), "Documents", "*")]
    out = []
    for pat in pats:
        for d in glob.glob(pat):
            if os.path.isfile(os.path.join(d, "r5apex.exe")) and os.path.isdir(os.path.join(d, "vpk")) \
                    and os.path.isdir(os.path.join(d, "paks")):
                out.append(d)
    return sorted(set(out))


def list_maps(game_dir):
    """[(map name, "full" | "props")]: "full" = client VPK with geometry, "props" = server VPK only."""
    vdir = os.path.join(game_dir, "vpk")
    if not os.path.isdir(vdir):
        return []
    kinds = {}
    for f in os.listdir(vdir):
        m = _CLIENT_RE.match(f)
        if not m:
            continue
        ctx, name = m.group(1).lower(), m.group(2)
        if name.lower() in _SKIP_MAPS or not name.lower().startswith("mp_"):
            continue
        if ctx == "client":
            kinds[name] = "full"
        else:
            kinds.setdefault(name, "props")
    return sorted(kinds.items())


_map_items_cache = {"key": None, "items": [("NONE", "(no map found)", "")]}


def map_enum_items(self, context):
    gd = bpy.path.abspath(self.game_dir) if self.game_dir else ""
    key = (gd, os.path.getmtime(os.path.join(gd, "vpk")) if gd and os.path.isdir(os.path.join(gd, "vpk")) else 0)
    if _map_items_cache["key"] != key:
        maps = list_maps(gd) if gd else []
        items = [(n, f"{n}  ({'geometry + props' if k == 'full' else 'collision terrain + props'})",
                  "Client VPK: render geometry + props" if k == "full" else
                  "Server VPK only (R5Flowstate): props, entities, sun, and the terrain rebuilt from the collision "
                  "data (no UVs: box-mapped textures). Models need RSX; a Legion+ export gives the real geometry")
                 for n, k in maps]
        _map_items_cache["key"] = key
        _map_items_cache["items"] = items or [("NONE", "(no map found)", "")]
    return _map_items_cache["items"]


def _walk_find(roots, filename, depth=4):
    for root in roots:
        if not os.path.isdir(root):
            continue
        base = root.rstrip("\\/").count(os.sep)
        for d, dirs, files in os.walk(root):
            if d.count(os.sep) - base >= depth:
                dirs[:] = []
            dirs[:] = [x for x in dirs if not x.startswith(".") and x.lower() not in
                       ("node_modules", "exported_files", "__pycache__", "paks", "audio", "_images")]
            if filename.lower() in (f.lower() for f in files):
                yield os.path.join(d, next(f for f in files if f.lower() == filename.lower()))


TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")


def bundled_tool(name):
    """Tools shipped with the add-on (tools/README.txt): LegionPlus.exe, revpk.exe."""
    p = os.path.join(TOOLS_DIR, name)
    return p if os.path.isfile(p) else ""


def find_revpk(game_dir=""):
    cands = [bundled_tool("revpk.exe")] if bundled_tool("revpk.exe") else []
    if game_dir:
        cands.append(os.path.join(game_dir, "bin", "revpk.exe"))
    cands += glob.glob(r"C:\Program Files\R5Reloaded\R5R Library\*\bin\revpk.exe")
    cands += list(_walk_find([os.path.join(_home(), "Documents"), os.path.join(_home(), "Downloads"),
                              os.path.join(_home(), "Desktop")], "revpk.exe", depth=6))
    seen, out = set(), []
    for c in cands:
        if os.path.isfile(c) and c.lower() not in seen:
            seen.add(c.lower())
            out.append(c)
    return out


def find_rsx():
    """RSX (r-ex/rsx), command-line build rsx_nogui.exe: the only tool that reads the R5Flowstate paks
    (Oodle). Not bundled: the user's own copy."""
    hits = list(_walk_find([os.path.join(_home(), "Documents"), os.path.join(_home(), "Downloads"),
                            os.path.join(_home(), "Desktop"), r"C:\Tools"], "rsx_nogui.exe", depth=4))
    hits.sort(key=len)
    return hits


def _rsx_cmd(exe, rpak, out_dir, what="mdl_"):
    # --loadwhitelist is required: without it this build loads nothing and exports nothing.
    # Textures are not exported (-matltextures / txtr crash rsx_nogui 2.3 on the R5Flowstate paks, whose HD
    # streams are not installed): they are found by material name in the other caches and exports.
    threads = str(max(1, min(8, (os.cpu_count() or 4) - 1)))
    return [exe, "-nogui", "--loadwhitelist", "mdl_,matl,txtr,shds,shdr", "-export", "--exporttypes", what,
            "--exportthreads", threads, "--exportdir", out_dir.rstrip("\\/") + os.sep, rpak]


def find_legion():
    hits = list(_walk_find([os.path.join(_home(), "Documents"), os.path.join(_home(), "Downloads"),
                            os.path.join(_home(), "Desktop"), r"C:\Tools"], "LegionPlus.exe", depth=5))
    # Prefer a standalone Legion+ (not the copy bundled inside Unity projects)
    hits.sort(key=lambda p: ("assets" in p.lower(), len(p)))
    own = bundled_tool("LegionPlus.exe")
    return ([own] if own else []) + hits


def default_cache_dir():
    try:
        return bpy.utils.extension_path_user(__package__, path="game_cache", create=True)
    except Exception:  # noqa: BLE001  (legacy add-on install)
        d = os.path.join(bpy.utils.user_resource("DATAFILES", path="r5_map_loader_cache", create=True))
        return d


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------
class GameImportError(Exception):
    pass


def _run_revpk(candidates, vpk_file, out_dir, log):
    """revpk tokenizes its arguments like a Source command line: ':' and spaces break paths. Run it from the
    VPK folder with a bare file name and a relative output path."""
    # What works: paths relative to the drive root, without spaces nor "..". So: a staging folder without
    # spaces, a junction in it pointing to the game's vpk folder ("Program Files" has a space), revpk run
    # from the drive root, then the result is moved into the cache.
    import _winapi
    os.makedirs(out_dir, exist_ok=True)
    staging = None
    for cand in (os.path.join(tempfile.gettempdir(), "r5ml_unpack"),
                 os.path.join(os.environ.get("PUBLIC", r"C:\Users\Public"), "r5ml_unpack"), r"C:\r5ml_unpack"):
        if " " not in os.path.splitdrive(cand)[1]:
            staging = cand
            break
    if staging is None:
        raise GameImportError("No folder without spaces available for revpk (temp folder path has spaces)")
    link = os.path.join(staging, "vpk_link")
    out = os.path.join(staging, "out")
    _remove_staging(staging, link)
    if os.path.lexists(link):
        raise GameImportError(f"Cannot remove the old link {link}")
    os.makedirs(staging, exist_ok=True)
    _winapi.CreateJunction(os.path.dirname(os.path.abspath(vpk_file)), link)
    drive, tail = os.path.splitdrive(staging)
    root = drive + "\\"
    arg = os.path.join(tail.lstrip("\\/"), "vpk_link", os.path.basename(vpk_file))
    rel = os.path.join(tail.lstrip("\\/"), "out") + "\\"
    stem = os.path.basename(vpk_file).lower().replace("englishclient_", "").replace("englishserver_", "")
    stem = stem.split(".bsp")[0]
    target = os.path.join(out, "maps", f"{stem}.bsp")
    errors = []
    try:
        seen = set()
        for exe in candidates:
            # identical copies (same size) are only tried once
            key = os.path.getsize(exe) if os.path.isfile(exe) else exe
            if key in seen:
                continue
            seen.add(key)
            shutil.rmtree(out, ignore_errors=True)
            # revpk also tokenizes its OWN path: run a copy placed in the staging folder (no spaces), so a revpk
            # living in "C:\Program Files\..." or in Blender's "...\Blender Foundation\..." works too.
            run_exe = os.path.join(staging, "revpk.exe")
            try:
                shutil.copy2(exe, run_exe)
            except OSError as exc:
                errors.append(f"{exe}: {exc}")
                continue
            try:
                r = subprocess.run([run_exe, "-unpack", arg, rel], cwd=root, capture_output=True, timeout=1800,
                                   creationflags=_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(f"{exe}: {exc}")
                continue
            if os.path.isfile(target):
                log(f"VPK unpacked with {exe}")
                dst = os.path.join(out_dir, "maps")
                shutil.rmtree(dst, ignore_errors=True)
                shutil.move(os.path.join(out, "maps"), dst)
                return exe
            tail_txt = (r.stdout or b"")[-300:].decode("utf-8", "replace")
            errors.append(f"{exe}: {'usage printed' if 'usage guide' in tail_txt else tail_txt.strip()[-120:]}")
    finally:
        _remove_staging(staging, link)
    raise GameImportError("No revpk could unpack the VPK:\n" + "\n".join(errors))


def _remove_staging(staging, link):
    """Remove the junction FIRST (never recurse into the game's vpk folder), then the staging folder."""
    if os.path.lexists(link):
        try:
            os.rmdir(link)
        except OSError:
            pass
    if not os.path.lexists(link):
        shutil.rmtree(staging, ignore_errors=True)


def _legion_private_copy(legion_exe, cache_root):
    """Legion+ always writes next to its exe: run a private copy so the user's own exports are untouched."""
    src = os.path.dirname(legion_exe)
    dst = os.path.join(cache_root, "_tools", "legion")
    os.makedirs(dst, exist_ok=True)
    for f in os.listdir(src):
        if f.lower() == os.path.basename(legion_exe).lower() or f.lower().endswith((".dll", ".cfg")):
            s, d = os.path.join(src, f), os.path.join(dst, f)
            if not os.path.isfile(d) or os.path.getsize(d) != os.path.getsize(s):
                shutil.copy2(s, d)
    return os.path.join(dst, os.path.basename(legion_exe))


RPAK_ZSTD = 0x8000      # R5Reloaded SDK paks: zstd stream after the 0x80-byte header (Legion+ 1.5 crashes on them)


def _stage_rpak(rpak, stage_dir, log):
    """Return a pak Legion+ can read. zstd-compressed paks are decompressed into `stage_dir`, with hard links to
    the game's .starpak files next to them (streamed model / texture data). Hard links take no space and the
    game files are never modified; deleting a link leaves the original untouched."""
    import struct
    with open(rpak, "rb") as f:
        head = bytearray(f.read(0x80))
    if len(head) < 0x80 or head[:4] != b"RPak":
        return rpak
    flags = struct.unpack_from("<H", head, 6)[0]
    if not flags & RPAK_ZSTD:
        return rpak
    try:
        import zstandard
    except ImportError:
        log("zstandard module missing: cannot read this pak")
        return rpak
    comp, _o, _s, decomp = struct.unpack_from("<4Q", head, 24)
    os.makedirs(stage_dir, exist_ok=True)
    dst = os.path.join(stage_dir, os.path.basename(rpak))
    with open(rpak, "rb") as f:
        f.seek(0x80)
        data = zstandard.ZstdDecompressor().decompress(f.read(comp - 0x80), max_output_size=decomp)
    struct.pack_into("<H", head, 6, flags & ~(RPAK_ZSTD | 0x100))
    struct.pack_into("<Q", head, 24, len(data) + 0x80)
    with open(dst, "wb") as f:
        f.write(bytes(head))
        f.write(data)
    del data
    linked = 0
    for sp in glob.glob(os.path.join(os.path.dirname(rpak), "*.starpak")):
        link = os.path.join(stage_dir, os.path.basename(sp))
        if os.path.exists(link):
            linked += 1
            continue
        try:
            os.link(sp, link)
            linked += 1
        except OSError:
            pass
    if not linked:
        log("starpaks could not be linked (cache on another drive than the game): some models may be skipped")
    return dst


def _stage_dir(cache_root, game_dir):
    """Staging folder on the game's drive (hard links only work on the same drive)."""
    drive = os.path.splitdrive(os.path.abspath(game_dir))[0].lower()
    for cand in (os.path.join(cache_root, "_stage"), os.path.join(tempfile.gettempdir(), "r5ml_stage")):
        if os.path.splitdrive(os.path.abspath(cand))[0].lower() == drive:
            return cand
    return os.path.join(cache_root, "_stage")


def _clear_stage(stage_dir):
    if os.path.isdir(stage_dir):
        for f in os.listdir(stage_dir):
            try:
                os.remove(os.path.join(stage_dir, f))       # files and hard links only, never recursive
            except OSError:
                pass


def _legion_cmd(exe, rpak, what):
    load = {"models": "--loadmodels", "materials": "--loadmaterials", "images": "--loadimages"}[what]
    return [exe, "--export", rpak, load, "--mdlfmt", "cast", "--imgfmt", "png", "--overwrite", "--nologfile"]


def _clear_legion_output(exe):
    out = os.path.join(os.path.dirname(exe), "exported_files")
    if os.path.isdir(out):
        shutil.rmtree(out, ignore_errors=True)
    return out


def _move_tree_into_pool(folder, pool):
    """Move every texture of `folder` into the shared pool (first copy wins, duplicates are deleted)."""
    n = 0
    for d, _dirs, files in os.walk(folder):
        for f in files:
            if not f.lower().endswith((".png", ".dds", ".tga")):
                continue
            src = os.path.join(d, f)
            dst = os.path.join(pool, f)
            if os.path.exists(dst):
                os.remove(src)
            else:
                os.replace(src, dst)
                n += 1
    return n


def _ent_sun_only(env_path, out_path):
    """Keep only light_environment from the (large) _env.ent."""
    with open(env_path, "r", encoding="utf-8", errors="replace") as f:
        data = placement.parse_ent_full(f.read())
    if not data.sun:
        return False
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("{\n" + "".join(f'"{k}" "{v}"\n' for k, v in data.sun.items()) + "}\n")
    return True


def map_cache_dir(cache_root, game_dir, map_name):
    tag = re.sub(r"[^a-z0-9]+", "_", os.path.basename(os.path.normpath(game_dir)).lower()).strip("_") or "game"
    return os.path.join(cache_root, tag, map_name)


def cache_ready(mdir, map_name):
    try:
        with open(os.path.join(mdir, "r5_cache.json"), "r", encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        return None
    if info.get("version") != CACHE_VERSION or not os.path.isfile(os.path.join(mdir, f"{map_name}_LOD0.mprt")):
        return None
    return info


def cache_size(mdir):
    total = 0
    for d, _dirs, files in os.walk(mdir):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
    return total


# --------------------------------------------------------------------------
# Build (generator: yields a status text or a running subprocess)
# --------------------------------------------------------------------------
_PAK_SKIP_RE = re.compile(r"loadscreen|_ps4|^ui|^gcard|^patch_master|^streaming", re.IGNORECASE)


def pak_search_order(paks_dir, map_name, search_all=False):
    """Where to look for a map's models / materials, best first. A season variant ("mp_rr_canyonlands_mu1")
    reuses many assets of its base map ("mp_rr_canyonlands_64k_x_64k") and of its patches ("..._mu1(02).rpak").
      1. the map pak's patches (newest first), then the map pak
      2. the other paks of the same map family (same "mp_rr_<name>" prefix), patches first
      3. common_mp, common, common_early
      4. (search_all) every other map pak"""
    if not os.path.isdir(paks_dir):
        return []
    files = [f for f in os.listdir(paks_dir) if f.lower().endswith(".rpak") and not _PAK_SKIP_RE.search(f)]

    def with_patches(stem):
        pat = re.compile(re.escape(stem) + r"\((\d+)\)\.rpak$", re.IGNORECASE)
        patches = sorted((f for f in files if pat.match(f)), key=lambda f: -int(pat.match(f).group(1)))
        base = [f for f in files if f.lower() == f"{stem}.rpak".lower()]
        return patches + base

    def stem_of(f):
        return re.sub(r"\(\d+\)$", "", os.path.splitext(f)[0])

    order = with_patches(map_name)
    family = "_".join(map_name.split("_")[:3]).lower()          # mp_rr_canyonlands
    stems = sorted({stem_of(f) for f in files if stem_of(f).lower().startswith(family)},
                   key=lambda s: ("64k" not in s, s))             # the base "_64k_x_64k" map first
    for s in stems:
        order += [f for f in with_patches(s) if f not in order]
    for s in ("common_mp", "common", "common_early"):
        order += [f for f in with_patches(s) if f not in order]
    if search_all:
        for s in sorted({stem_of(f) for f in files if f.lower().startswith("mp_")}):
            order += [f for f in with_patches(s) if f not in order]
    return [os.path.join(paks_dir, f) for f in order]


def _missing_texture_refs(models_dir, pool):
    """File names referenced by the exported model casts and found neither next to them nor in the pool."""
    from .castfile import CastFile
    in_pool = {f.lower() for f in os.listdir(pool)} if os.path.isdir(pool) else set()
    missing = set()
    for d in os.listdir(models_dir):
        for c in glob.glob(os.path.join(models_dir, d, "*.cast")):
            try:
                cast = CastFile(c)
            except Exception:  # noqa: BLE001
                continue
            for p in cast.files.values():
                name = os.path.basename(p.replace("\\", "/"))
                stem = os.path.splitext(name)[0].lower()
                if os.path.isfile(os.path.join(cast.dir, p)) or any(stem + e in in_pool for e in (".png", ".dds", ".tga")):
                    continue
                missing.add(stem)
            cast.free()
    return missing


def _legion_export_into(exe, rp, what, cache_root, game_dir, log):
    """Generator: run Legion+ on one pak (staged when needed). Returns the export folder of `what`."""
    out = _clear_legion_output(exe)
    stage = _stage_dir(cache_root, game_dir)
    _clear_stage(stage)
    readable = _stage_rpak(rp, stage, log)
    proc = subprocess.Popen(_legion_cmd(exe, readable, what), cwd=os.path.dirname(exe))
    yield proc
    _clear_stage(stage)
    return out


def _fill_missing_textures(models_dir, pool, cache_root, game_dir, tex_paks, exe, log):
    """Generator: textures the model casts reference but Legion+ did not export with them (they live in other
    paks, usually the common ones). Other cached maps first, then image exports. Returns how many are left."""
    yield "Looking for missing textures..."
    need = _missing_texture_refs(models_dir, pool)
    n0 = len(need)
    for other in glob.glob(os.path.join(cache_root, "*", "*", "_shared_textures")):
        if not need:
            break
        if os.path.normcase(other) == os.path.normcase(pool):
            continue
        for f in os.listdir(other):
            stem = os.path.splitext(f)[0].lower()
            if stem in need and not os.path.exists(os.path.join(pool, f)):
                shutil.copy2(os.path.join(other, f), os.path.join(pool, f))
                need.discard(stem)
    for rp in tex_paks:
        if not need:
            break
        yield f"Legion+: exporting the missing textures from {os.path.basename(rp)}..."
        out = yield from _legion_export_into(exe, rp, "images", cache_root, game_dir, log)
        for d, _dirs, fs in os.walk(out):
            for f in fs:
                stem = os.path.splitext(f)[0].lower()
                if stem in need:
                    dst = os.path.join(pool, f)
                    if not os.path.exists(dst):
                        os.replace(os.path.join(d, f), dst)
                    need.discard(stem)
        log(f"textures: {n0 - len(need)}/{n0} found after {os.path.basename(rp)}")
    _clear_legion_output(exe)
    return len(need)


def _install_tag(game_dir):
    return re.sub(r"[^a-z0-9]+", "_", os.path.basename(os.path.normpath(game_dir)).lower()).strip("_") or "game"


def extra_dir(cache_root, game_dir):
    """Models fetched on demand (ReMap maps, props from other maps): <cache>/_extra/<install>/"""
    return os.path.join(cache_root, "_extra", _install_tag(game_dir))


def fetch_models(game_dir, keys, cache_root, legion_exe, log):
    """Generator: export the given models from any pak of the game (common paks first, then every map pak)
    into the "_extra" cache, which every import uses as a fallback models folder."""
    root = extra_dir(cache_root, game_dir)
    models_dir = os.path.join(root, "_models")
    pool = os.path.join(root, "_shared_textures")
    os.makedirs(models_dir, exist_ok=True)
    os.makedirs(pool, exist_ok=True)
    exe = _legion_private_copy(legion_exe, cache_root)
    order = pak_search_order(os.path.join(game_dir, "paks", "Win64"), "", True)
    rpaks = [p for p in order if os.path.basename(p).lower().startswith("common")] + \
            [p for p in order if not os.path.basename(p).lower().startswith("common")]
    needed = {k.lower() for k in keys}
    have = {d.lower() for d in os.listdir(models_dir)}
    for rp in rpaks:
        missing = needed - have
        if not missing:
            break
        yield f"Legion+: looking for {len(missing)} models in {os.path.basename(rp)}..."
        out = yield from _legion_export_into(exe, rp, "models", cache_root, game_dir, log)
        src = os.path.join(out, "models")
        if os.path.isdir(src):
            for d in os.listdir(src):
                if d.lower() in missing:
                    target = os.path.join(models_dir, d)
                    shutil.rmtree(target, ignore_errors=True)
                    os.replace(os.path.join(src, d), target)
                    have.add(d.lower())
        log(f"models: {len(have & needed)}/{len(needed)} after {os.path.basename(rp)}")
    _clear_legion_output(exe)
    yield "Sharing identical textures..."
    _move_tree_into_pool(models_dir, pool)
    commons = [p for p in rpaks if os.path.basename(p).lower().startswith("common")]
    yield from _fill_missing_textures(models_dir, pool, cache_root, game_dir, commons, exe, log)
    return sorted(needed - have)


def all_cached_model_dirs(s):
    root = bpy.path.abspath(s.game_cache_dir) if s.game_cache_dir else default_cache_dir()
    return [d for d in sorted(glob.glob(os.path.join(root, "*", "*", "_models")))
            if os.path.isdir(d) and any(os.scandir(d))]


def missing_models_from_text():
    """Model keys listed by the last import in the Blender text "APX_missing_models"."""
    txt = bpy.data.texts.get("APX_missing_models")
    if txt is None:
        return []
    # model paths have no spaces (header / notes lines do)
    lines = [ln.strip() for ln in txt.as_string().splitlines()[1:] if ln.strip() and " " not in ln.strip()]
    return sorted({placement.model_key(ln) for ln in lines})


def build_cache(game_dir, map_name, kind, cache_root, legion_exe, revpk_cands, log, search_all=False,
                rsx_exe=""):
    mdir = map_cache_dir(cache_root, game_dir, map_name)
    if os.path.isdir(mdir):
        shutil.rmtree(mdir, ignore_errors=True)
    os.makedirs(mdir, exist_ok=True)
    pool = os.path.join(mdir, "_shared_textures")
    os.makedirs(pool, exist_ok=True)
    models_dir = os.path.join(mdir, "_models")
    os.makedirs(models_dir, exist_ok=True)
    info = {"version": CACHE_VERSION, "map": map_name, "game_dir": game_dir, "kind": kind}

    # 1) VPK -------------------------------------------------------------
    yield "Unpacking the map VPK (revpk)..."
    ctx = "client" if kind == "full" else "server"
    vpk_file = os.path.join(game_dir, "vpk", f"english{ctx}_{map_name}.bsp.pak000_dir.vpk")
    vdir = os.path.join(mdir, "vpk")
    _run_revpk(revpk_cands, vpk_file, vdir, log)
    maps_dir = os.path.join(vdir, "maps")
    if kind == "full" and not os.path.isfile(os.path.join(maps_dir, f"{map_name}.bsp")):
        raise GameImportError("The client VPK has no .bsp")

    # 2) BSP: props + geometry -------------------------------------------
    yield "Reading the BSP..."
    b = bsplib.Bsp(os.path.join(maps_dir, f"{map_name}.bsp"))
    names, org, ang, scl, midx = b.static_props()
    entries = []
    for i in range(len(org)):
        mi = int(midx[i])
        if mi >= len(names):
            continue
        key = placement.model_key(names[mi])
        s = float(scl[i]) if scl[i] > 0 else 1.0
        entries.append((key, tuple(map(float, org[i])), tuple(map(float, ang[i])), s))
    castwriter.write_mprt(os.path.join(mdir, f"{map_name}_LOD0.mprt"), entries)
    needed_models = {e[0] for e in entries}
    # the dynamic props of the script entities (doors, loot bins, ziplines...) need their models too
    script_ent = os.path.join(maps_dir, f"{map_name}_script.ent")
    if os.path.isfile(script_ent):
        try:
            needed_models |= {placement.model_key(p.model) for p in placement.parse_ent(script_ent)}
        except Exception as exc:
            log(f"Could not read {os.path.basename(script_ent)}: {exc}")
    info["props"] = len(entries)
    log(f"BSP: {len(entries)} static props, {len(needed_models)} models")
    geometry = b.world_geometry(0) if b.has_geometry() else []
    if not geometry and b.has_collision():
        # server BSP (R5Flowstate): no render meshes, but the collision BVH holds the world surfaces
        yield "Rebuilding the terrain from the collision data..."
        geometry = b.collision_geometry(0)
        info["geometry_source"] = "collision"
        log(f"collision: {sum(len(g[5]) // 3 for g in geometry)} triangles, {len(geometry)} materials")
    # tool materials (TOOLS\TOOLSNODRAW, triggers, clip...) have no textures in any pak: don't look for them
    from .castfile import material_group
    needed_mats = {k for k in (os.path.basename(g[0].replace("\\", "/")).lower() for g in geometry)
                   if not k.startswith("tools") and material_group(k) not in ("tool", "trigger")}
    info["geometry_groups"] = len(geometry)

    # 3) Legion+: models + materials --------------------------------------
    # (R5Flowstate "_hu" paks use a compression Legion+ 1.5 cannot read: it crashes on them. Their models come
    # from an existing Legion+ export of the map and from the R5Reloaded caches, see import_sources().)
    mats_dir = os.path.join(mdir, "_materials")
    os.makedirs(mats_dir, exist_ok=True)
    have_models, have_mats = set(), set()
    jobs = []
    if kind == "full":
        exe = _legion_private_copy(legion_exe, cache_root)
        paks = os.path.join(game_dir, "paks", "Win64")
        rpaks = pak_search_order(paks, map_name, search_all)
        if not rpaks:
            raise GameImportError(f"No rpak found for {map_name} in {paks}")
        jobs = (("models", needed_models, have_models, models_dir), ("materials", needed_mats, have_mats, mats_dir))
    for what, needed, have, dest in jobs:
        for rp in rpaks:
            missing = needed - have
            if not missing:
                break
            out = _clear_legion_output(exe)
            yield f"Legion+: exporting the {what} of {os.path.basename(rp)}..."
            stage = _stage_dir(cache_root, game_dir)
            _clear_stage(stage)
            readable = _stage_rpak(rp, stage, log)
            proc = subprocess.Popen(_legion_cmd(exe, readable, what), cwd=os.path.dirname(exe))
            yield proc
            _clear_stage(stage)
            src_root = os.path.join(out, what)
            if not os.path.isdir(src_root):
                log(f"Legion+ exported no {what} from {os.path.basename(rp)}")
                continue
            yield f"Keeping the {what} the map uses..."
            for d in os.listdir(src_root):
                k = d.lower()
                if k in missing:
                    target = os.path.join(dest, d)
                    if os.path.isdir(target):
                        shutil.rmtree(target, ignore_errors=True)
                    os.replace(os.path.join(src_root, d), target)
                    have.add(k)
            log(f"{what}: {len(have & needed)}/{len(needed)} after {os.path.basename(rp)}")
        _clear_legion_output(exe)
    if kind == "full":
        info["models_missing"] = sorted(needed_models - have_models)
        info["materials_missing"] = sorted(needed_mats - have_mats)

    # 3b) R5Flowstate: models with RSX (Legion+ cannot read these paks) ----------------
    if kind != "full" and rsx_exe and os.path.isfile(rsx_exe):
        paks = os.path.join(game_dir, "paks", "Win64")
        order = [os.path.join(paks, n) for n in (f"{map_name}.rpak", "common_mp.rpak", "common.rpak",
                                                 "common_early.rpak")]
        order = [p for p in order if os.path.isfile(p)]
        rsx_dir = os.path.join(cache_root, "_tools", "rsx")
        out = os.path.join(rsx_dir, "out")
        for rp in order:
            missing = needed_models - have_models
            if not missing:
                break
            shutil.rmtree(out, ignore_errors=True)
            os.makedirs(out, exist_ok=True)
            yield f"RSX: exporting the models of {os.path.basename(rp)}..."
            proc = subprocess.Popen(_rsx_cmd(rsx_exe, rp, out), cwd=rsx_dir, creationflags=_NO_WINDOW,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            yield proc
            src_root = os.path.join(out, "mdl")
            if not os.path.isdir(src_root):
                log(f"RSX exported no model from {os.path.basename(rp)} (exit code {proc.returncode})")
                continue
            yield "Keeping the models the map uses..."
            for d in os.listdir(src_root):
                k = d.lower()
                if k in missing:
                    target = os.path.join(models_dir, d)
                    if os.path.isdir(target):
                        shutil.rmtree(target, ignore_errors=True)
                    os.replace(os.path.join(src_root, d), target)
                    have_models.add(k)
            log(f"models: {len(have_models & needed_models)}/{len(needed_models)} after {os.path.basename(rp)}")
        shutil.rmtree(out, ignore_errors=True)
        info["models_missing"] = sorted(needed_models - have_models)
        info["models_source"] = "rsx"
        # Models whose materials RSX could only name by hash ("0x1801F2AA2DDAE": no name in these paks) cannot
        # get their textures by name: they go to a last-resort folder, so that the same model from a Legion+
        # export or an R5Reloaded cache (textured) wins when there is one.
        yield "Sorting the models RSX could not name the materials of..."
        from . import castfile
        low = os.path.join(mdir, "_models_rsx")
        n_low = 0
        for d in os.listdir(models_dir):
            p = os.path.join(models_dir, d)
            casts = glob.glob(os.path.join(p, "*_LOD0.cast"))
            if not casts:
                continue
            try:
                cast = castfile.CastFile(casts[0])
                hashed = any(_HASH_NAME.match(cm.name or "") for cm in cast.materials.values())
                cast.free()
            except Exception:  # noqa: BLE001
                continue
            if hashed:
                os.makedirs(low, exist_ok=True)
                os.replace(p, os.path.join(low, d))
                n_low += 1
        info["models_hashed_materials"] = n_low
        log(f"{n_low} models with unnamed materials kept as a last resort")

    # 4) Textures -> shared pool -------------------------------------------
    yield "Sharing identical textures..."
    # material textures: remember their file names before moving them
    mat_files = {}
    for d in os.listdir(mats_dir):
        files = []
        for root, _dirs, fs in os.walk(os.path.join(mats_dir, d)):
            files += [f for f in fs if f.lower().endswith((".png", ".dds", ".tga"))]
        mat_files[d.lower()] = (d, files)
    _move_tree_into_pool(models_dir, pool)
    _move_tree_into_pool(mats_dir, pool)
    shutil.rmtree(mats_dir, ignore_errors=True)
    # materials no pak of this map holds: take their textures from the other cached maps
    lacking = sorted(needed_mats - set(mat_files))
    if lacking:
        others = [p for p in glob.glob(os.path.join(cache_root, "*", "*", "_shared_textures"))
                  if os.path.normcase(p) != os.path.normcase(pool)]
        found = 0
        for other in others:
            if not lacking:
                break
            names = os.listdir(other)
            for key in list(lacking):
                # "<material>_<slot>Texture.png": the slot part has no "_" (excludes longer material names)
                files = [f for f in names if f.lower().startswith(key + "_")
                         and "_" not in os.path.splitext(f)[0][len(key) + 1:]]
                if files:
                    for f in files:
                        if not os.path.exists(os.path.join(pool, f)):
                            shutil.copy2(os.path.join(other, f), os.path.join(pool, f))
                    mat_files[key] = (key, files)
                    lacking.remove(key)
                    found += 1
        if found:
            log(f"materials: {found} taken from other cached maps")
        if kind == "full":
            info["materials_missing"] = lacking

    # 4b) Textures the models reference but Legion+ did not export with them (they live in other paks,
    # usually the common ones): other cached maps first, then an image export of the searched paks.
    if kind == "full":
        # common paks first (shared textures), then the map's own pak and patches; not every family pak (a full
        # image export of a map pak takes minutes)
        own = re.compile(re.escape(map_name) + r"(\(\d+\))?\.rpak$", re.IGNORECASE)
        tex_paks = [p for p in rpaks if os.path.basename(p).lower().startswith("common")] + \
                   [p for p in rpaks if own.match(os.path.basename(p))]
        left = yield from _fill_missing_textures(models_dir, pool, cache_root, game_dir, tex_paks, exe, log)
        info["textures_missing"] = left

    # 5) World geometry -> .cast -------------------------------------------
    if geometry:
        yield "Writing the map geometry..."
        materials, mat_index, meshes = [], {}, []
        for name, pos, nrm, uv, col, tri in geometry:
            key = os.path.basename(name.replace("\\", "/")).lower()
            if key not in mat_index:
                folder, files = mat_files.get(key, (key, []))
                slots = {}
                for f in sorted(files):
                    stem = os.path.splitext(f)[0]
                    slot = stem[len(folder) + 1:] if stem.lower().startswith(folder.lower() + "_") else stem
                    slot = slot[:-7] if slot.lower().endswith("texture") else slot
                    slots.setdefault(slot or "albedo", f"_shared_textures/{f}")
                mat_index[key] = len(materials)
                materials.append((key, slots))
            meshes.append((key, pos, nrm, uv, col, tri, mat_index[key]))
        castwriter.write_static(os.path.join(mdir, f"{map_name}_LOD0.cast"), f"{map_name}_LOD0", meshes, materials)
    b.free()

    # 6) Entities ------------------------------------------------------------
    ents = []
    for suffix in ("script",):
        p = os.path.join(maps_dir, f"{map_name}_{suffix}.ent")
        if os.path.isfile(p):
            dst = os.path.join(mdir, os.path.basename(p))
            shutil.copy2(p, dst)
            ents.append(os.path.basename(dst))
    env = os.path.join(maps_dir, f"{map_name}_env.ent")
    if os.path.isfile(env) and _ent_sun_only(env, os.path.join(mdir, f"{map_name}_sun.ent")):
        ents.append(f"{map_name}_sun.ent")
    info["ents"] = ents
    shutil.rmtree(vdir, ignore_errors=True)      # BSP lumps are no longer needed
    info["size"] = cache_size(mdir)
    info["built"] = time.strftime("%Y-%m-%d %H:%M")
    with open(os.path.join(mdir, "r5_cache.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    yield "Cache ready"
    return mdir


def import_paths(mdir, map_name, info, export_dir=""):
    paths = [os.path.join(mdir, f"{map_name}_LOD0.mprt")]
    paths += [os.path.join(mdir, e) for e in info.get("ents", []) if os.path.isfile(os.path.join(mdir, e))]
    if info.get("kind") != "full" and export_dir:
        # props-only map: the world geometry of an existing Legion+ export of the same map
        cast = os.path.join(export_dir, f"{map_name}_LOD0.cast")
        if os.path.isfile(cast):
            paths.append(cast)
    return paths


def find_export_dir(map_name, hints=()):
    """A Legion+ export of the map: folder holding <map>_LOD0.mprt (+ .cast and _models)."""
    for h in hints:
        if not h:
            continue
        for d in (h, os.path.dirname(h.rstrip("\\/"))):
            if os.path.isfile(os.path.join(d, f"{map_name}_LOD0.mprt")) or \
                    os.path.isfile(os.path.join(d, f"{map_name}_LOD0.cast")):
                return d
    return ""


def models_sources(mdir, info, export_dir, cache_root):
    """Models folders, best first: the map's own cache, its Legion+ export, then every other cached map
    (R5Reloaded caches fill the models a Flowstate export lacks)."""
    out = []
    own = os.path.join(mdir, "_models")
    if os.path.isdir(own) and any(os.scandir(own)):
        out.append(own)
    if export_dir and os.path.isdir(os.path.join(export_dir, "_models")):
        out.append(os.path.join(export_dir, "_models"))
    for d in sorted(glob.glob(os.path.join(cache_root, "*", "*", "_models"))):
        if os.path.normcase(d) != os.path.normcase(own) and any(os.scandir(d)):
            out.append(d)
    low = os.path.join(mdir, "_models_rsx")          # RSX models without material names: last resort
    if os.path.isdir(low) and any(os.scandir(low)):
        out.append(low)
    return out


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------
class R5_OT_game_detect(bpy.types.Operator):
    """Find the R5Reloaded / R5Flowstate installs, Legion+ and revpk on this computer"""
    bl_idname = "r5.game_detect"
    bl_label = "Detect game & tools"

    def execute(self, context):
        s = context.scene.r5_settings
        games = find_game_dirs()
        if games and not (s.game_dir and os.path.isdir(bpy.path.abspath(s.game_dir))):
            full = [g for g in games if any(k == "full" for _n, k in list_maps(g))]
            s.game_dir = (full or games)[0]
        # Legion+ and revpk ship with the add-on: the fields stay empty (= bundled) unless a copy is missing
        if not bundled_tool("LegionPlus.exe") and not (s.legion_path and os.path.isfile(bpy.path.abspath(s.legion_path))):
            lg = find_legion()
            if lg:
                s.legion_path = lg[0]
        if not bundled_tool("revpk.exe") and not (s.revpk_path and os.path.isfile(bpy.path.abspath(s.revpk_path))):
            rv = find_revpk(bpy.path.abspath(s.game_dir) if s.game_dir else "")
            if rv:
                s.revpk_path = rv[0]
        if not (s.rsx_path and os.path.isfile(bpy.path.abspath(s.rsx_path))):
            rx = find_rsx()
            if rx:
                s.rsx_path = rx[0]
        _map_items_cache["key"] = None
        self.report({"INFO"}, f"{len(games)} install(s): {', '.join(os.path.basename(g) for g in games) or 'none'}")
        return {"FINISHED"}


class _GameModal:
    """Shared modal loop of the game operators: steps a generator that yields status texts or Legion+/revpk
    processes. Not an Operator itself: Blender cannot register an Operator that subclasses another registered
    Operator (the parent then loses its Python class and its button silently does nothing)."""
    _timer = None
    _gen = None
    _proc = None

    def _log(self, msg):
        print(f"[R5] game import: {msg}")
        R5_OT_import_game.status = msg

    def modal(self, context, event):
        if event.type == "ESC":
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill()
            self._finish(context)
            self.report({"WARNING"}, "Game import cancelled")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        if self._proc is not None:
            if self._proc.poll() is None:
                self._redraw(context)
                return {"RUNNING_MODAL"}
            self._proc = None
        try:
            step = next(self._gen)
        except StopIteration:
            self._finish(context)
            return self._done(context)
        except Exception as exc:  # noqa: BLE001
            self._finish(context)
            self._log(f"failed: {exc}")
            self.report({"ERROR"}, f"Game import failed: {exc}")
            return {"CANCELLED"}
        if isinstance(step, subprocess.Popen):
            self._proc = step
        else:
            self._log(step)
        self._redraw(context)
        return {"RUNNING_MODAL"}

    def _redraw(self, context):
        for area in context.screen.areas if context.screen else []:
            if area.type == "VIEW_3D":
                area.tag_redraw()

    def _finish(self, context):
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None


class R5_OT_import_game(_GameModal, bpy.types.Operator):
    """Import the chosen map straight from the game files (builds a cache the first time)"""
    bl_idname = "r5.import_game"
    bl_label = "Import map from game"

    status = ""

    def invoke(self, context, event):
        s = context.scene.r5_settings
        game_dir = bpy.path.abspath(s.game_dir) if s.game_dir else ""
        if not os.path.isdir(game_dir):
            self.report({"ERROR"}, "Choose the game folder (the one holding r5apex.exe)")
            return {"CANCELLED"}
        maps = dict(list_maps(game_dir))
        if s.game_map not in maps:
            self.report({"ERROR"}, "Choose a map")
            return {"CANCELLED"}
        self.map_name, self.kind = s.game_map, maps[s.game_map]
        self.game_dir = game_dir
        self.cache_root = bpy.path.abspath(s.game_cache_dir) if s.game_cache_dir else default_cache_dir()
        self.mdir = map_cache_dir(self.cache_root, game_dir, self.map_name)
        info = None if s.game_rebuild else cache_ready(self.mdir, self.map_name)
        if info is not None:
            return self._import(context, info)
        legion = bpy.path.abspath(s.legion_path) if s.legion_path else ""
        if not os.path.isfile(legion):
            legion = bundled_tool("LegionPlus.exe") or next(iter(find_legion()), "")
        if self.kind == "full" and not os.path.isfile(legion):
            self.report({"ERROR"}, "Legion+ (LegionPlus.exe) not found: set its path or press Detect")
            return {"CANCELLED"}
        revpks = ([bpy.path.abspath(s.revpk_path)] if s.revpk_path else []) + find_revpk(game_dir)
        revpks = [p for i, p in enumerate(revpks) if os.path.isfile(p) and p not in revpks[:i]]
        if not revpks:
            self.report({"ERROR"}, "revpk.exe not found (R5Reloaded SDK tool): set its path or press Detect")
            return {"CANCELLED"}
        rsx = bpy.path.abspath(s.rsx_path) if s.rsx_path else ""
        if not os.path.isfile(rsx):
            rsx = next(iter(find_rsx()), "") if self.kind != "full" else ""
        if self.kind != "full" and not rsx:
            self.report({"WARNING"}, "RSX (rsx_nogui.exe) not found: the map's own models cannot be exported, only "
                                     "the models of other cached maps are used")
        self._gen = build_cache(game_dir, self.map_name, self.kind, self.cache_root, legion, revpks, self._log,
                                search_all=s.game_search_all, rsx_exe=rsx)
        self._proc = None
        self._t0 = time.time()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)
        self._log(f"building the cache of {self.map_name} in {self.mdir}")
        return {"RUNNING_MODAL"}

    def _done(self, context):
        info = cache_ready(self.mdir, self.map_name)
        if info is None:
            self.report({"ERROR"}, "The cache could not be built")
            return {"CANCELLED"}
        self._log(f"cache built in {time.time() - self._t0:.0f}s ({info.get('size', 0) / 1e9:.1f} GB)")
        return self._import(context, info)

    def _import(self, context, info):
        from . import operators
        s = context.scene.r5_settings
        export_dir = ""
        if self.kind != "full":
            hint = bpy.path.abspath(s.game_export_dir) if s.game_export_dir else ""
            prev = bpy.path.abspath(s.models_dir) if s.models_dir and ";" not in s.models_dir else ""
            export_dir = find_export_dir(self.map_name, (hint, prev))
            if not export_dir and info.get("geometry_source") != "collision":
                self.report({"WARNING"}, f"{self.map_name}: this install has no world geometry for it. Set 'Legion+ "
                                         f"export' to an export of the map to get the ground and buildings")
        sources = models_sources(self.mdir, info, export_dir, self.cache_root)
        if not sources:
            self.report({"ERROR"}, "No models: import an R5Reloaded map first, or set 'Legion+ export'")
            return {"CANCELLED"}
        s.models_dir = ";".join(sources)
        s.ent_path = ""
        paths = import_paths(self.mdir, self.map_name, info, export_dir)
        try:
            created, n_models, missing = operators.import_map(context, paths,
                                                              report=lambda m: self.report({"INFO"}, m))
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"Import failed: {exc}")
            return {"CANCELLED"}
        R5_OT_import_game.status = (f"{self.map_name}: {created} props, {n_models} models"
                                    f"{f', {len(missing)} missing' if missing else ''}")
        self.report({"INFO"}, R5_OT_import_game.status)
        return {"FINISHED"}


class R5_OT_game_fetch_models(_GameModal, bpy.types.Operator):
    """Export the models the last import could not find (Blender text APX_missing_models) from any pak of the
    game: common paks first, then every map pak. Re-import the map afterwards"""
    bl_idname = "r5.game_fetch_models"
    bl_label = "Find missing models in the game"

    def invoke(self, context, event):
        s = context.scene.r5_settings
        game_dir = bpy.path.abspath(s.game_dir) if s.game_dir else ""
        if not os.path.isdir(os.path.join(game_dir, "paks")):
            self.report({"ERROR"}, "Choose the game folder first (R5Reloaded: its paks are read)")
            return {"CANCELLED"}
        self.keys = missing_models_from_text()
        if not self.keys:
            self.report({"INFO"}, "No missing model listed (text APX_missing_models)")
            return {"CANCELLED"}
        legion = bpy.path.abspath(s.legion_path) if s.legion_path else ""
        if not os.path.isfile(legion):
            legion = bundled_tool("LegionPlus.exe") or next(iter(find_legion()), "")
        if not os.path.isfile(legion):
            self.report({"ERROR"}, "Legion+ not found")
            return {"CANCELLED"}
        self.cache_root = bpy.path.abspath(s.game_cache_dir) if s.game_cache_dir else default_cache_dir()
        self._result = None

        def run():
            self._result = yield from fetch_models(game_dir, self.keys, self.cache_root, legion, self._log)
        self._gen = run()
        self._proc = None
        self._t0 = time.time()
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)
        self._log(f"looking for {len(self.keys)} models in the game paks")
        return {"RUNNING_MODAL"}

    def _done(self, context):
        left = self._result or []
        found = len(self.keys) - len(left)
        R5_OT_import_game.status = (f"{found}/{len(self.keys)} missing models found in {time.time() - self._t0:.0f}s"
                                    f" - re-import the map to use them")
        self.report({"INFO"}, R5_OT_import_game.status)
        if left:
            txt = bpy.data.texts.get("APX_missing_models")
            if txt is not None:
                txt.write("\n\nStill not found in any pak of this game:\n" + "\n".join(left))
        return {"FINISHED"}


class R5_OT_game_open_cache(bpy.types.Operator):
    """Open the cache folder of the chosen map"""
    bl_idname = "r5.game_open_cache"
    bl_label = "Open cache folder"

    def execute(self, context):
        s = context.scene.r5_settings
        root = bpy.path.abspath(s.game_cache_dir) if s.game_cache_dir else default_cache_dir()
        d = map_cache_dir(root, bpy.path.abspath(s.game_dir), s.game_map) if s.game_dir else root
        d = d if os.path.isdir(d) else root
        bpy.ops.wm.path_open(filepath=d)
        return {"FINISHED"}


def draw_game(col, s):
    """"From the game" part of the Import panel."""
    row = col.row(align=True)
    row.prop(s, "game_dir", text="Game")
    row.operator("r5.game_detect", text="", icon="VIEWZOOM")
    col.prop(s, "game_map", text="Map")
    gd = bpy.path.abspath(s.game_dir) if s.game_dir else ""
    root = bpy.path.abspath(s.game_cache_dir) if s.game_cache_dir else default_cache_dir()
    info = cache_ready(map_cache_dir(root, gd, s.game_map), s.game_map) if gd and s.game_map != "NONE" else None
    if info:
        col.label(text=f"Cached {info.get('built', '')}: {info.get('size', 0) / 1e9:.1f} GB", icon="CHECKMARK")
    elif gd and s.game_map != "NONE":
        col.label(text="First import: builds the cache (several minutes)", icon="TIME")
    if gd and s.game_map != "NONE" and dict(list_maps(gd)).get(s.game_map) == "props":
        # R5Flowstate: terrain from the collision data + models with RSX; an export gives the real geometry
        if not (s.rsx_path and os.path.isfile(bpy.path.abspath(s.rsx_path))):
            col.label(text="R5Flowstate: set RSX (Tools) for the map's models", icon="ERROR")
        col.prop(s, "game_export_dir", text="Legion+ export (optional)")
    row = col.row()
    row.scale_y = 1.3
    row.operator("r5.import_game", icon="IMPORT")
    if bpy.data.texts.get("APX_missing_models") is not None:
        col.operator("r5.game_fetch_models", icon="VIEWZOOM")
    if R5_OT_import_game.status:
        col.label(text=R5_OT_import_game.status, icon="INFO")


def draw_game_tools(col, s):
    if bundled_tool("LegionPlus.exe") and bundled_tool("revpk.exe"):
        col.label(text="Legion+ and revpk: bundled (fields = other copies)", icon="CHECKMARK")
    col.prop(s, "legion_path", text="Legion+")
    col.prop(s, "revpk_path", text="revpk")
    col.prop(s, "rsx_path", text="RSX")
    col.prop(s, "game_cache_dir", text="Cache")
    col.prop(s, "game_search_all")
    row = col.row(align=True)
    row.prop(s, "game_rebuild")
    row.operator("r5.game_open_cache", text="", icon="FILE_FOLDER")


CLASSES = (R5_OT_game_detect, R5_OT_import_game, R5_OT_game_fetch_models, R5_OT_game_open_cache)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        if getattr(c, "is_registered", False):
            bpy.utils.unregister_class(c)
