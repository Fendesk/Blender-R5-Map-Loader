# Blender R5 Map Loader 0.19.5 (Blender 4.2 to 5.3)

Loads R5Reloaded / Apex Legends map exports into Blender: map geometry, static and dynamic props, 3D skybox,
ziplines, POIs and the map's sun. Handles instancing, dynamic LODs, off-screen unloading, regions / POIs, sky,
city lights, movers / animated shaders and a Cycles setup.

## Install
Blender > Edit > Preferences > Get Extensions > ▾ (top right) > **Install from Disk…** >
`Blender-R5-Map-Loader-BETA-0.19.5.zip`.
Uninstall the previous version first, **restart Blender**, then import the map into an empty file.
The panels are in 3D View > Sidebar (N) > **R5 Map Loader**. The importer is also in File > Import.
While a map loads, a progress bar shows the current step in Blender's status bar.

## Load a map
Export the map with **r5mex** (`r5mex-gui.exe`), then pick the `.mprt` in **Import**:
```
mp_rr_desertlands_hu/
  mp_rr_desertlands_hu_LOD0.mprt     <- static prop placements (pick this file)
  mp_rr_desertlands_hu_LOD0.cast     <- map geometry (loaded automatically)
  mp_rr_desertlands_hu_skybox.mprt   <- 3D skybox (loaded automatically)
  _images/                            <- geometry textures
  _models/<model>/<model>_LOD0.cast
  entities/                           <- doors, loot bins, ziplines, POIs, rotators, lights
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
- **Regions / POI**: show only one or several POIs (or grid sectors). Everything outside is fully disabled.
- **Performance (LOD & culling)**: how many props are hidden right now, with *Show everything*; *Levels of
  detail*, *Unloading (FPS)* (outside the view, too small on screen, too far), *Update timing*, and *Scene
  tools* (viewport texture limit, 512 by default, needed to keep a full map within video memory). Props larger
  than *Always keep props larger than* (10 m) are never hidden by their size. Generated LODs (r5mex exports LOD0
  only) skip big models and architecture (*Never decimate*) and keep at least 25% of the faces.
- **Sky**: procedural sky, HDRI, in-game sky dome or cubemap, with *Sun*, *Atmosphere* and *Fog* sub-panels.
- **City lights**: creates lights on lamps, signs and screens, per type (point / spot / area), with the map's
  light colours.
- **Animation**: map rotators, fans and turbines turn; water, lava, screens, energy and foliage animate.
  Off by default: start them once the map is loaded, then play the timeline. *Speeds* and *Foliage wind* below.
- **Camera & Render**: **New camera from view** creates a camera exactly where the 3D view looks from (position,
  angle, focal length) and makes it the active camera; the button next to it moves the active camera there
  instead (also in View > Cameras). Cameras go in the *R5 Cameras* collection and stay when the map is replaced.
  *Cycles preview* / *Cycles final* presets, camera-based LOD, smooth LOD transitions in animations.

## Tips
- Use **Geometry Nodes** instancing for a full map; *Empties* only for *Import an area only*.
- For fast navigation, use Solid mode with *Color: Texture*.
- Decals are invisible in Solid mode: they only show in Material Preview / Rendered.
- The add-on turns on *Render > Lock Interface*: it keeps animation renders of big maps stable.
- The 3D skybox is a backdrop, as in game: it casts no shadow and blocks no light, and its sky dome is drawn
  unlit. The map is lit by the sun and the world (*Sky* panel).
- Scale: 1 game unit = 0.0254 m.
