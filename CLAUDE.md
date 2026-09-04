# Analysarr — contexte projet

## Mission

Analysarr est un tableau de bord self-hosted qui unifie Emby, Sonarr, Radarr, qBittorrent et (en option) cross-seed pour donner, pour chaque média (film ou série), une vue de son état de santé dans toute la stack — et permettre des actions groupées (suppression cascade, recherche cross-seed ciblée) en quelques clics au lieu de jongler manuellement entre 4 interfaces.

Rien de comparable n'existe : Cleanuparr gère la santé des files de téléchargement (torrents bloqués, blacklist), Maintainerr gère la rétention basée sur les stats de visionnage. Aucun des deux ne fait de détection de doublons/orphelins par média avec suppression cascade unifiée.

## Principes non négociables

### 1. Zéro CLI pour l'utilisateur final
Personne ne doit éditer un `.env` ou taper une commande pour configurer l'app.
- Au premier lancement (aucune config en base), afficher un wizard d'onboarding qui demande : URL + clé API Emby, URL + clé API Sonarr, URL + clé API Radarr, URL + identifiants qBittorrent, et les chemins de dossiers utiles (bibliothèque Emby, dossier de téléchargement qBittorrent) tels que vus **depuis le conteneur** (après montage des volumes).
- Chaque champ a un bouton "Tester la connexion" avec retour immédiat clair (succès/échec + message d'erreur brut de l'API).
- Chaque champ de chemin a un bouton "Parcourir" qui ouvre un navigateur de dossiers (façon Unraid, `GET /api/settings/browse?path=`) listant les sous-dossiers du conteneur Analysarr — évite de taper un chemin à l'aveugle.
- Toute la config est stockée en base SQLite, modifiable à tout moment depuis un écran Réglages. Seuls le port d'écoute et le chemin de la base SQLite restent des variables d'environnement (détails d'infra Docker classiques, pas de la config applicative).

### 2. cross-seed est optionnel, jamais un prérequis
- Toggle "Activer cross-seed" dans les réglages, désactivé par défaut.
- Désactivé : aucune trace de cross-seed dans l'UI (pas de bouton grisé, pas de badge "inconnu" — la fonctionnalité est absente, point).
- Activé : champs URL + clé API cross-seed, test de connexion, apparition du bouton "Chercher un cross-seed" sur les fiches média.
- La détection de couverture tracker (voir principe 3) ne dépend PAS de cross-seed. cross-seed n'intervient que pour l'action de recherche ciblée, jamais pour la détection.

### 3. Visibilité des trackers par torrent
Pour chaque torrent qBittorrent lié à un média, afficher les trackers sur lesquels il est seedé via l'endpoint qBittorrent Web API v2 `torrents/trackers?hash=<hash>` (retourne URL + statut de chaque tracker annoncé). Extraire le domaine de chaque URL pour un affichage lisible (masquer les passkeys). Agréger au niveau du groupe de hardlinks : plusieurs torrents pour le même contenu = trackers potentiellement différents = nombre de trackers distincts couvrant ce contenu. C'est cette info qui remplace un simple badge "non cross-seedé" et qui reste utile même sans cross-seed activé.

## Fonctionnalités — backlog complet (par palier)

**Noyau (V1)**
- Scan périodique + à la demande, cache local (pas d'appel API à chaque affichage)
- Carte média (jaquette + statuts) : `sain`, `doublon`, `orphelin_qbit`, `tracker_unique`, `manquant_emby`, `manquant_qbit`
- Seuls les médias effectivement téléchargés sont affichés (un film/une série demandé(e) mais pas encore présent(e) dans Sonarr/Radarr n'apparaît pas dans l'interface)
- Fiche détail : tous les fichiers liés (Emby, Sonarr/Radarr, torrents qBit avec leurs trackers, cross-seed si activé)
- Suppression cascade avec preview avant action puis exécution, gestion d'erreur si une étape échoue
- Bouton "chercher un cross-seed" (visible seulement si cross-seed activé)
- Filtres/tri : statut, taille, bibliothèque, recherche texte

**Confort (V1.5)**
- Estimation d'espace disque récupérable par doublon/orphelin
- Mode simulation (dry-run) avant suppression cascade
- Historique des actions
- Notifications (Discord/ntfy/Gotify)
- Statut de connexion par service affiché en permanence
- Taux de rattachement torrent → média affiché après chaque scan (torrents qBittorrent rattachés à un média connu / total) : un écart signale un problème de correspondance plutôt qu'une vraie absence de contenu. `GET /api/scan/debug/unmatched-torrents` liste concrètement les torrents non rattachés (hash + nom), plutôt que de se fier seulement au chiffre agrégé.
- Diagnostic des chemins (Réglages → Chemins) : vérifie côté qBittorrent chaque fichier individuel du torrent (comme le scan lui-même), pas seulement `content_path`/`save_path` — un torrent multi-fichiers (pack saison, film avec extras) a un `content_path` qui est un DOSSIER, jamais un fichier régulier, donc toujours "non résolu" si on ne regarde que lui alors que le scan le matche très bien via ses fichiers individuels.
- Sélecteur de taille de grille (petite/moyenne/grande) sur la bibliothèque

**Bonus (V2)**
- Règles pour ignorer les faux doublons volontaires (ex: garder VF + VOSTFR)
- Auto-clean optionnel avec règles (off par défaut)
- Endpoint `/api/status` pour widget dashboard externe (type Homarr)
- Multi-instance Sonarr/Radarr/Emby

## Stack technique

- **Backend** : Python 3.12, FastAPI (async), SQLAlchemy/SQLModel, SQLite par défaut, APScheduler pour les scans planifiés, httpx pour les appels API externes.
- **Frontend** : React + TypeScript + Vite, TailwindCSS + shadcn/ui, TanStack Query + TanStack Table.
- **Un seul conteneur Docker** : FastAPI sert l'API (sous `/api`) et le build statique du frontend. Un seul port exposé.
- **Temps réel** : Server-Sent Events pour la progression des scans et suppressions cascade.
- **CI/CD** : GitHub Actions, build + push multi-arch vers GHCR à chaque tag `vX.Y.Z`.
- **Auth** : support du forward-auth (headers `X-Authentik-Username` / `X-Authentik-Email` envoyés par un reverse proxy Authentik). Pas de login natif à développer en V1.

## Concepts domaine

- **Média** : entité logique (film ou série) regroupant 1 entrée Sonarr/Radarr, 1 item Emby (si trouvé), N torrents qBittorrent, N entrées cross-seed (si activé).
- **Groupe de hardlinks** : fichiers partageant le même inode (`os.stat().st_ino` sur les chemins qBittorrent et Emby vus depuis le conteneur). Sert à détecter si un fichier de téléchargement est encore protégé par un lien vers la bibliothèque.
- **Correspondance torrent → média** : l'inode est le signal principal (pas l'historique Sonarr/Radarr ni le chemin de stockage). Un torrent dont le contenu partage l'inode d'un fichier Emby actuel est rattaché à ce média et marqué protégé, quel que soit son dossier de stockage réel — indispensable pour les copies cross-seed, qui vivent souvent hors des dossiers gérés par Sonarr/Radarr et que Sonarr/Radarr n'ont jamais "grabbed" (donc absentes de leur historique). Repli en trois passes : (1) rattachement direct par inode Emby actuel, sinon historique Sonarr/Radarr, sinon chemin racine du média ; (2) tout torrent encore non rattaché qui partage l'inode d'un torrent déjà identifié en passe 1 hérite du même média (cas typique : la copie cross-seed d'un ancien fichier orphelin) ; (3) pour ce qui reste non rattaché, repli heuristique par similarité de titre (préfixe de mots normalisés, année exigée pour les films) — capture les torrents ajoutés manuellement avant la mise en place du hardlink sur le serveur, jamais grabbés par Sonarr/Radarr et sans inode commun avec le fichier actuel (`Torrent.matched_by_name = true`, purement informatif). Ce rattachement n'est jamais marqué protégé (`is_hardlinked = false`).
- **Non hardlink vs orphelin** : parmi les torrents `is_hardlinked = false`, la provenance du rattachement (historique vs nom) ne suffit PAS à distinguer un vrai orphelin (ancienne qualité remplacée par un upgrade Sonarr/Radarr) d'une simple copie non hardlinkée du fichier actuel — seul le CONTENU fait foi. `Torrent.repairable = true` quand un fichier du torrent a la même taille en octets qu'un fichier de la bibliothèque pour le même épisode (séries, appariement par numéro `SxxEyy` extrait du nom de fichier) ou le même média (films) : quasi-certitude qu'il s'agit du même fichier, juste jamais hardlinké. `repairable = false` (vrai orphelin) sinon. Cette classification pilote tout : badge "Non hardlink" (ambre) vs "Orphelin" (rouge) sur la fiche média, éligibilité au bouton "Réparer les hardlinks", et exclusion des torrents réparables de la suppression cascade ("Nettoyer" ne doit jamais supprimer un fichier que la réparation compte réutiliser).
- **Réparation de hardlink** : action manuelle (fiche média, bouton "Réparer les hardlinks", preview puis confirmation) pour les torrents `is_hardlinked = false` ET `repairable = true`. Entièrement automatique une fois confirmé : pour chaque fichier de la bibliothèque apparié à un fichier du torrent, remplace la copie actuellement suivie par Emby/Sonarr/Radarr par un hardlink vers le fichier du torrent — un seul clic répare autant de fichiers que nécessaire (un épisode, une saison entière, ou toute la série). Sécurité critique : le nouveau lien est toujours créé à côté (`<chemin>.analysarr-tmp`) puis substitué par `os.replace()` (atomique) — l'original n'est **jamais** supprimé avant que le nouveau lien n'existe. Si `os.link()` échoue (notamment `EXDEV`/Errno 18 : le dossier de téléchargement et la bibliothèque sont sur des systèmes de fichiers différents — arrive typiquement quand un dossier `cross-seed` dédié n'est pas monté sur le même volume que le reste), le fichier de la bibliothèque reste intact et l'erreur est remontée telle quelle. Cas d'usage typique : anciennes séries ajoutées avant que le hardlink ne fonctionne correctement sur le serveur.
  - Deux cas sont exclus des réparations proposées (mais toujours listés, pour transparence) : (1) **déjà protégé** — un AUTRE torrent hardlinke déjà ce fichier (ex : plusieurs copies cross-seed d'un même film déjà liées entre elles et à la bibliothèque) ; le proposer romprait ce hardlink fonctionnel pour le remplacer par un lien vers ce torrent-ci, sans aucun gain. (2) **systèmes de fichiers différents** — contenu identique, mais `st_dev` diffère entre le fichier du torrent et celui de la bibliothèque : `os.link()` échoue toujours avec `EXDEV`, quel que soit le SENS du lien (ce n'est pas un choix de code à inverser, c'est une limite physique du système de fichiers) ; seul un changement de montage Docker peut le résoudre.
- **Statuts** (cumulables, un média peut en porter plusieurs à la fois) :
  - `sain` — aucun des statuts ci-dessous ne s'applique
  - `doublon` — plusieurs fichiers Emby pour le même média (upgrade Sonarr/Radarr sans nettoyage de l'ancien)
  - `orphelin_qbit` — torrent(s) sans hardlink valide vers un fichier Emby actuel ET sans preuve de contenu identique (`repairable = false`) : ancienne version remplacée par un upgrade Radarr/Sonarr, y compris ses éventuelles copies cross-seed
  - `non_hardlink` — torrent(s) non hardlinké(s) mais dont le contenu est identique à un fichier actuellement suivi (`repairable = true`) : le média EST bien seedé, juste pas protégé — n'implique jamais `manquant_qbit`, éligible au bouton "Réparer les hardlinks"
  - `tracker_unique` — un seul tracker distinct détecté sur l'ensemble des torrents du groupe de hardlinks
  - `manquant_emby` — le média n'a pas été retrouvé dans Emby
  - `manquant_qbit` — aucun torrent activement protégé (hardlink confirmé) ni réparable n'a été trouvé dans qBittorrent pour ce média

Un média `sain` doit donc être à la fois présent dans Emby et activement seedé (protégé) dans qBittorrent, sans torrent orphelin.

## Intégrations externes

- **Emby** : API REST classique (`/Items`, `/Items/{Id}/Images/Primary` pour les jaquettes)
- **Sonarr / Radarr** : API REST v3 classique (séries/films, fichiers, historique)
- **qBittorrent** : Web API v2 (`torrents/info`, `torrents/delete`, `torrents/trackers?hash=`)
- **cross-seed** (optionnel) : mode daemon avec API HTTP — webhook `POST /api/webhook?apikey=<KEY>` (clé en query string, `infoHash` OU `path` en corps de requête `x-www-form-urlencoded`) pour déclencher une recherche ciblée. `infoHash` cible un torrent qBittorrent existant ; `path` (chemin d'un fichier OU D'UN DOSSIER Emby) permet de lancer une recherche même pour un média absent de qBittorrent (statut `manquant_qbit`), sans torrent existant pour s'appuyer dessus. Chaque requête inclut `ignoreExcludeRecentSearch=true` (équivalent HTTP du flag CLI `--ignore-timestamps`) pour qu'une recherche déclenchée manuellement ne soit jamais ignorée en silence parce que cross-seed a déjà cherché récemment. Si le conteneur cross-seed monte la bibliothèque à un chemin différent d'Analysarr/Emby (réglage optionnel `cross_seed_library_path`), le `path` envoyé est traduit du préfixe `emby_library_path` vers ce chemin avant l'appel — sinon cross-seed rejette une requête `path` avec `400 "A valid infoHash or an accessible path must be provided"` même quand le fichier existe bel et bien.
  - **Portée de recherche pour une série** (bouton "Chercher un cross-seed" sur la fiche média, menu à 3 choix pour les séries) : `path` accepte un dossier aussi bien qu'un fichier — cross-seed explore alors récursivement (`findPotentialNestedRoots`, vérifié dans son code source). Ça permet de choisir la granularité sans paramètre dédié côté cross-seed, juste en changeant le chemin envoyé : "Par épisode" (défaut, historique — infoHash si des torrents existent déjà, sinon un chemin par fichier d'épisode), "Par saison" (un chemin par dossier de saison, un appel par saison), "Série intégrale" (un seul chemin, dossier racine commun à tous les épisodes, un seul appel). Contrairement à "par épisode", les portées saison/série cherchent TOUJOURS par chemin même si des torrents existent déjà pour certains épisodes — recherche délibérément plus large (utile pour retrouver un season pack/intégral sur un autre tracker). Endpoint : `POST /api/media/{id}/cross-seed-search?scope=episode|season|series`.

## Ce qu'on ne veut pas

- Config obligatoire en dehors de l'UI
- Dépendance dure à cross-seed
- Suppression sans preview + confirmation explicite
- Conteneurs séparés frontend/backend en V1

## État d'avancement

- Phase 1 (scaffolding + wizard de config) : à démarrer
- Phase 2 (moteur de scan + détection) : pas commencé
- Phase 3 (fiche détail + actions cascade) : pas commencé
- Phase 4 (confort) : pas commencé
