# Template Unraid

`analysarr.xml` est le template Docker d'Analysarr pour Unraid. Il est aussi publié dans
[Community Applications](https://ca.unraid.net) : cherchez **Analysarr** dans l'onglet **Apps**.
Le dépôt lu par Community Applications est
[SEC844/unraid-templates](https://github.com/SEC844/unraid-templates) ; les deux copies du template
doivent rester identiques.

Support : [fil Unraid](https://forums.unraid.net/topic/200625-support-sec844-analysarr/).

## Installation manuelle (sans Community Applications)

1. Unraid → **Docker** → **Add Container**.
2. Dans **Template**, collez l'URL :
   `https://raw.githubusercontent.com/SEC844/Analysarr/main/unraid/analysarr.xml`
3. Vérifiez le chemin **Data (media and downloads)** puis **Apply**.
4. Ouvrez l'interface (`http://<ip-unraid>:1818`), créez le compte administrateur et suivez l'assistant.

## Montage des données : le point essentiel

Analysarr détecte les hardlinks en comparant les fichiers vus par Emby/Jellyfin et par votre client torrent (qBittorrent, Deluge ou Transmission). Il doit donc voir **exactement les mêmes chemins** que ces conteneurs :

| Conteneur | Chemin hôte | Chemin conteneur |
|---|---|---|
| Emby / Jellyfin | `/mnt/user/data` | `/data` |
| qBittorrent / Deluge / Transmission | `/mnt/user/data` | `/data` |
| Sonarr / Radarr | `/mnt/user/data` | `/data` |
| **Analysarr** | `/mnt/user/data` | `/data` |

C'est l'organisation recommandée par les [TRaSH Guides](https://trash-guides.info/File-and-Folder-Structure/). Si vos conteneurs utilisent d'autres chemins, reproduisez-les à l'identique dans Analysarr. L'onglet **Réglages → Chemins → Diagnostic des chemins** indique immédiatement si un montage manque.

La base de données est rangée dans `/config` (appdata), jamais dans `/data`, pour ne pas se mélanger aux médias.
