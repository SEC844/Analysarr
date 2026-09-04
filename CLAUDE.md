# Analysarr — contexte projet

## Mission

Analysarr est un tableau de bord self-hosted qui unifie Emby, Sonarr, Radarr, qBittorrent et (en option) cross-seed pour donner, pour chaque média (film ou série), une vue de son état de santé dans toute la stack — et permettre des actions groupées (suppression cascade, recherche cross-seed ciblée) en quelques clics au lieu de jongler manuellement entre 4 interfaces.

Rien de comparable n'existe : Cleanuparr gère la santé des files de téléchargement (torrents bloqués, blacklist), Maintainerr gère la rétention basée sur les stats de visionnage. Aucun des deux ne fait de détection de doublons/orphelins par média avec suppression cascade unifiée.

## Principes non négociables

### 1. Zéro CLI pour l'utilisateur final
Personne ne doit éditer un `.env` ou taper une commande pour configurer l'app.
- Au premier lancement (aucune config en base), afficher un wizard d'onboarding qui demande : URL + clé API Emby, URL + clé API Sonarr, URL + clé API Radarr, URL + identifiants qBittorrent, et les chemins de dossiers utiles (bibliothèque Emby, dossier de téléchargement qBittorrent) tels que vus **depuis le conteneur** (après montage des volumes).
- Chaque champ a un bouton "Tester la connexion" avec retour immédiat clair (succès/échec + message d'erreur brut de l'API).
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
- Taux de rattachement torrent → média affiché après chaque scan (torrents qBittorrent rattachés à un média connu / total) : un écart signale un problème de correspondance plutôt qu'une vraie absence de contenu
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
- **Correspondance torrent → média** : l'inode est le signal principal (pas l'historique Sonarr/Radarr ni le chemin de stockage). Un torrent dont le contenu partage l'inode d'un fichier Emby actuel est rattaché à ce média et marqué protégé, quel que soit son dossier de stockage réel — indispensable pour les copies cross-seed, qui vivent souvent hors des dossiers gérés par Sonarr/Radarr et que Sonarr/Radarr n'ont jamais "grabbed" (donc absentes de leur historique). Repli en trois passes : (1) rattachement direct par inode Emby actuel, sinon historique Sonarr/Radarr, sinon chemin racine du média ; (2) tout torrent encore non rattaché qui partage l'inode d'un torrent déjà identifié en passe 1 hérite du même média (cas typique : la copie cross-seed d'un ancien fichier orphelin) ; (3) pour ce qui reste non rattaché, repli heuristique par similarité de titre (préfixe de mots normalisés, année exigée pour les films) — capture les torrents ajoutés manuellement avant la mise en place du hardlink sur le serveur, jamais grabbés par Sonarr/Radarr et sans inode commun avec le fichier actuel. Ce rattachement n'est jamais marqué protégé (`Torrent.matched_by_name = true`, `is_hardlinked = false`) : le média reste `orphelin_qbit`/`manquant_qbit`, mais le torrent redevient visible sur la bonne fiche et éligible à la réparation ci-dessous.
- **Réparation de hardlink** : action manuelle (fiche média, bouton "Réparer les hardlinks", preview puis confirmation) pour les torrents rattachés mais non protégés (`is_hardlinked = false`), quelle que soit la passe qui les a rattachés. Pour chaque fichier de la bibliothèque appairé à un fichier du torrent (par numéro d'épisode pour une série, fichier unique pour un film) : supprime la copie actuellement suivie par Emby/Sonarr/Radarr et la remplace par `os.link()` vers le fichier du torrent, qui devient alors l'unique copie sur le disque et protège réellement le média. Cas d'usage typique : anciennes séries ajoutées avant que le hardlink ne fonctionne correctement sur le serveur.
- **Statuts** (cumulables, un média peut en porter plusieurs à la fois) :
  - `sain` — aucun des statuts ci-dessous ne s'applique
  - `doublon` — plusieurs fichiers Emby pour le même média (upgrade Sonarr/Radarr sans nettoyage de l'ancien)
  - `orphelin_qbit` — torrent(s) sans hardlink valide vers un fichier Emby actuel (ex : ancienne version remplacée par un upgrade Radarr/Sonarr, y compris ses éventuelles copies cross-seed)
  - `tracker_unique` — un seul tracker distinct détecté sur l'ensemble des torrents du groupe de hardlinks
  - `manquant_emby` — le média n'a pas été retrouvé dans Emby
  - `manquant_qbit` — aucun torrent activement protégé (hardlink confirmé) n'a été trouvé dans qBittorrent pour ce média

Un média `sain` doit donc être à la fois présent dans Emby et activement seedé (protégé) dans qBittorrent, sans torrent orphelin.

## Intégrations externes

- **Emby** : API REST classique (`/Items`, `/Items/{Id}/Images/Primary` pour les jaquettes)
- **Sonarr / Radarr** : API REST v3 classique (séries/films, fichiers, historique)
- **qBittorrent** : Web API v2 (`torrents/info`, `torrents/delete`, `torrents/trackers?hash=`)
- **cross-seed** (optionnel) : mode daemon avec API HTTP — webhook `POST /api/webhook?apikey=<KEY>` (clé en query string, `infoHash` OU `path` en corps de requête `x-www-form-urlencoded`) pour déclencher une recherche ciblée. `infoHash` cible un torrent qBittorrent existant ; `path` (chemin d'un fichier Emby) permet de lancer une recherche même pour un média absent de qBittorrent (statut `manquant_qbit`), sans torrent existant pour s'appuyer dessus.

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
