"""Rattachement d'un média de la bibliothèque à Sonarr/Radarr.

Un média détecté dans la bibliothèque seule (statut `manquant_arr`, voir
`services/scan.build_untracked_results`) n'a pas d'identité Sonarr/Radarr.
Ce module la lui donne : il cherche la fiche chez Sonarr/Radarr, la propose à
l'utilisateur, puis l'ajoute AVEC le dossier qui contient déjà les fichiers,
pour que Sonarr/Radarr les reprenne sans rien retélécharger.

Fiabilité des identifiants — le point délicat. Les `ProviderIds` du serveur
multimédia sont parfois faux (identifiant IMDb d'une autre œuvre, vu en
conditions réelles) : ils ne sont donc jamais utilisés tels quels. Chaque
identifiant est RÉSOLU chez Sonarr/Radarr, et le candidat obtenu n'est retenu
que si son titre (ou un titre alternatif) et, quand elle est connue, son année
correspondent au média. Radarr expose deux endpoints par identifiant
(`movie/lookup/tmdb`, `movie/lookup/imdb`) ; Sonarr ne comprend que le préfixe
`tvdb:` et retombe sinon en recherche texte, d'où la validation systématique.

Sécurité : le dossier racine et le profil de qualité envoyés par l'interface
sont vérifiés contre les listes que Sonarr/Radarr renvoie — jamais de chemin
arbitraire transmis à un service externe."""

from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlmodel import Session, select

from app.models.media import Media, MediaFile, MediaType
from app.models.settings import Settings
from app.services.arr_instances import ArrTarget, arr_target_by_id, arr_targets
from app.services.torrent_match import normalize_words

# Un candidat "certain" vient d'un identifiant résolu par Sonarr/Radarr ET
# porte le même titre (et la même année) que le média : c'est le seul niveau
# qu'une automatisation accepte, sans confirmation humaine.
CERTAIN = "certain"
PROBABLE = "probable"


@dataclass
class ArrCandidate:
    title: str
    year: int | None
    tmdb_id: int | None
    tvdb_id: int | None
    imdb_id: str | None
    confidence: str
    # Fiche renvoyée par Sonarr/Radarr, réutilisée telle quelle à l'ajout.
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Identifiant retenu pour l'ajout : celui que le service utilise."""
        if self.tvdb_id:
            return f"tvdb:{self.tvdb_id}"
        if self.tmdb_id:
            return f"tmdb:{self.tmdb_id}"
        return f"imdb:{self.imdb_id or ''}"


@dataclass
class ArrLinkPreview:
    service: str  # radarr | sonarr
    instance_id: int | None
    instance_name: str
    candidates: list[ArrCandidate]
    root_folders: list[str]
    suggested_root: str | None
    quality_profiles: list[tuple[int, str]]
    suggested_profile: int | None


class ArrLinkError(RuntimeError):
    """Rattachement impossible : message destiné à l'utilisateur."""


def _titles_of(media: Media) -> list[str]:
    titles = [media.title] + [t for t in (media.alt_titles or "").split("\n") if t]
    return [t for t in titles if t]


def _same_title(media: Media, candidate_titles: list[str]) -> bool:
    """Comparaison sur les mots normalisés, comme le rattachement des torrents
    (`services/torrent_match.normalize_words`) : mêmes règles partout."""
    ours = {" ".join(normalize_words(title)) for title in _titles_of(media)}
    theirs = {" ".join(normalize_words(title)) for title in candidate_titles if title}
    ours.discard("")
    theirs.discard("")
    return bool(ours & theirs)


def _same_year(media: Media, year: int | None) -> bool:
    # Une année d'écart est courante entre bases (sortie cinéma/diffusion).
    if media.year is None or year is None:
        return True
    return abs(media.year - year) <= 1


def _candidate_titles(entry: dict[str, Any]) -> list[str]:
    titles = [entry.get("title"), entry.get("originalTitle"), entry.get("sortTitle")]
    titles += [alt.get("title") for alt in entry.get("alternateTitles") or [] if isinstance(alt, dict)]
    return [title for title in titles if title]


def _to_candidate(media: Media, entry: dict[str, Any], from_identifier: bool) -> ArrCandidate | None:
    """Candidat retenu seulement s'il ressemble vraiment au média : sans cette
    vérification, un identifiant erroné du serveur multimédia rattacherait le
    média à une autre œuvre (bug réel)."""
    titles = _candidate_titles(entry)
    year = entry.get("year")
    matches_title = _same_title(media, titles)
    if not matches_title or not _same_year(media, year):
        return None
    return ArrCandidate(
        title=entry.get("title") or media.title,
        year=year,
        tmdb_id=entry.get("tmdbId"),
        tvdb_id=entry.get("tvdbId"),
        imdb_id=entry.get("imdbId"),
        confidence=CERTAIN if from_identifier else PROBABLE,
        payload=entry,
    )


def _dedup(candidates: list[ArrCandidate]) -> list[ArrCandidate]:
    """Un même titre peut sortir de plusieurs recherches : on garde la
    meilleure confiance, dans l'ordre de découverte."""
    kept: dict[str, ArrCandidate] = {}
    for candidate in candidates:
        existing = kept.get(candidate.key)
        if existing is None or (existing.confidence == PROBABLE and candidate.confidence == CERTAIN):
            kept[candidate.key] = candidate
    return list(kept.values())


async def _movie_candidates(target: ArrTarget, media: Media) -> list[ArrCandidate]:
    radarr = target.radarr()
    found: list[ArrCandidate] = []

    if media.tmdb_id:
        entry = await radarr.lookup_movie_by_tmdb(media.tmdb_id)
        if entry and (candidate := _to_candidate(media, entry, from_identifier=True)):
            found.append(candidate)
    if media.imdb_id:
        entry = await radarr.lookup_movie_by_imdb(media.imdb_id)
        if entry and (candidate := _to_candidate(media, entry, from_identifier=True)):
            found.append(candidate)

    # Recherche par titre : elle sert aussi de filet quand les identifiants du
    # serveur multimédia sont faux.
    term = f"{media.title} {media.year}" if media.year else media.title
    for entry in (await radarr.lookup_movies(term))[:10]:
        if candidate := _to_candidate(media, entry, from_identifier=False):
            found.append(candidate)
    return _dedup(found)


async def _series_candidates(target: ArrTarget, media: Media) -> list[ArrCandidate]:
    sonarr = target.sonarr()
    found: list[ArrCandidate] = []

    if media.tvdb_id:
        for entry in await sonarr.lookup_series(f"tvdb:{media.tvdb_id}"):
            if candidate := _to_candidate(media, entry, from_identifier=True):
                found.append(candidate)
    for entry in (await sonarr.lookup_series(media.title))[:10]:
        if candidate := _to_candidate(media, entry, from_identifier=False):
            found.append(candidate)
    return _dedup(found)


def _media_paths(session: Session, media: Media) -> list[str]:
    paths = [row.path for row in session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()]
    return [path for path in paths if path]


def _posix(path: str) -> str:
    """Chemins comparés en séparateurs POSIX : ils viennent de conteneurs
    Linux, même quand Analysarr tourne ailleurs."""
    return path.replace("\\", "/").rstrip("/")


def suggest_root(root_folders: list[str], paths: list[str], media_root: str | None) -> str | None:
    """Dossier racine qui contient déjà les fichiers : c'est lui qui permet à
    Sonarr/Radarr de reprendre l'existant au lieu de retélécharger. Le plus
    profond gagne, pour distinguer `/data/media` de `/data/media/films`."""
    references = [_posix(path) for path in [media_root, *paths] if path]
    matching = [
        root for root in root_folders if any(reference.startswith(_posix(root) + "/") for reference in references)
    ]
    return max(matching, key=len) if matching else None


async def build_link_preview(session: Session, settings: Settings, media: Media) -> ArrLinkPreview:
    """Ce qu'Analysarr propose pour rattacher ce média : candidats vérifiés,
    dossiers racine et profils de qualité de l'instance visée."""
    if media.radarr_id is not None or media.sonarr_id is not None:
        raise ArrLinkError("Ce média est déjà suivi par Sonarr/Radarr.")

    is_movie = media.media_type == MediaType.movie
    service = "radarr" if is_movie else "sonarr"
    targets = arr_targets(session, settings, service)
    if not targets:
        raise ArrLinkError(f"Aucune instance {service.capitalize()} configurée.")
    target = targets[0]

    try:
        candidates = await (_movie_candidates(target, media) if is_movie else _series_candidates(target, media))
        client = target.radarr() if is_movie else target.sonarr()
        root_folders = [row.get("path") for row in await client.get_root_folders() if row.get("path")]
        profiles = [(row["id"], row.get("name") or str(row["id"])) for row in await client.get_quality_profiles() if row.get("id")]
    except httpx.HTTPError as exc:
        raise ArrLinkError(f"{target.name} injoignable : {exc}") from exc

    return ArrLinkPreview(
        service=service,
        instance_id=target.instance_id,
        instance_name=target.name,
        candidates=candidates,
        root_folders=root_folders,
        suggested_root=suggest_root(root_folders, _media_paths(session, media), media.root_path),
        quality_profiles=profiles,
        suggested_profile=profiles[0][0] if profiles else None,
    )


def pick_automatic(preview: ArrLinkPreview) -> ArrCandidate | None:
    """Candidat qu'une automatisation peut ajouter sans confirmation : un seul
    candidat certain, et un dossier racine déduit des fichiers en place."""
    certain = [candidate for candidate in preview.candidates if candidate.confidence == CERTAIN]
    if len(certain) != 1 or preview.suggested_root is None or preview.suggested_profile is None:
        return None
    return certain[0]


async def link_media(
    session: Session,
    settings: Settings,
    media: Media,
    *,
    candidate_key: str,
    root_folder: str,
    quality_profile_id: int,
    monitored: bool = True,
) -> str:
    """Ajoute le média dans Sonarr/Radarr puis demande un rescan du dossier.
    Renvoie le titre ajouté. Le choix de l'utilisateur est re-vérifié ici :
    l'interface ne fait que proposer, le serveur décide."""
    preview = await build_link_preview(session, settings, media)
    candidate = next((c for c in preview.candidates if c.key == candidate_key), None)
    if candidate is None:
        raise ArrLinkError("Candidat inconnu : relancez la recherche.")
    if root_folder not in preview.root_folders:
        raise ArrLinkError("Dossier racine inconnu de Sonarr/Radarr.")
    if quality_profile_id not in {profile_id for profile_id, _ in preview.quality_profiles}:
        raise ArrLinkError("Profil de qualité inconnu de Sonarr/Radarr.")

    target = arr_target_by_id(session, settings, preview.service, preview.instance_id)
    if target is None:
        raise ArrLinkError("Instance Sonarr/Radarr introuvable.")

    body = dict(candidate.payload)
    body["rootFolderPath"] = root_folder
    body["qualityProfileId"] = quality_profile_id
    body["monitored"] = monitored

    try:
        if preview.service == "radarr":
            created = await target.radarr().add_movie(body) or {}
            if created.get("id"):
                await target.radarr().rescan_movie(created["id"])
        else:
            created = await target.sonarr().add_series(body) or {}
            if created.get("id"):
                await target.sonarr().rescan_series(created["id"])
    except httpx.HTTPError as exc:
        raise ArrLinkError(_error_text(exc)) from exc
    return candidate.title


def _error_text(exc: httpx.HTTPError) -> str:
    """Message utile : Sonarr/Radarr explique le refus dans le corps de la
    réponse (dossier inaccessible, film déjà présent...), un code HTTP seul
    n'aiderait personne. Aucune clé API n'y transite (en-tête)."""
    response = getattr(exc, "response", None)
    if response is None:
        return f"{type(exc).__name__} : {exc}"
    body = " ".join((response.text or "").split())
    return f"HTTP {response.status_code}{' — ' + body[:200] if body else ''}"
