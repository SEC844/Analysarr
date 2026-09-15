# Template Unraid

`analysarr.xml` est le template Docker d'Analysarr pour Unraid.

## Installation manuelle (dès maintenant)

1. Unraid → **Docker** → **Add Container**.
2. Dans **Template**, collez l'URL :
   `https://raw.githubusercontent.com/SEC844/Analysarr/main/unraid/analysarr.xml`
3. Vérifiez le chemin **Data (media and downloads)** puis **Apply**.
4. Ouvrez l'interface (`http://<ip-unraid>:8000`), créez le compte administrateur et suivez l'assistant.

## Montage des données : le point essentiel

Analysarr détecte les hardlinks en comparant les fichiers vus par Emby/Jellyfin et par qBittorrent. Il doit donc voir **exactement les mêmes chemins** que ces conteneurs :

| Conteneur | Chemin hôte | Chemin conteneur |
|---|---|---|
| Emby / Jellyfin | `/mnt/user/data` | `/data` |
| qBittorrent | `/mnt/user/data` | `/data` |
| Sonarr / Radarr | `/mnt/user/data` | `/data` |
| **Analysarr** | `/mnt/user/data` | `/data` |

C'est l'organisation recommandée par les [TRaSH Guides](https://trash-guides.info/File-and-Folder-Structure/). Si vos conteneurs utilisent d'autres chemins, reproduisez-les à l'identique dans Analysarr. L'onglet **Réglages → Chemins → Diagnostic des chemins** indique immédiatement si un montage manque.

La base de données est rangée dans `/config` (appdata), jamais dans `/data`, pour ne pas se mélanger aux médias.
