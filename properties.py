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


class APX_Settings(bpy.types.PropertyGroup):
    # ---------------- Import ----------------
    models_dir: StringProperty(
        name="Dossier des modèles", subtype="DIR_PATH",
        description="Dossier contenant les modèles exportés (Legion+ : .gltf/.glb/.fbx/.obj/.cast/.semodel). "
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
        name="Exclure", default="dev_,editor,trigger,clip,invisible,nodraw,collision,_col,fx/,error",
        description="Motifs séparés par des virgules ; les modèles dont le chemin contient l'un d'eux sont ignorés")
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
    import_world: BoolProperty(
        name="Importer la géométrie de la map", default=True,
        description="Avec un .mprt : importe aussi le .cast de même nom (terrain, bâtiments)")
    world_tile_size: FloatProperty(
        name="Taille des tuiles (m)", default=256.0, min=0.0,
        description="La géométrie de la map est découpée en tuiles (0 = un seul objet)")

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
    auto_lod_min_faces: IntProperty(name="Faces min. pour générer", default=500, min=0)
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
    use_culling: BoolProperty(name="Masquer au loin (culling)", default=True, update=_lod_changed)
    cull_distance: FloatProperty(name="Distance de culling (m)", default=1500.0, min=0.0, update=_lod_changed)
    cull_small_props: BoolProperty(name="Culling des petits props", default=True, update=_lod_changed)
    small_prop_size: FloatProperty(name="Taille 'petit' (m)", default=0.5, min=0.0, update=_lod_changed)
    small_prop_distance: FloatProperty(name="Distance petits props (m)", default=120.0, min=0.0,
                                       update=_lod_changed)
    lod_source: EnumProperty(
        name="Point de vue",
        items=[("VIEWPORT", "Viewport", "Distance depuis la vue 3D"),
               ("CAMERA", "Caméra de scène", "Distance depuis la caméra active")],
        default="VIEWPORT", update=_lod_changed)
    lod_auto_update: BoolProperty(name="Mise à jour auto", default=True,
                                  description="Met à jour les LODs en continu pendant la navigation")
    update_interval: FloatProperty(name="Intervalle (s)", default=0.25, min=0.05, max=5.0)
    update_move_threshold: FloatProperty(name="Seuil de déplacement (m)", default=2.0, min=0.0,
                                         description="Ne recalcule que si la vue a bougé de plus de cette distance")
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
