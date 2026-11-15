# Blender R5 Map Loader (Blender 4.2+)

Loads R5Reloaded / Apex Legends maps into Blender: map geometry (`.cast`), static props (`.mprt`),
dynamic props and points of interest (`.ent`, read straight from R5Reloaded VPKs). The add-on handles
instancing, dynamic LODs, unloading props you cannot see, regions / POIs, the sky and a Cycles setup.

## Install
Blender > Edit > Preferences > Get Extensions > ▾ (top right) > **Install from Disk…** > `r5_map_loader-0.5.3.zip`.
Uninstall the previous version first, then load the map into an empty file.
The old *Apex Legends Map Importer* (`apex_map_importer`) can stay installed: since 0.5.1 the two add-ons no longer
share operator / panel / property names, so each one can be disabled or uninstalled on its own.
The panels live in the 3D View > Sidebar (N) > **R5 Map Loader** tab. The importer is also in File > Import.

## Load a map
```
World Edge/
  mp_rr_desertlands_hu_LOD0.mprt   <- static prop placements (pick this file)
  mp_rr_desertlands_hu_LOD0.cast   <- map geometry (loaded automatically)
  _images/                          <- geometry textures
  _models/<model>/<model>_LOD0.cast
```
**Entities**: set **Import > Entities** to the map's *server* VPK, for example
`R5Flowstate\vpk\englishserver_mp_rr_desertlands_hu.bsp.pak000_dir.vpk`.
- `*_script.ent` adds doors, loot bins, zipline cables and the points of interest.
- `*_env.ent` and `*_fx.ent` are LZHAM-compressed in those VPKs and cannot be read.
- Models that are not in `_models` are listed in the Blender text `APX_missing_models`. Export them with RSX.

## Panels
- **Visibility**: hide or show these groups. Everything except the game itself is hidden by default.
  - triggers
  - tool brushes (clip, blocklight, skybox…)
  - **light shafts & atmosphere volumes**: godray / atmosphere props, which show up as big opaque ellipses or cones
  - game mode barriers
  - dev textures

  *Prop models* lists every model with its number of placements. Each one can be hidden in the viewport or the
  render, or filtered by name (for example `godray`, `sphere`).
- **Regions / POI**: show only part of a loaded map.
  - The POIs are created from the entity files (`zone_6_fragment`, `zone_9_harvester`…, 68 on World's Edge).
  - You can also add grid sectors (A1, B1…) or POIs at the 3D cursor.
  - *Show only this POI* hides everything outside a disc. Objects outside are fully disabled, so EEVEE and Cycles do
    not even load their textures.
  - *Whole map* brings everything back. Nothing is re-imported.
- **LOD & streaming**: the LODs can be switched off. Props are also unloaded when they are outside the field of
  view or too small on screen. Recomputing happens when the view stops moving, so navigation stays smooth.
- **Sky**: three sources.
  - Procedural sky + sun. If an uncompressed env `.ent` with `light_environment` was loaded, the map's own sun is used.
  - A panorama / HDRI.
  - An **in-game sky dome**: square image with a round sky, zenith in the centre and the horizon on the circle
    (Apex skybox textures, .png/.jpg/.webp...). The disc is found automatically. Projection (equidistant by default),
    horizon radius, rotation and mirror update live. Below the horizon, the image's background colour is used.
    A square image picked in *Panorama* mode switches to this mode by itself.
  - A 6-face cubemap: `name_000..005`, `px/nx/py/ny/pz/nz` or `right/left/up/down/front/back`, for example a sky
    exported with RSX. It is converted to a panorama.
- **Render (Cycles)**: *Preview* and *Final* presets.
  - Uses the GPU when one is enabled in Preferences > System > Cycles Render Devices.
  - Sets denoising, adaptive sampling and enough transparent bounces for foliage.
  - Limits the texture size.
  - Uses camera-based LOD without field-of-view culling, so off-screen props still cast shadows.

## Video memory
A full map has ~4,400 unique textures. On import (and when a saved map is opened) the add-on lowers Blender's
viewport texture limit to *Optimization > Textures* (512 by default; it never raises a lower one). Measured on
World's Edge with a 16 GB RTX 5080, Material Preview:

| Limit | Solid | Material Preview | Video memory |
|---|---|---|---|
| none | 2.7–8 fps | 0.2 fps, 12 s frames, driver reset (TDR) | > 16 GB |
| 1024 | 35 fps | 8.8 fps | 13.6 GB |
| 512 | 38 fps | 8.7 fps | 6.2 GB |

*Empties* instancing on a full map (World Edge = 433k objects) updates its LODs but Blender draws ~1 fps with that many objects:
use Geometry Nodes for a full map and keep Empties for *Import an area only*.

## Notes
- Map geometry is split *one object per material* by default. The draw cost matches a single object, and regions
  only load the materials in view. Measured on a region render: 6 GB of memory instead of 17 GB.
- The textures come from the `.cast` files (materials and image paths), never from the `.mprt` or the BSP.
- Legion+ names the texture slots by position (albedo, normal, gloss, specular, emissive, ao, cavity), whatever
  the shader. The add-on trusts the file names first (`_albedo2Texture`, `_opacityMultiplyTexture`...), then the
  position, with the blend-material layout `[albedo, albedo2, normal, normal2, gloss, gloss2, spec]` for `_bm`.
- Blend materials (`_bm`): both layers are exported, but not the vertex colours that blend them (all white), so the
  first layer is shown.
- Decals: the transparency comes from the `opacityMultiply` texture. Solid mode cannot apply it, so decals that use
  it are invisible in Solid and only appear in Material Preview / Rendered.
- Textures are read with retries: external drives sometimes report existing files as missing. A material whose
  albedo could not be read is rebuilt on the next import.
- Scale: 1 game unit = 0.0254 m. Rotation: XYZ Euler = (roll, pitch, yaw).
