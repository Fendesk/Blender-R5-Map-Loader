Third-party tools bundled with Blender R5 Map Loader
====================================================

These programs are separate, unmodified executables. The add-on only runs them (command line); it does not
link to them. They keep their own licences, which are included in this folder.

LegionPlus.exe  -  Legion+ 1.5.1, Apex Legends asset extractor (originally by DTZxPorter, maintained by r-ex)
    Licence : GNU General Public License v3 (LEGIONPLUS-LICENSE.txt)
    Source  : https://github.com/r-ex/LegionPlus  (version 1.5.1)
    Use     : exports the models and materials of the game's rpak files.
    Note    : the RAD Game Tools libraries that come with Legion+ releases (binkawin64.dll, mileswin64.dll) are
              proprietary and NOT included. They are only needed for audio / video assets, not for models,
              materials or textures.

revpk.exe       -  VPK tool of the R5Reloaded SDK (same file as shipped with R5Reloaded and ReMap)
    Licence : Source 1 SDK License, Copyright (c) Valve Corp. (REVPK-LICENSE.txt)
              Third-party notices: REVPK-THIRD-PARTY-NOTICES.txt (LZHAM is MIT licensed)
    Source  : https://github.com/Mauler125/r5sdk  (src/revpk)
    Use     : unpacks the map VPK files (BSP and entity files, LZHAM compressed).
    Note    : the SDK licence allows redistribution free of charge only. This add-on is free.
