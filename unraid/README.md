# Unraid

Analysarr est publié dans [Community Applications](https://ca.unraid.net) : cherchez **Analysarr**
dans l'onglet **Apps** d'Unraid.

Le template Docker vit dans un dépôt dédié, [SEC844/unraid-templates](https://github.com/SEC844/unraid-templates),
lu directement par Community Applications — c'est la seule copie, il n'y en a pas dans ce dépôt-ci.
Ce dossier ne garde que l'icône (`analysarr.png`, 512 px, générée à partir de
`frontend/public/favicon.svg` : garder les deux identiques) et cette documentation.

Support : [fil Unraid](https://forums.unraid.net/topic/200625-support-sec844-analysarr/).

## Installation manuelle (sans Community Applications)

1. Unraid → **Docker** → **Add Container**.
2. Dans **Template**, collez l'URL :
   `https://raw.githubusercontent.com/SEC844/unraid-templates/main/templates/analysarr.xml`
3. Vérifiez le chemin **Data (media and downloads)** puis **Apply**.
4. Ouvrez l'interface (`http://<ip-unraid>:1818`), créez le compte administrateur et suivez l'assistant.

## Montage des données : le point essentiel

Analysarr détecte les hardlinks en comparant les fichiers vus par Emby/Jellyfin et par votre client
torrent (qBittorrent, Deluge ou Transmission). Il doit donc voir **exactement les mêmes chemins** que
ces conteneurs :

| Conteneur | Chemin hôte | Chemin conteneur |
|---|---|---|
| Emby / Jellyfin | `/mnt/user/data` | `/data` |
| qBittorrent / Deluge / Transmission | `/mnt/user/data` | `/data` |
| Sonarr / Radarr | `/mnt/user/data` | `/data` |
| **Analysarr** | `/mnt/user/data` | `/data` |

C'est l'organisation recommandée par les [TRaSH Guides](https://trash-guides.info/File-and-Folder-Structure/).
Si vos conteneurs utilisent d'autres chemins, reproduisez-les à l'identique dans Analysarr. L'onglet
**Réglages → Chemins → Diagnostic des chemins** indique immédiatement si un montage manque.

La base de données est rangée dans `/config` (appdata), jamais dans `/data`, pour ne pas se mélanger
aux médias.

## Utilisateur et droits (`PUID`, `PGID`, `UMASK`)

Analysarr crée et supprime des fichiers dans la bibliothèque (réparation de hardlinks, corbeille) : il
doit tourner sous le même utilisateur que Sonarr, Radarr et le client torrent. Sur Unraid, c'est
presque toujours `PUID=99` (`nobody`) et `PGID=100` (`users`), avec `UMASK=002`. Seul le dossier
appdata (`/config`) change de propriétaire au démarrage, jamais `/data`.

## Variables et montages

Toute nouvelle variable d'environnement ou tout nouveau montage doit être répercuté dans le template
du dépôt `unraid-templates`, sans quoi les installations Community Applications ne le verront jamais.
