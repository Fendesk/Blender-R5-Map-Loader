# Blender R5 Map Loader 0.21.0

Loads R5Reloaded / Apex Legends map exports into Blender: map geometry, static and dynamic props, 3D skybox,
ziplines, POIs and the map's sun. Handles instancing, dynamic LODs, off-screen unloading, regions / POIs, sky,
city lights, movers / animated shaders and a Cycles setup.

##  B3D version and hardware spec recommendations
Br5ML is working on every versions of B3D from 4.2 to the latest 5.3 release including LTS versions.
This add-on require a high end device : loading an r5 map takes a lot of computing ressource. Having the _RECOMMENDED_ system specs is a must have.


System Requirements | MINIMUM | RECOMMENDED
--- | --- | ---
VRAM | 6 GB | 16 GB
RAM | 32 GB | 64 GB
CPU | 6 cores | 10 cores

<ins> **For RTX 50 Blackwell users ONLY:** </ins>\
I recommend using the latest 5.3 version since they integrates DLSS 4.5 Ray Reconstruction as a new denoiser.
This will help your pc to load and move in the viewport rendered scene so much faster and smoother than
using OptiX or OID (it's more efficient if you're working on huge scene like full Apex maps).
> [!CAUTION]
> B3D DLSS 4.5 require to use the latest 621.40 NVIDIA Driver release.\
> DO NOT USE ANY PRE 621.40 VERSION\
> They have an excessive SSD-writing bug that can damage your SSD.

## Install
Blender > Edit > Preferences > Get Extensions > ▾ (top right) > **Install from Disk…** >
`Blender-R5-Map-Loader-0.21.0.zip`.
Uninstall the previous version first, **restart Blender**, then import the map into an empty file.
The panels are in 3D View > Sidebar (N) > **R5 Map Loader**. The importer is also in File > Import.
While a map loads, a progress bar shows the current step in Blender's status bar.

## Load a map
Use **r5mex** (coming soon...) or [Legion+](https://github.com/r-ex/LegionPlus) and export the map in `.mprt` format.

> [!WARNING]
Using Legion+ is not a good idea since the tool is deprecated.\
This can result in missing files and broken map import.

```
mp_rr_desertlands_hu/
  mp_rr_desertlands_hu_LOD0.mprt     <- static prop placements (pick this file)
  mp_rr_desertlands_hu_LOD0.cast     <- map geometry (loaded automatically)
  mp_rr_desertlands_hu_skybox.mprt   <- 3D skybox (loaded automatically)
  _images/                           <- geometry textures
  _models/<model>/<model>_LOD0.cast
  entities/                          <- doors, loot bins, ziplines, POIs, rotators, lights
```
- **Replace the loaded map**: the button under *Import* (or the option in the file browser) removes the map
  already in the file before importing the new one. Sky and render settings are kept; Ctrl+Z brings it back.
- **One POI only**: after selecting the `.mprt`, pick a POI in *Import only one POI* (file browser sidebar, N).
  Only the props and the map geometry inside it are imported (*Radius x* enlarges the area).
- Models not found in `_models` (*missing*) and model files that could not be read (*import failed*) are listed
  in the Blender text `APX_missing_models`. Exports with paths longer than 259 characters load too.
- The *Models* field accepts several folders separated by `;`.
- Materials exported without textures take them by name from the other exports next to this one
  (*Textures from other exports*, Map > Import settings > Materials & textures): keep your exports side by side (`maps-export-main/<season>/<map>`).
  Retail terrain layers get theirs from the blend materials of the same export.
- Legion+ / RSX exports also load (without entities, vertex colours or skybox).
- ReMap maps (`.nut`): pick the `.nut` in the same importer, with a *Models* folder holding their props. Import the
  base map first to see both together.

## Panels
In the order of the work, most used buttons on top, rarely used settings in closed sub-panels:
- **Map**: *Models* folder, instancing, **Import** and *Replace the loaded map*. *Import settings* (closed)
  groups everything applied to the next import: geometry & ziplines, entities, materials & textures, meshes,
  LOD generation, placement & filters.
- **Display**: show / hide triggers, tool brushes, light shafts, mist cards, barriers, dev textures, and each
  prop model (*Prop models*).
- **Regions / POI**: show only one or several POIs (or grid sectors). Everything outside is fully disabled,
  except the 3D skybox, which stays around the region as in game.
- **Performance (LOD & culling)**: how many props are hidden right now, with *Show everything*; *Levels of
  detail*, *Unloading (FPS)* (outside the view, too small on screen, too far), *Update timing*, and *Scene
  tools* (viewport texture limit, 512 by default, needed to keep a full map within video memory). Props larger
  than *Always keep props larger than* (10 m) are never hidden by their size. Generated LODs (r5mex exports LOD0
  only) skip big models and architecture (*Never decimate*) and keep at least 25% of the faces.
- **Sky**: procedural sky, HDRI, in-game sky dome or cubemap, with *Sun*, *Atmosphere* and *Fog* sub-panels.
  *Fog* is a volumetric height fog lit by the sun and the sky: dense at its *Base height*, thinning out over its
  *Thickness* down to *Above the layer*, broken into drifting *Patches* (they move with the *Wind* when the
  timeline plays), with light shafts in EEVEE. Switched on the first time, it starts from the map's own fog
  (the `env_fog_controller` of the export: colour, density, heights); *Use the map's fog* sets it back.
- **City lights**: creates lights on lamps, signs and screens, per type (point / spot / area), with the map's
  light colours.
- **Effects & water**: *Map effects* (on by default) draws the effects the map places (its
  `info_particle_system` entities): sparks & fireworks, electricity, fire & lava embers, steam & smoke, water
  spray, snow, fog & mist, dust & pollen, insects, blinking lights, glows. The game's particle systems are not in
  the exports: each effect is recognised by its name and drawn by a procedural particle system that moves when the
  timeline plays. *Effect types* (closed) shows / hides each kind, with its amount, size, strength and colour.
  *Falling leaves* makes leaves fall from every tree of the map (in the colour of its foliage), plus the leaf and
  petal effects of the map. *Water plane* adds a large water surface around the map, with animated waves
  (swell, chop and ripples, in metres: *Waves* scales them); *Detect* puts it at the level of the map's own water.
- **Player banners**: banner images (PNG, JPG...) on the flex screens of the maps
  (`survival_modular_flexscreens`: the tall screens of E-District, Olympus, World's Edge, Storm Point). Add one
  image or several: each screen gets one at random (or the selected one everywhere), filling the screen without
  stretching, readable from both sides. *Glow* sets their brightness. Kept for the next imports.
- **Animation**: map rotators, fans and turbines turn; water, lava, screens, energy and foliage animate.
  Off by default: start them once the map is loaded, then play the timeline. *Speeds* and *Foliage wind* below.
- **Camera & Render**: **New camera from view** creates a camera exactly where the 3D view looks from (position,
  angle, focal length) and makes it the active camera; the button next to it moves the active camera there
  instead (also in View > Cameras). Cameras go in the *R5 Cameras* collection and stay when the map is replaced.
  *Cycles preview* / *Cycles final* presets, camera-based LOD, smooth LOD transitions in animations.

> [!TIP]
> - Use **Geometry Nodes** instancing for a full map; *Empties* only for *Import an area only*.
> - For fast navigation, use Solid mode with *Color: Texture*.
> - Decals are invisible in Solid mode: they only show in Material Preview / Rendered.
> - The add-on turns on *Render > Lock Interface*: it keeps animation renders of big maps stable.
> - The 3D skybox is a backdrop, as in game: it casts no shadow and blocks no light, and its sky dome is drawn
  unlit. The map is lit by the sun and the world (*Sky* panel).
> - Scale: 1 game unit = 0.0254 m.
