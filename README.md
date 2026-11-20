# Blender R5 Map Loader 0.15.0 (Blender 4.2 to 5.3)

Loads R5Reloaded / Apex Legends map exports into Blender (RSXreloaded, Legion+ or RSX): map geometry, static props,
dynamic props, points of interest and the map's sun. The add-on handles instancing, dynamic LODs, unloading props
you cannot see, regions / POIs, the sky, city lights and a Cycles setup.

## Install
Blender > Edit > Preferences > Get Extensions > ▾ (top right) > **Install from Disk…** >
`Blender-R5-Map-Loader-BETA-0.15.0.zip`.
Uninstall the previous version first and **restart Blender**, then load the map into an empty file (or re-import
it: materials from older versions are rebuilt).
The old *Apex Legends Map Importer* (`apex_map_importer`) can stay installed: since 0.5.1 the two add-ons no longer
share operator / panel / property names, so each one can be disabled or uninstalled on its own.
The panels live in the 3D View > Sidebar (N) > **R5 Map Loader** tab. The importer is also in File > Import.

-----------------------------------------------------------------------------
# PAST PATCHNOTES

## What's new in 0.15.0
Re-import the maps (into a new file): the materials are rebuilt.
- **3D skybox** (`<map>_skybox.mprt`, r5mex exports). It loads with the `.mprt` (*3D skybox*, Import > Map geometry,
  on by default) or on its own. It is the distant scenery around the map, at the size and place the game shows it
  (E-District: the `district_se` city, scaled x1000, up to ~35 km away). Its props stay in LOD0 and stay visible
  whatever the culling and the shown POIs. The clip end of the 3D views and of the camera is raised to reach it.
- **Movers follow the game.** Static props never move in game: what turns is driven by the map's rotators
  (`script_mover_lightweight` / `script_rotator`: axis, speed in degrees per second).
  - Every part of a rotator turns around the same pivot and axis. That covers its linked brush models and linked
    props, and the props placed on them: the koi sign of E-District (sign and frame turn as one, 3°/s) and the four
    big fans of Fan Town (10 and 20°/s, around their horizontal axis).
  - Fans, turbine blades and rings found by name now turn around their measured axis of rotational symmetry.
    Models without one no longer turn.
  - The parts of the Olympus rotating building share one vertical axis.
  - Every spinning part uses the same phase, so the parts of one machine stay aligned.
  - Banners and flags no longer swing as rigid boards. They ripple with the shader wind (*Start shaders*), like
    the vertex-animated cloth of the game. Zipline balloons no longer bob.
- **E-District launchers**: the tilted tube drawn on every launcher was the editor model of the launcher's
  script entity (`script_ref`, not drawn in game). It is no longer imported. The launcher beams and rings, the zipline
  magnets and the holograms (lotus, statues, mall holograms, star drips) now glow: a see-through additive light
  in their vertex colour, faded by their opacity texture and vertex alpha.
- **Lights have their own colours.** Many lamps, neon tubes and holograms have a white emissive texture that the
  game tints per vertex. The vertex colour now tints their glow too. *City lights* take each light's colour from
  the nearest light entity of the map (`light` / `light_spot` in the env `.ent`, 15,700 on E-District), or from
  the glowing part's vertex colour. Before, every light of a model had the same colour.

## What's new in 0.14.0
Re-import the maps (into a new file) to get these fixes: models already in a file keep their old orientation.
- **Loot bins, Tridents, MRVNs, replicators, elevator doors... stand the right way.** Models with a real skeleton
  are exported Y-up (Maya axes), unlike static props. They are now turned to Z-up, forward +X. Measured on Olympus:
  the loot bin is 1.65 m high with *SUPPLIES* readable, the Trident 2.4 m high and 7 m long, nose forward.
- **No more random props at the world origin.** The map `.cast` also holds the BSP brush models (breakable glass,
  `func_brush` signs, fog volumes, triggers), exported around the origin. They are now moved to the position of
  their entity (`"model" "*N"`), read from every `.ent` file of the `entities` folder.
- **The map sits above Z = 0** (*Map above Z = 0*, Import > Options, on by default). The map is moved so that its
  bottom is on the ground grid. A few disconnected pieces far below the map are left below it: the skybox room,
  test rooms and distant backdrops (Olympus has some 400 to 700 m under its lowest island).
- **Olympus energy dome**: the storm dome was a plain grey shell. Its material keeps the electric pattern in an
  unnamed texture slot. That pattern now drives a see-through glowing material, as do the other energy materials
  (`*storm*`, `*energy*`, `*electric*`: electric rings, zipline magnets, hover pads).
- **Movers / Shaders** (new panel), switched on after loading the map:
  - *Start movers*: fans, turbine blades, rotating rings and buildings spin; banners and flags sway; zipline
    balloons and Tridents bob. Each moving model gets a second Geometry Nodes modifier (`APX_Mover`), so only
    those objects are recomputed on each frame. *Movers speed* changes them all (negative = reverse). Geometry
    Nodes instancing only.
  - *Start shaders*: water, waterfalls, lava, screens / holograms, energy, smoke / fire / clouds scroll their
    textures; screens flicker; grass, leaves and trees sway in the wind (vertex displacement). Each kind has its own
    speed, and the wind has a strength. The speeds are drivers on one shared clock, so changing them does not
    recompile the materials. *Stop* removes the added nodes.
  - Play the timeline to see them move; they also move in animation renders.
- **Smooth LOD transitions** (Render > Props at render time, *LOD from the camera*). In an animation render, a prop
  that changes LOD is drawn in both LODs inside a band around each LOD distance (*Transition band*, ±20% by
  default). Each LOD covers its share of a fixed noise pattern, so the prop cross-fades over several frames
  instead of popping. Render only by default (*Also in the viewport* for the viewport). Works in Cycles and EEVEE.
- **Pink ground on Olympus**: that is the lavender moss of the map (`olympus_moss_purple_01`, layer 1 of the
  `..._moss_01_lavender_wld` materials) and the cherry trees: it is in the game too. The only magenta texture is
  `toolsfogvolume`, hidden with the triggers.

## What's new in 0.13.1
- Showing POIs no longer draws discs over them (the region object stays hidden; it only drives what is shown).

## What's new in 0.13.0
- **Import from the game removed** (too complex and not stable enough). Maps are imported from exports only
  (RSXreloaded recommended). Legion+ and revpk are no longer bundled, and the add-on is no longer limited to
  Windows. The *Models* folder accepts several `_models` folders separated by `;` (for `.nut` maps).
- **Several POIs at once** (Regions / POI): check POIs in the list, then *Show N POIs*. With no POI checked, the
  selected one is shown. The two buttons next to the list check / uncheck every POI. Region files of older versions are converted when the region is first used.
- **City lights follow the shown POIs**: while POIs are shown, *Find light sources* / *Create lights* only use the
  props inside them. Lights already created are rebuilt when the shown POIs change, and *Whole map* brings the
  others back.
- **Ice (World's Edge)**: the ice now uses its real texture. It was a flat colour, because the add-on took the
  cloudy tint texture instead of the cracked ice texture, which is now picked among the material's extra
  textures. The snow layer of the material is blended in with the vertex alpha, and on `_zup` materials only on
  faces looking up, so ice walls stay ice and snow sits on top.
- **Mossy rocks (World's Edge)**: props without vertex colours (the exporter writes them white) were fully covered
  by their second layer: yellow-green moss rocks (`icelandic_rock_cluster_01_moss_01_wld_bm`,
  `rock_lava_desertland_02_moss...`). On those meshes the second layer now only covers the faces that look up in
  the world, so the moss sits on top and the sides stay rock, even on tilted rocks.

## What's new in 0.12.3
Re-export the maps with the updated `rsxr-gui.exe` (RSXreloaded), then re-import them: most of these fixes
need the new exports.
- **Ground textures blend** instead of showing hard borders between meshes.
  - Two-layer materials (`_bm`, `..._to_...`) now mix layer 1 and layer 2 with the vertex alpha. The
    albedo, normal and gloss layers are all blended. Before, only layer 1 was shown.
  - Terrain-blend decals (`_b2t`, `_bt`: road tracks, mud, snow patches) store the terrain weight in the
    vertex alpha: 255 on the patch border. It is now inverted (detected per mesh), so the patches fade
    into the ground instead of ending in hard-edged squares.
  - Decals also find their opacity texture among the unnamed `slot<n>` textures.
  - The exporter now writes the world's vertex colours, which this needs.
- **Loot bins, doors, evac towers (balloon and base), crafting replicators...**
  - The exporter now also exports the models of the entities' dynamic props.
  - With an `.mprt`, the add-on loads `entities\<map>_script.ent` next to it automatically (option
    *Export's entities*, on by default, used when the Entities field is empty). That also brings the
    ziplines and the POIs. The editor helpers of the entity files (`mdl/dev/...`) are skipped.
- **Ziplines are yellow** (viewport and render). They were near-black in Solid mode and default grey in
  render.
- **White grass, flowers and some tree barks (E-District...).** When a material's pixel shader was missing,
  the exporter used the vertex shader's slot names, which number the textures differently. Those materials
  had no albedo. They now get their slot names from the texture formats like the others.
- **Effect shells are see-through.** Models named `vfx_*` (the Broken Moon dome
  `vfx_moon_mu1_z07_dome`) get a transparent, faintly glowing light-blue material.
- **.nut (ReMap) maps were invisible.** They are often built far from the origin (about 1 km up). The view
  stayed on the empty grid, and off-screen props are not instanced, so *View All* could not find them
  either. After an import, the 3D views now move to the imported props when they are not already looking
  at them.

## What's new in 0.12.2
- **Panel order**: Import, Visibility, Regions / POI, **Sky**, **City lights**, LOD & streaming, Render,
  Optimization.
- **RSXreloaded exports** (the exporter, not the add-on): many R5Flowstate materials had no texture in
  their `.cast`. Their shaders ship without bytecode, so the slot names (albedo, normal...) were
  unknown, and the textures were only written as `_images\0x<guid>.png`. On World's Edge this affected
  225 of 551 world materials and 662 of 4,457 prop materials. The exporter now guesses the slot names
  from the texture formats and references every texture in the `.cast`. **Re-export the map** with the
  updated `rsxr-gui.exe` to get them.

## What's new in 0.12.1
- **Blender 5.3 support.**
  - The map import stopped at the first prop. Blender 5.3 no longer accepts writing Geometry Nodes modifier
    inputs as `modifier["Socket_N"]`, so only the map geometry was imported, without props and with part of the
    textures. Modifier inputs now go through `modifier.properties.inputs` on 5.3, and the old way on 4.x.
  - Sky: Blender 5 renamed the *Nishita* sky to *Single Scattering* and *dust density* to *aerosol density*.
    Because of that, the sun angle and the densities were never applied. Both names now work.

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
- **Import from game** panel (removed in 0.13.0).
-----------------------------------------------------------------------------


## Load a map
Export the map with **RSXreloaded** (`rsxr-gui.exe`), then pick the `.mprt` in **Import**:
```
mp_rr_desertlands_hu/
  mp_rr_desertlands_hu_LOD0.mprt   <- static prop placements (pick this file)
  mp_rr_desertlands_hu_LOD0.cast   <- map geometry (loaded automatically)
  _images/                          <- geometry textures
  _models/<model>/<model>_LOD0.cast
  entities/mp_rr_desertlands_hu_script.ent   <- doors, loot bins, ziplines, POIs (loaded automatically)
```
- *Export's entities* (Import > Entities, on by default) loads `entities\<map>_script.ent` with the `.mprt`.
- The **Entities** field can instead point to an `.ent` file or to the map's *server* VPK, for example
  `R5Flowstate\vpk\englishserver_mp_rr_desertlands_hu.bsp.pak000_dir.vpk` (its `*_env.ent` and `*_fx.ent` are
  LZHAM-compressed and cannot be read there; RSXreloaded writes them uncompressed).
- Models that are not in `_models` are listed in the Blender text `APX_missing_models`.
- The *Models* field accepts several folders separated by `;` (the first folder holding a model wins).
- Legion+ / RSX exports load the same way (without the entity folder).

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
  - Check one or several POIs, then *Show N POIs*: everything outside their discs is hidden (with no POI checked,
    the selected one is shown). Objects outside are fully disabled, so EEVEE and Cycles do not even load their
    textures.
  - *Whole map* brings everything back. Nothing is re-imported.
  - City lights are only created inside the shown POIs.
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
- **Movers / Shaders**: moving props and animated materials (see 0.14.0). Both are off after an import and cost
  time on every frame: start them once the map is loaded, stop them for fast navigation.
  - Movers: the map's rotators (entities) with everything they move; by name, fan blades, turbine blades and
    engine / teleporter rings (around their axis of rotational symmetry), the rotating building of the Olympus
    farms (one shared axis) and hovering Tridents.
  - Shaders: water / waterfalls, lava, screens and holograms, energy, smoke / fire / clouds, foliage wind, and
    the cloth of banners and flags.
- **Render (Cycles)**: *Preview* and *Final* presets.
  - Uses the GPU when one is enabled in Preferences > System > Cycles Render Devices.
  - Sets denoising, adaptive sampling and enough transparent bounces for foliage.
  - Limits the texture size.
  - Uses camera-based LOD without field-of-view culling, so off-screen props still cast shadows.
  - *Smooth LOD transitions*: cross-fades the LODs in animation renders (Geometry Nodes instancing).

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

The models come from the *Models* folder: the `_models` folder of a map export holding these props (several folders
can be separated by `;`). Models that are not found are listed in `APX_missing_models`.

To see the custom map on its base map, import the base map first, then the `.nut` in the same file: both use the
same coordinates and recentering offset. After the import, the 3D view moves to the custom map when it is not
looking at it (ReMap maps are often built far from the origin).

## Notes
- Map geometry is split *one object per material* by default. The draw cost matches a single object, and regions
  only load the materials in view. Measured on a region render: 6 GB of memory instead of 17 GB.
- The textures come from the `.cast` files (materials and image paths), never from the `.mprt` or the BSP.
- Legion+ names the texture slots by position (albedo, normal, gloss, specular, emissive, ao, cavity), whatever
  the shader. The add-on trusts the file names first (`_albedo2Texture`, `_opacityMultiplyTexture`...), then the
  position, with the blend-material layout `[albedo, albedo2, normal, normal2, gloss, gloss2, spec]` for `_bm`.
- Blend materials (`_bm`) mix their two layers with the vertex alpha (RSXreloaded exports). Legion+ exports have
  no world vertex colours: their blend materials show layer 1 only. Props without vertex colours get layer 2 on the
  faces looking up.
- Ice (`desertlands_ice_03_wld_dark/bright…`): the albedo is a flat dark grey, the look comes from the game's ice
  shader. The add-on rebuilds it from the material's ice texture (the blue-ish extra texture with the most detail),
  gloss mask and ice normal map (light blue, glossy, a little subsurface and a clear coat), with its snow layer.
- Decals: the transparency comes from the `opacityMultiply` texture. Solid mode cannot apply it, so decals that use
  it are invisible in Solid and only appear in Material Preview / Rendered.
- Textures are read with retries: external drives sometimes report existing files as missing. A material whose
  albedo could not be read is rebuilt on the next import.
- Scale: 1 game unit = 0.0254 m. Rotation: XYZ Euler = (roll, pitch, yaw).
