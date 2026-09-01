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
- Carte média (jaquette + statuts) : `sain`, `doublon`, `orphelin_qbit`, `tracker_unique`
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
- **Statuts** :
  - `sain` — fichiers cohérents, hardlinks valides
  - `doublon` — plusieurs fichiers Emby pour le même média (upgrade Sonarr/Radarr sans nettoyage de l'ancien)
  - `orphelin_qbit` — torrent(s) sans hardlink valide vers un fichier Emby actuel
  - `tracker_unique` — un seul tracker distinct détecté sur l'ensemble des torrents du groupe de hardlinks

## Intégrations externes

- **Emby** : API REST classique (`/Items`, `/Items/{Id}/Images/Primary` pour les jaquettes)
- **Sonarr / Radarr** : API REST v3 classique (séries/films, fichiers, historique)
- **qBittorrent** : Web API v2 (`torrents/info`, `torrents/delete`, `torrents/trackers?hash=`)
- **cross-seed** (optionnel) : mode daemon avec API HTTP — webhook `POST /api/webhook?apikey=<KEY>&infoHash=<HASH>` pour déclencher une recherche ciblée sur un torrent précis

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
