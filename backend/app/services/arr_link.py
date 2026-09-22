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

Le dossier importé vient TOUJOURS de Sonarr/Radarr (`unmappedFolders` de
`GET /api/v3/rootfolder`), jamais des chemins vus par Analysarr : les deux
conteneurs montent souvent la bibliothèque ailleurs, et un chemin deviné
aboutissait à un média ajouté sans son fichier. L'ajout emprunte l'import en
masse (`POST /api/v3/movie/import`, `/series/import`), c'est-à-dire exactement
le bouton « Importer N films » de l'écran d'import.

Sécurité : dossier et profil de qualité envoyés par l'interface sont vérifiés
contre les listes que Sonarr/Radarr renvoie — jamais de chemin arbitraire
transmis à un service externe."""

from collections.abc import Iterable
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

# États de surveillance proposés, repris de l'écran d'import de Sonarr/Radarr.
# Un film n'a que « surveillé » ou « non surveillé » ; une série choisit quels
# épisodes suivre.
MONITOR_CHOICES = ("all", "existing", "future", "none")
# Disponibilité minimale d'un film, comme dans l'écran d'import de Radarr.
AVAILABILITY_CHOICES = ("announced", "inCinemas", "released")


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
    # Dossiers que Sonarr/Radarr voit sur le disque sans média rattaché, tels
    # qu'ils les voient EUX (chemins de leur conteneur).
    folders: list[str]
    suggested_folder: str | None
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


def folder_name(path: str) -> str:
    return _posix(path).rsplit("/", 1)[-1]


def folder_matches(media: Media, path: str, extra_titles: Iterable[str] = ()) -> int:
    """Score d'un dossier non mappé pour ce média. 2 : le nom du dossier porte
    un titre connu ET l'année. 1 : le titre seul. 0 : rien à voir.

    Les titres viennent du média ET des fiches trouvées chez Sonarr/Radarr
    (titre original, titres alternatifs) : un dossier nommé « OSS 117 - Cairo,
    Nest of Spies (2006) » doit être reconnu pour « OSS 117 : Le Caire, nid
    d'espions ». Comparaison sur les mots normalisés, comme le rattachement des
    torrents."""
    name_words = normalize_words(folder_name(path))
    if not name_words:
        return 0
    for title in [*_titles_of(media), *extra_titles]:
        title_words = normalize_words(title)
        if title_words and name_words[: len(title_words)] == title_words:
            return 2 if media.year and str(media.year) in name_words else 1
    return 0


def pick_folder(
    media: Media, folders: list[str], paths: list[str], extra_titles: Iterable[str] = ()
) -> str | None:
    """Dossier non mappé le plus probable. Un chemin identique à celui d'un
    fichier connu l'emporte (mêmes montages des deux côtés) ; sinon c'est le
    nom du dossier qui décide, et à défaut rien n'est proposé — mieux vaut un
    choix manuel qu'un mauvais dossier importé."""
    titles = list(extra_titles)
    known = {_posix(path).rsplit("/", 1)[0] for path in paths if "/" in _posix(path)}
    for folder in folders:
        if _posix(folder) in known:
            return folder
    # Montages différents des deux côtés (`/media/movies` ici,
    # `/data/media/movies` chez Radarr) : le NOM du dossier, lui, est le même.
    known_names = {folder_name(path).lower() for path in known}
    for folder in folders:
        if folder_name(folder).lower() in known_names:
            return folder
    scored = [(folder_matches(media, folder, titles), folder) for folder in folders]
    best = max(scored, key=lambda item: item[0], default=(0, None))
    return best[1] if best[0] > 0 else None


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
        rows = await client.get_root_folders()
        profiles = [(row["id"], row.get("name") or str(row["id"])) for row in await client.get_quality_profiles() if row.get("id")]
    except httpx.HTTPError as exc:
        raise ArrLinkError(f"{target.name} injoignable : {exc}") from exc

    # `unmappedFolders` : les dossiers présents sur le disque qu'aucun média ne
    # revendique — la liste même de l'écran « Import Existing ».
    folders = [
        folder["path"]
        for row in rows
        for folder in row.get("unmappedFolders") or []
        if isinstance(folder, dict) and folder.get("path")
    ]

    return ArrLinkPreview(
        service=service,
        instance_id=target.instance_id,
        instance_name=target.name,
        candidates=candidates,
        folders=folders,
        suggested_folder=pick_folder(
            media,
            folders,
            _media_paths(session, media),
            # Titres tels que Sonarr/Radarr les connaît : le dossier porte
            # souvent le titre original, pas celui de la bibliothèque.
            [title for candidate in candidates for title in _candidate_titles(candidate.payload)],
        ),
        quality_profiles=profiles,
        suggested_profile=profiles[0][0] if profiles else None,
    )


def pick_automatic(preview: ArrLinkPreview) -> ArrCandidate | None:
    """Candidat qu'une automatisation peut importer sans confirmation : un seul
    candidat certain, un dossier propre au média, et un profil de qualité."""
    certain = [candidate for candidate in preview.candidates if candidate.confidence == CERTAIN]
    if len(certain) != 1 or preview.suggested_folder is None or preview.suggested_profile is None:
        return None
    return certain[0]


async def link_media(
    session: Session,
    settings: Settings,
    media: Media,
    *,
    candidate_key: str,
    quality_profile_id: int,
    folder: str | None = None,
    monitor: str = "none",
    minimum_availability: str = "released",
) -> str:
    """Importe le média dans Sonarr/Radarr comme le fait leur écran d'import :
    sur un dossier qu'EUX voient comme non rattaché, par leur import en masse.
    Renvoie le titre importé.

    Chaque choix de l'interface est revérifié ici contre ce que le service
    déclare : dossier non mappé, profil de qualité, surveillance."""
    preview = await build_link_preview(session, settings, media)
    candidate = next((c for c in preview.candidates if c.key == candidate_key), None)
    if candidate is None:
        raise ArrLinkError("Fiche inconnue : relancez la recherche.")

    target_folder = folder or preview.suggested_folder
    if target_folder is None:
        raise ArrLinkError(
            f"{preview.instance_name} ne voit aucun dossier à importer pour ce média : vérifiez que sa bibliothèque "
            "est bien montée du même côté, puis relancez une analyse."
        )
    if target_folder not in preview.folders:
        raise ArrLinkError("Dossier inconnu de Sonarr/Radarr.")
    if quality_profile_id not in {profile_id for profile_id, _ in preview.quality_profiles}:
        raise ArrLinkError("Profil de qualité inconnu de Sonarr/Radarr.")
    if monitor not in MONITOR_CHOICES:
        raise ArrLinkError("État de surveillance inconnu.")
    if minimum_availability not in AVAILABILITY_CHOICES:
        raise ArrLinkError("Disponibilité minimale inconnue.")

    target = arr_target_by_id(session, settings, preview.service, preview.instance_id)
    if target is None:
        raise ArrLinkError("Instance Sonarr/Radarr introuvable.")

    body = dict(candidate.payload)
    body["path"] = target_folder
    body["qualityProfileId"] = quality_profile_id
    body["monitored"] = monitor != "none"

    try:
        if preview.service == "radarr":
            body["minimumAvailability"] = minimum_availability
            body["addOptions"] = {"searchForMovie": False}
            await target.radarr().import_movies([body])
        else:
            body["seasonFolder"] = True
            body["addOptions"] = {
                "searchForMissingEpisodes": False,
                "searchForCutoffUnmetEpisodes": False,
                "monitor": monitor,
            }
            await target.sonarr().import_series([body])
    except httpx.HTTPError as exc:
        raise ArrLinkError(_error_text(exc)) from exc
    return candidate.title


def _error_text(exc: httpx.HTTPError) -> str:
    """Message utile : Sonarr/Radarr explique le refus dans le corps de la
    réponse (dossier inaccessible, média déjà présent...), un code HTTP seul
    n'aiderait personne. Aucune clé API n'y transite (en-tête)."""
    response = getattr(exc, "response", None)
    if response is None:
        return f"{type(exc).__name__} : {exc}"
    body = " ".join((response.text or "").split())
    return f"HTTP {response.status_code}{' — ' + body[:200] if body else ''}"
