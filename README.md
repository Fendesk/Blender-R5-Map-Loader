# Blender R5 Map Loader 0.12.0 (Blender 4.2+)

Loads R5Reloaded / Apex Legends maps into Blender, **straight from the game install** or from Legion+ exports:
map geometry, static props, dynamic props, points of interest and the map's sun. The add-on handles instancing,
dynamic LODs, unloading props you cannot see, regions / POIs, the sky and a Cycles setup.

## Install
Blender > Edit > Preferences > Get Extensions > ▾ (top right) > **Install from Disk…** >
`Blender-R5-Map-Loader-BETA-0.12.0.zip`.
Uninstall the previous version first and **restart Blender**, then load the map into an empty file (or re-import
it: materials from older versions are rebuilt).
Nothing else to install: Legion+ and revpk come with the add-on (see *Bundled tools*).
The old *Apex Legends Map Importer* (`apex_map_importer`) can stay installed: since 0.5.1 the two add-ons no longer
share operator / panel / property names, so each one can be disabled or uninstalled on its own.
The panels live in the 3D View > Sidebar (N) > **R5 Map Loader** tab. The importer is also in File > Import.

-----------------------------------------------------------------------------
# PAST PATCHNOTES

## What's new in 0.12.0
- **City lights**:
  - **light type per prop type**: point, spot (cone, with angle and blend) or area (soft panel, with shape and
    spread). Changes apply live to the lights already created.
  - **Light quality improvement**: fix some weird lights that clip in the geometry.

## What's new in 0.11.0
- **Import panel**: *From files* comes first; *From the game* is marked **Beta** (experimental: models,
  textures or terrain may be incomplete).
- **City lights** (new panel): *Find light sources* sorts the props into types (street lamps, floodlights,
  signs & screens, interior & wall lights, other glowing props); *Create lights* puts a point light at the glowing
  part of each one. Per type, live: on/off, power, colour (or each model's emissive colour), radius, shadows,
  **glow of the emissive textures**, max number of lights (closest to the 3D cursor first).
- **Prop materials in the Material tab**: clicking a prop selects the object that draws every copy of its model;
  it now carries the model's materials, so the Material tab shows them (editing one changes every copy).
  *Visibility > Prop models > Show prop materials* does it for maps imported with an older version.

## What's new in 0.10.0
- **One Import panel**: *From the game* / *From files* switch at the top; every option of the two former panels
  is still there, in the sub-panels (*Tools & cache*, *Entities*, *Map geometry & ziplines*, *Materials & meshes*,
  *Options*).
- **R5Flowstate maps from the game**:
  - **terrain and buildings rebuilt from the collision data** of the server BSP (the only geometry Flowstate ships:
    the add-on decodes the collision BVH, ~2.7 million triangles on World's Edge). No UVs exist in that data:
    textures are box-mapped (512 units per tile); a Legion+ export of the map, when given, replaces it with the
    real render geometry.
  - **the map's own props** (432k static props on World's Edge) with their **models exported by RSX**
    (`rsx_nogui.exe`, your copy: *Tools & cache > RSX*, found by 🔍). RSX is the only tool reading the Flowstate
    paks. Its textures cannot be exported from the command line (it crashes on the Flowstate paks): textures are
    found **by material name** in the R5Reloaded caches and in Legion+ exports.
- **White props fixed**: materials with a plain white / light grey albedo (painted metal, tarps, velvet...) are
  tinted by the model's vertex colours, as in game (`silver_metal_panel_01`, `building_walls_metal_03`...).
- **Sky**: the sun elevation / rotation sliders are always shown. They start from the map's sun; moving them
  switches to a manual sun.
- Textures missing from an export are looked up by material name in the other exports / caches.

## What's new in 0.9.0
- **More complete maps from the game**: models and materials are looked for in the map's patch paks
  (`map(01).rpak`…), the paks of the same map family (a season map such as `mp_rr_canyonlands_mu1` reuses the
  base `mp_rr_canyonlands_64k_x_64k`) and the common paks. Kings Canyon's terrain sections are props: they were
  missing, leaving holes in the ground. The models of the dynamic props (`_script.ent`) are looked for as well.
- **Missing textures**: textures a model uses but stores in another pak are taken from the other cached maps, then
  exported from the common paks.
- Option *Search every map pak* (slow) for what is still missing.
- **ReMap custom maps (`.nut`)**: see *ReMap maps*.
- **Find missing models in the game**: looks for the models of the last import that were not found, in every pak.

## What's new in 0.8.0
- Legion+ and revpk are **bundled**: importing from the game needs nothing else than the game.
- revpk now works from any folder (it splits its own path on spaces: the add-on runs a copy from a folder without
  spaces). An R5Reloaded install alone is enough.
- 0.7.1: installing over a loaded version no longer mixes old and new code (error "no attribute 'game_dir'").

## What's new in 0.7.0
- **Import from game** panel: pick an R5Reloaded map in a list and import it, no manual export. See below.
-----------------------------------------------------------------------------


## Import from game
Panel **Import**, *From the game (Beta)* (top of the R5 Map Loader tab):
1. Press 🔍 (*Detect game & tools*): finds the R5Reloaded / R5Flowstate installs (and RSX).
2. Choose the map, press **Import map from game**.

The *Legion+* / *revpk* fields are optional: empty = the bundled copies.

The first import of a map builds a cache (5–10 min for World's Edge, Blender stays usable, Esc cancels):
- revpk unpacks the map VPK (`.bsp`, its lumps and the `.ent` files, LZHAM-compressed).
- The add-on reads the BSP itself: world geometry (model 0, grouped by material) and the static props.
- Legion+ (command line, private copy inside the cache) exports the models and materials of the map's rpak, then
  `common_mp.rpak` for what is missing. zstd-compressed R5Reloaded paks (Legion+ crashes on them) are decompressed
  into a temporary copy, with hard links to the game's `.starpak` files; the game files are never modified.
- Only what the map uses is kept, and identical textures are stored once (`_shared_textures`): ~10 GB for
  World's Edge instead of ~47 GB. Materials no pak of the map holds are taken from the other cached maps.
- `*_script.ent` gives the dynamic props, ziplines and POIs; `light_environment` from `*_env.ent` gives the
  **map's real sun**.

Next imports of the same map reuse the cache (*Rebuild cache* forces a new extraction). The cache folder can be set
(several GB per map); by default it is in the add-on's user folder.

Measured on R5Reloaded LIVE World's Edge (`mp_rr_desertlands_64k_x_64k`): cache 5 min 15 s, 10 GB; import 6 min;
456k props, 1362 models, 400 geometry materials, 80 POIs. Props sit exactly on the geometry.

**R5Flowstate (`*_hu` maps)**: only server VPKs are shipped, and the paks use Oodle compression that Legion+ cannot
read. The add-on:
- reads the static props, entities and sun from the server BSP / VPK;
- rebuilds the terrain and buildings from the **collision data** (box-mapped textures);
- exports the map's models with **RSX** (`rsx_nogui.exe`, set in *Tools & cache* or found by 🔍);
- finds the textures by material name in the R5Reloaded caches (import an R5Reloaded map first: World's Edge
  shares most of its materials with `mp_rr_desertlands_64k_x_64k`) and in the *Legion+ export* if one is given;
- with a *Legion+ export* of the same map, uses its real render geometry instead of the collision terrain.

Not imported yet: brush entities of the BSP (some doors / movers), the skybox model.

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
- `*_env.ent` and `*_fx.ent` are LZHAM-compressed in those VPKs: this field cannot read them, *Import from game*
  can (it uses revpk).
- Models that are not in `_models` are listed in the Blender text `APX_missing_models`. Export them with RSX.
- The *Models* field accepts several folders separated by `;` (the first folder holding a model wins).

## Panels
- **Visibility**: hide or show these groups. Everything except the game itself is hidden by default.
  - triggers
  - tool brushes (clip, blocklight, skybox…)
  - **light shafts & atmosphere volumes**: godray / atmosphere props, which show up as big opaque ellipses or cones
  - **particle / mist cards**: crossed planes with a mist or steam texture that are animated particles in game, e.g.
    `vent_climatizer_mesh_idle02` (~27 × 30 m of mist next to the cloud-seeding climatizer vents) or the
    `fog_all` sheets of the distant scenery. When shown, they render as a soft additive mist.
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
  Right after an import, the first update can take ~20 s while Blender prepares the display.
- **Sky**: four sources, then sun, atmosphere and fog. Once *Apply sky* has been pressed, every value updates live.
  - Procedural sky + sun. If an uncompressed env `.ent` with `light_environment` was loaded, the map's own sun is used.
  - A panorama / HDRI.
  - An **in-game sky dome**: square image with a round sky, zenith in the centre and the horizon on the circle
    (Apex skybox textures, .png/.jpg/.webp...). The disc is found automatically. Projection (equidistant by default),
    horizon radius, rotation and mirror update live. Below the horizon, the image's background colour is used.
    A square image picked in *Panorama* mode switches to this mode by itself.
  - A 6-face cubemap: `name_000..005`, `px/nx/py/ny/pz/nz` or `right/left/up/down/front/back`, for example a sky
    exported with RSX. It is converted to a panorama.
  - **Sun**: elevation, rotation, strength and **colour**. *Reset* returns to the map's sun colour
    (`light_environment`) when one was loaded, otherwise warm white.
  - **Atmosphere** (image skies): Blender's physical sky blended over the image, strongest towards the horizon.
    *Air (gas)* = blue sky / orange sunsets, *Dust / haze* = whitish haze and sun glow, *Ozone* = deeper blue.
    The procedural sky uses the same three densities directly.
  - **Volumetric fog**: a volume box around the map (density, colour, anisotropy, EEVEE range). A world volume is
    not used: EEVEE treats it as infinite and it absorbs the whole sky and sun. Fog makes renders heavier,
    especially in Cycles.
- **Render (Cycles)**: *Preview* and *Final* presets.
  - Uses the GPU when one is enabled in Preferences > System > Cycles Render Devices.
  - Sets denoising, adaptive sampling and enough transparent bounces for foliage.
  - Limits the texture size.
  - Uses camera-based LOD without field-of-view culling, so off-screen props still cast shadows.

## Video memory
A full map has ~4,400 unique textures. On import (and when a saved map is opened) the add-on lowers Blender's
viewport texture limit to *Optimization > Textures* (512 by default; it never raises a lower one). Measured on
World's Edge with a 16 GB RTX 5080:

| Limit | Solid | Material Preview | Video memory |
|---|---|---|---|
| none | 2.7–8 fps | 0.2 fps, 12 s frames, driver reset (TDR) | > 16 GB |
| 1024 | 35 fps | 8.8 fps | 13.6 GB |
| 512 | 38 fps | 11.9 fps | 6.6 GB |

The textures cost video memory and shading time whatever the instancing mode: Geometry Nodes only place the props
and pick their LOD, the materials (shader nodes) display the textures. For fast navigation, use Solid mode with
*Color: Texture* (one texture per material, no shader).

*Empties* instancing on a full map (433k objects) updates its LODs but Blender draws ~1 fps with that many objects:
use Geometry Nodes for a full map and keep Empties for *Import an area only*.

## ReMap maps (.nut)
Custom maps made with ReMap / the R5Reloaded map editor are Squirrel scripts (`mp_rr_remap(name).nut`). Import one
with the regular **Import** (File > Import > R5 Map Loader, or the Import panel) and pick the `.nut`:
- every `MapEditor_CreateProp( $"mdl/...rmdl", <origin>, <angles>, ..., scale )` becomes a prop, with its scale;
  other calls taking a model and two vectors (`MapEditor_CreateMovingProp`...) too, at their start position;
- `MapEditor_CreateZiplineFromUnity` gives zipline cables;
- buttons, triggers, text panels and script logic are ignored.

The models come from the maps already cached by *Import from game* (plus the *Models* folder if set). If some are
listed in `APX_missing_models`, press **Find missing models in the game** (Import from game panel): they are
exported from the common paks, then from every map pak, into a shared `_extra` cache. Then import the `.nut` again.

To see the custom map on its base map, import the base map first (Import from game), then the `.nut` in the same
file: both use the same coordinates and recentering offset.

## Bundled tools
The `tools` folder of the add-on holds two separate, unmodified programs. The add-on only runs them; they keep their
own licences (texts included in `tools/`, details in `tools/README.txt`):
- **Legion+ 1.5.1** (`LegionPlus.exe`, by DTZxPorter / r-ex): GNU GPL v3, source at
  https://github.com/r-ex/LegionPlus. The proprietary RAD Game Tools DLLs of Legion+ releases are not included (not
  needed for models, materials and textures).
- **revpk** (`revpk.exe`, R5Reloaded SDK): Source 1 SDK License (Valve), redistributable free of charge only, with
  its licence and third-party notices. Source at https://github.com/Mauler125/r5sdk.

Because of these Windows executables the add-on is marked `windows-x64`.

## Notes
- Map geometry is split *one object per material* by default. The draw cost matches a single object, and regions
  only load the materials in view. Measured on a region render: 6 GB of memory instead of 17 GB.
- The textures come from the `.cast` files (materials and image paths), never from the `.mprt` or the BSP.
- Legion+ names the texture slots by position (albedo, normal, gloss, specular, emissive, ao, cavity), whatever
  the shader. The add-on trusts the file names first (`_albedo2Texture`, `_opacityMultiplyTexture`...), then the
  position, with the blend-material layout `[albedo, albedo2, normal, normal2, gloss, gloss2, spec]` for `_bm`.
- Blend materials (`_bm`) show the layer stored in their `albedo` slot. Legion+ exports both layers but not the
  vertex colours that blend them, so some ground shows its second layer (snow) where the game shows grass or moss.
- Ice (`desertlands_ice_03_wld_dark/bright…`): the albedo is a flat dark grey, the look comes from the game's ice
  shader. The add-on rebuilds it from the material's ice colour texture, gloss mask and ice normal map (light blue,
  glossy, a little subsurface and a clear coat). The snow that covers part of the spikes in game is not shown.
- Decals: the transparency comes from the `opacityMultiply` texture. Solid mode cannot apply it, so decals that use
  it are invisible in Solid and only appear in Material Preview / Rendered.
- Textures are read with retries: external drives sometimes report existing files as missing. A material whose
  albedo could not be read is rebuilt on the next import.
- Scale: 1 game unit = 0.0254 m. Rotation: XYZ Euler = (roll, pitch, yaw).
