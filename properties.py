import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, StringProperty)

from . import lod_system


def _lod_changed(self, context):
    lod_system.invalidate()
    try:
        from . import gn_instancer
        gn_instancer.apply_settings(context.scene)
    except Exception as exc:  # noqa: BLE001
        print(f"[APX] {exc}")


def _vis_changed(self, context):
    from . import visibility
    visibility.apply(context.scene)


class APX_Settings(bpy.types.PropertyGroup):
    # ---------------- Visibilité ----------------
    hide_trigger: BoolProperty(name="Cacher les triggers", default=True, update=_vis_changed,
                               description="toolstrigger, volumes de brouillard…")
    hide_tool: BoolProperty(name="Cacher les brushes outils", default=True, update=_vis_changed,
                            description="clip, blocklight, skybox, nodraw, invisible…")
    hide_gamemode: BoolProperty(name="Cacher les barrières de mode de jeu", default=True, update=_vis_changed,
                                description="Murs/bordures des modes Arènes, Contrôle… (matériaux ar_*)")
    hide_dev: BoolProperty(name="Cacher les textures dev", default=True, update=_vis_changed,
                           description="dev_* (damiers), test_*, placeholders. Les panneaux lumineux dev_*_light, "
                                       "visibles en jeu, ne sont pas concernés")
    decal_offset: FloatProperty(
        name="Décalage des surcouches (unités jeu)", default=0.35, min=0.0, max=5.0, precision=3,
        description="Décale les décals et surcouches de terrain (_b2t, _bt, decal…) au-dessus de la surface "
                    "pour supprimer le scintillement (z-fighting). 0 = off")
    cast_extra_textures: BoolProperty(
        name="Ajouter les textures non utilisées", default=False,
        description="Ajoute aussi AO, cavity, etc. dans les matériaux (non branchées, plus de mémoire)")
    texture_limit: EnumProperty(
        name="Résolution max des textures (viewport)",
        items=[("CLAMP_OFF", "Illimitée", ""), ("CLAMP_2048", "2048", ""), ("CLAMP_1024", "1024", ""),
               ("CLAMP_512", "512", ""), ("CLAMP_256", "256", "")],
        default="CLAMP_1024",
        description="Une map complète utilise des milliers de textures 2K : sans limite la mémoire vidéo "
                    "sature et des textures ne s'affichent pas")

    # ---------------- Import ----------------
    models_dir: StringProperty(
        name="Dossier des modèles", subtype="DIR_PATH",
        description="Dossier contenant les modèles exportés (RSX / Legion+ : .cast, ou .gltf/.glb/.fbx/.obj/.smd). "
                    "Les fichiers nommés *_LOD0, *_LOD1... sont reconnus comme LODs")
    unit_scale: FloatProperty(
        name="Échelle", default=0.0254, min=1e-5, precision=4,
        description="Unités du jeu -> mètres (1 unité Source = 1 pouce = 0.0254 m)")
    axis_correction: EnumProperty(
        name="Correction d'axe des modèles",
        items=[("NONE", "Aucune", ""), ("X90", "X +90°", ""), ("X-90", "X -90°", ""),
               ("Z90", "Z +90°", ""), ("Z-90", "Z -90°", ""), ("Z180", "Z 180°", "")],
        default="NONE",
        description="Rotation appliquée aux modèles si l'exporteur les sort couchés / tournés")
    recenter: BoolProperty(name="Recentrer la map", default=False,
                           description="Déplace le centre de la map à l'origine (meilleure précision)")
    split_by_category: BoolProperty(name="Collections par catégorie", default=True,
                                    description="Range les props par dossier (props, foliage, rocks...)")
    exclude_patterns: StringProperty(
        name="Exclure", default="dev,editor,trigger,playerclip,invisible,nodraw,collision,error",
        description="Mots séparés par des virgules. Un modèle est ignoré si un mot entier de son nom correspond "
                    "(« col » exclut « rock_col » mais pas « column »). * = joker")
    use_region: BoolProperty(name="Limiter à une zone", default=False,
                             description="N'importe que les props dans un rayon autour d'un point (unités du jeu)")
    region_center: FloatVectorProperty(name="Centre", size=3, default=(0, 0, 0))
    region_radius: FloatProperty(name="Rayon", default=10000.0, min=1.0)
    join_meshes: BoolProperty(name="Fusionner les meshes d'un modèle", default=True,
                              description="1 objet par LOD au lieu de plusieurs (moins d'objets = viewport plus rapide)")
    merge_distance: FloatProperty(name="Fusion des sommets", default=0.0, min=0.0, precision=5,
                                  description="Distance de fusion des sommets doublons (0 = désactivé)")
    dedupe_on_import: BoolProperty(name="Dédoublonner matériaux/images", default=True)
    instancing_mode: EnumProperty(
        name="Instanciation",
        items=[("GN", "Geometry Nodes (rapide)",
                "1 objet par modèle unique, LOD calculé dans Blender. Recommandé pour les maps complètes (.mprt)"),
               ("EMPTY", "Empties (1 objet par prop)",
                "Chaque prop est sélectionnable/éditable. Lent au-delà de ~50 000 props")],
        default="GN")
    copy_source: StringProperty(
        name="Dossier de la map", subtype="DIR_PATH",
        description="Dossier qui contient le .mprt, le .cast et _models (vide = parent du dossier des modèles)")
    copy_max_texture: EnumProperty(
        name="Textures",
        items=[("1024", "Réduire à 1024 px (conseillé)", "Chargement ~4x plus rapide, 4x moins de mémoire vidéo"),
               ("2048", "Réduire à 2048 px", ""),
               ("512", "Réduire à 512 px (PC modeste)", ""),
               ("0", "Taille d'origine", "Copie simple sans réduction")],
        default="1024",
        description="Les textures 2K mettent longtemps à se charger (décodage PNG) et saturent la mémoire vidéo")
    ent_path: StringProperty(
        name="Entités (.vpk / .ent)", subtype="FILE_PATH",
        description="Optionnel, importé avec le .mprt : le VPK serveur de la map (R5Reloaded : "
                    "vpk/englishserver_<map>.bsp.pak000_dir.vpk) ou un fichier .ent. Ajoute les props "
                    "dynamiques (portes, coffres, tyroliennes...)")
    ent_files: StringProperty(
        name="Fichiers .ent du VPK", default="script",
        description="Lesquels lire dans le VPK, séparés par des virgules : script, snd, spawn, env, fx "
                    "(env et fx sont compressés dans les VPK R5Reloaded et ne peuvent pas être lus)")
    import_ziplines: BoolProperty(name="Créer les tyroliennes", default=True,
                                  description="Crée les câbles des tyroliennes en courbes à partir des .ent")
    setup_viewport_on_import: BoolProperty(
        name="Régler le viewport après import", default=True,
        description="Mode Solid en couleur « Texture », distances d'affichage adaptées à une grande map")
    import_world: BoolProperty(
        name="Importer la géométrie de la map", default=True,
        description="Avec un .mprt : importe aussi le .cast de même nom (terrain, bâtiments)")
    world_tile_size: FloatProperty(
        name="Taille des tuiles (m)", default=0.0, min=0.0,
        description="0 = un seul objet (le plus rapide : mesuré 247 fps en Solid contre 62 fps avec des tuiles de "
                    "256 m, car chaque tuile multiplie les appels de dessin par matériau)")

    # ---------------- Cast ----------------
    cast_load_textures: BoolProperty(name="Charger les textures", default=True)
    cast_alpha_mode: EnumProperty(
        name="Transparence",
        items=[("AUTO", "Auto (feuillage, grillages, décals…)",
                "Alpha de l'albedo seulement pour les matériaux dont le nom l'indique"),
               ("ALWAYS", "Toujours", "Alpha de l'albedo sur tous les matériaux"),
               ("NEVER", "Jamais", "")],
        default="AUTO",
        description="Les albedos Apex sont toutes RGBA mais l'alpha ne sert à la transparence que sur certains")
    cast_spec_to_metal: BoolProperty(
        name="Convertir spéculaire -> métal", default=True,
        description="Apex utilise un workflow spéculaire/gloss : les métaux ont une albedo noire et leur "
                    "couleur dans la carte specular. Convertit vers Base Color/Metallic")
    cast_flip_normal_green: BoolProperty(name="Inverser le vert des normal maps", default=True,
                                         description="Les normal maps Apex sont au format DirectX")
    cast_weld: FloatProperty(name="Soudure des sommets (unités jeu)", default=0.01, min=0.0, precision=4,
                             description="Fusionne les sommets identiques (réduit fortement la mémoire). 0 = off")
    cast_custom_normals: BoolProperty(name="Normales d'origine", default=True,
                                      description="Utilise les normales du jeu (plus fidèle, un peu plus lourd)")
    cast_smooth_by_angle: BoolProperty(name="Lissage", default=True)

    # ---------------- LOD ----------------
    use_lod: BoolProperty(name="Activer les LODs", default=True, update=_lod_changed,
                          description="Désactivé : tous les props utilisent le LOD fixe choisi, sans culling")
    forced_lod: IntProperty(name="LOD fixe", default=0, min=0, max=7, update=_lod_changed,
                            description="Niveau utilisé quand les LODs sont désactivés")
    max_lods: IntProperty(name="LODs max importés", default=4, min=1, max=8,
                          description="Nombre maximum de niveaux de LOD chargés par modèle")
    auto_generate_lods: BoolProperty(name="Générer les LODs manquants", default=True,
                                     description="Crée des LODs par décimation si le modèle n'en a qu'un")
    auto_lod_ratio: FloatProperty(name="Ratio de décimation", default=0.5, min=0.05, max=0.95,
                                  description="Chaque LOD garde ce ratio de faces du précédent")
    auto_lod_min_faces: IntProperty(name="Faces min. pour générer", default=200, min=0)
    lod_distances: FloatVectorProperty(
        name="Distances LOD (m)", size=4, default=(25.0, 60.0, 150.0, 400.0), min=0.0,
        update=_lod_changed, description="Distance à partir de laquelle on passe au LOD1, LOD2, LOD3, LOD4")
    lod_bias: FloatProperty(name="Biais qualité", default=1.0, min=0.1, max=10.0, update=_lod_changed,
                            description=">1 = haute qualité plus loin, <1 = bascule plus tôt")
    lod_size_scaling: BoolProperty(name="Adapter à la taille", default=True, update=_lod_changed,
                                   description="Les gros objets gardent leur détail plus loin")
    lod_reference_size: FloatProperty(name="Taille de référence (m)", default=2.0, min=0.01, update=_lod_changed)
    use_bounds_level: BoolProperty(name="Boîtes englobantes au loin", default=False, update=_lod_changed)
    bounds_distance: FloatProperty(name="Distance boîtes (m)", default=800.0, min=0.0, update=_lod_changed)
    use_culling: BoolProperty(name="Masquer au loin (culling)", default=False, update=_lod_changed,
                              description="Cache les props lointains (plus fluide, mais la map n'est plus "
                                          "entièrement visible)")
    cull_distance: FloatProperty(name="Distance de culling (m)", default=3000.0, min=0.0, update=_lod_changed)
    cull_small_props: BoolProperty(
        name="Décharger les petits objets lointains", default=True, update=_lod_changed,
        description="Cache un prop quand il devient plus petit qu'un certain pourcentage de la hauteur de "
                    "l'écran (un petit objet disparaît vite, un gros rocher reste visible très loin)")
    min_screen_pct: FloatProperty(
        name="Taille min. à l'écran (%)", default=1.0, min=0.0, max=20.0, precision=2, update=_lod_changed,
        description="Rayon minimal du prop en % de la demi-hauteur de l'écran (1 % ≈ 5 pixels de rayon en "
                    "1080p). Mesuré sur World's Edge au sol : 0.6 % ≈ 19 fps, 1 % ≈ 35 fps, 2 % ≈ 48 fps")
    use_frustum: BoolProperty(
        name="Cacher hors du champ de vision", default=True, update=_lod_changed,
        description="Les props derrière la caméra ou hors cadre ne sont pas instanciés (gros gain de FPS)")
    frustum_margin: FloatProperty(
        name="Marge du champ", default=1.5, min=1.0, max=3.0, update=_lod_changed,
        description="Élargit le champ testé pour éviter de voir des props apparaître quand on tourne la vue")
    render_frustum: BoolProperty(
        name="Champ de vision au rendu", default=False,
        description="Applique aussi le culling hors champ au rendu (plus rapide, mais les ombres et reflets "
                    "des objets hors champ disparaissent)")
    small_prop_size: FloatProperty(name="Taille 'petit' (m)", default=0.5, min=0.0, update=_lod_changed)
    small_prop_distance: FloatProperty(name="Distance petits props (m)", default=250.0, min=0.0,
                                       update=_lod_changed)
    lod_source: EnumProperty(
        name="Point de vue",
        items=[("VIEWPORT", "Viewport", "Distance depuis la vue 3D"),
               ("CAMERA", "Caméra de scène", "Distance depuis la caméra active")],
        default="VIEWPORT", update=_lod_changed)
    lod_auto_update: BoolProperty(name="Mise à jour auto", default=True,
                                  description="Met à jour les LODs en continu pendant la navigation")
    update_interval: FloatProperty(
        name="Recalcul pendant le mouvement (s)", default=0.0, min=0.0, max=30.0,
        description="0 = jamais pendant qu'on bouge (déplacement fluide, recalcul à l'arrêt). Sinon, recalcul "
                    "forcé toutes les N secondes de mouvement continu (chaque recalcul gèle l'image ~0,3 s)")
    update_settle: FloatProperty(
        name="Délai après arrêt (s)", default=0.25, min=0.0, max=2.0,
        description="Recalcule quand la vue est immobile depuis ce délai")
    update_move_threshold: FloatProperty(name="Seuil de déplacement (m)", default=2.0, min=0.0,
                                         description="Ne recalcule que si la vue a bougé de plus de cette distance")
    update_angle_threshold: FloatProperty(name="Seuil de rotation (°)", default=6.0, min=0.0, max=90.0,
                                          description="… ou a tourné de plus de cet angle")
    render_mode: EnumProperty(
        name="Au rendu",
        items=[("LOD0", "Tout en LOD0", "Qualité max au rendu"),
               ("CAMERA", "LOD depuis la caméra", "Recalcule les LODs depuis la caméra à chaque frame"),
               ("KEEP", "Garder l'état viewport", "")],
        default="CAMERA")


def register():
    bpy.utils.register_class(APX_Settings)
    bpy.types.Scene.apx_settings = bpy.props.PointerProperty(type=APX_Settings)


def unregister():
    del bpy.types.Scene.apx_settings
    bpy.utils.unregister_class(APX_Settings)
