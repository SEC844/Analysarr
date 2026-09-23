"""Rattachement d'un média non suivi à Sonarr/Radarr.

Deux points sensibles, tous deux venus de tests en conditions réelles :

- les identifiants du serveur multimédia sont parfois faux (identifiant IMDb
  d'une autre œuvre), donc chaque identifiant est résolu chez Sonarr/Radarr
  puis confronté au titre et à l'année ;
- le dossier importé vient de Sonarr/Radarr (`unmappedFolders`), jamais des
  chemins vus par Analysarr : les deux conteneurs montent souvent la
  bibliothèque ailleurs, et un chemin deviné ajoutait le média sans son
  fichier."""

import asyncio
import json

import httpx
import pytest
from sqlmodel import select

from app.models.media import Media, MediaFile, MediaType
from app.services.arr_link import (
    ArrLinkError,
    build_link_preview,
    folder_matches,
    link_media,
    pick_automatic,
    pick_folder,
)

MOVIE_FOLDER = "/data/media/movies/OSS 117 - Cairo, Nest of Spies (2006)"
OTHER_FOLDER = "/data/media/movies/Un autre film (2011)"
PROFILES = [{"id": 4, "name": "HD-1080p"}, {"id": 7, "name": "Ultra-HD"}]
MOVIE = {"title": "OSS 117 : Le Caire, nid d'espions", "year": 2006, "tmdbId": 5511, "imdbId": "tt0464913"}


def add_media(session, media_type, **fields):
    media = Media(media_type=media_type, **fields)
    session.add(media)
    session.commit()
    session.refresh(media)
    return media


def radarr_server(
    calls: list[tuple[str, str]],
    *,
    tmdb: dict | None = None,
    imdb: dict | None = None,
    search=(),
    unmapped: tuple[str, ...] = (MOVIE_FOLDER, OTHER_FOLDER),
    imported: list[dict] | None = None,
):
    """Radarr minimal : recherche, dossiers non rattachés, profils et import en
    masse — exactement ce qu'utilise l'écran « Import Existing »."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if path == "/api/v3/movie/lookup/tmdb":
            return httpx.Response(200, json=tmdb) if tmdb else httpx.Response(404, json={})
        if path == "/api/v3/movie/lookup/imdb":
            return httpx.Response(200, json=imdb) if imdb else httpx.Response(404, json={})
        if path == "/api/v3/movie/lookup":
            return httpx.Response(200, json=list(search))
        if path == "/api/v3/rootfolder":
            return httpx.Response(
                200,
                json=[{"path": "/data/media/movies", "unmappedFolders": [{"path": f} for f in unmapped]}],
            )
        if path == "/api/v3/qualityprofile":
            return httpx.Response(200, json=PROFILES)
        if path == "/api/v3/movie/import":
            if imported is not None:
                imported.extend(json.loads(request.content))
            return httpx.Response(201, json=[{"id": 51}])
        return httpx.Response(404)

    return handler


def sonarr_server(calls: list[tuple[str, str]], results: dict[str, list[dict]], *, imported: list[dict] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        calls.append((request.method, path))
        if path == "/api/v3/series/lookup":
            return httpx.Response(200, json=results.get(params.get("term", ""), []))
        if path == "/api/v3/rootfolder":
            return httpx.Response(
                200, json=[{"path": "/data/tv", "unmappedFolders": [{"path": "/data/tv/Game of Thrones"}]}]
            )
        if path == "/api/v3/qualityprofile":
            return httpx.Response(200, json=PROFILES)
        if path == "/api/v3/series/import":
            if imported is not None:
                imported.extend(json.loads(request.content))
            return httpx.Response(201, json=[{"id": 33}])
        return httpx.Response(404)

    return handler


def test_an_identifier_that_points_elsewhere_is_rejected(session, settings, fake_http):
    """Bug réel : le serveur multimédia donnait un identifiant IMDb d'une autre
    œuvre. Un candidat dont le titre ne correspond pas n'est jamais proposé."""
    fake_http["http://radarr"] = radarr_server(
        [], imdb={"title": "Un tout autre film", "year": 1998, "tmdbId": 999, "imdbId": "tt999"}, search=[MOVIE]
    )
    media = add_media(session, MediaType.movie, title="OSS 117 : Le Caire, nid d'espions", year=2006, imdb_id="tt999")

    preview = asyncio.run(build_link_preview(session, settings, media))

    assert [candidate.title for candidate in preview.candidates] == ["OSS 117 : Le Caire, nid d'espions"]
    assert preview.candidates[0].confidence == "probable"  # trouvé par titre, pas par identifiant


def test_a_matching_identifier_gives_a_certain_candidate(session, settings, fake_http):
    fake_http["http://radarr"] = radarr_server([], tmdb=MOVIE)
    media = add_media(session, MediaType.movie, title="OSS 117 : Le Caire, nid d'espions", year=2006, tmdb_id=5511)

    preview = asyncio.run(build_link_preview(session, settings, media))

    assert len(preview.candidates) == 1
    candidate = preview.candidates[0]
    assert (candidate.confidence, candidate.key, candidate.tmdb_id) == ("certain", "tmdb:5511", 5511)


def test_the_series_lookup_uses_the_only_prefix_sonarr_understands(session, settings, fake_http):
    """Sonarr ne comprend que `tvdb:` (SkyHookProxy) : un `imdb:` retomberait
    en recherche texte, donc on ne l'envoie jamais."""
    fake_http["http://sonarr"] = sonarr_server(
        [],
        {
            "tvdb:121361": [{"title": "Game of Thrones", "year": 2011, "tvdbId": 121361, "imdbId": "tt0944947"}],
            "Game of Thrones": [{"title": "Game of Thrones", "year": 2011, "tvdbId": 121361}],
        },
    )
    media = add_media(session, MediaType.series, title="Game of Thrones", year=2011, tvdb_id=121361)

    preview = asyncio.run(build_link_preview(session, settings, media))

    assert preview.service == "sonarr"
    assert [candidate.key for candidate in preview.candidates] == ["tvdb:121361"]
    assert preview.candidates[0].confidence == "certain"
    assert preview.suggested_folder == "/data/tv/Game of Thrones"


def test_the_folder_comes_from_radarr_not_from_our_own_paths(session, settings, fake_http):
    """Bug réel : Analysarr voit `/media/movies`, Radarr `/data/media/movies`.
    Le dossier proposé est celui de Radarr, reconnu par son nom."""
    fake_http["http://radarr"] = radarr_server([], tmdb=MOVIE)
    media = add_media(session, MediaType.movie, title="OSS 117 : Le Caire, nid d'espions", year=2006, tmdb_id=5511)
    session.add(MediaFile(media_id=media.id, path="/media/movies/OSS 117 - Cairo, Nest of Spies (2006)/film.mkv"))
    session.commit()

    preview = asyncio.run(build_link_preview(session, settings, media))

    assert preview.folders == [MOVIE_FOLDER, OTHER_FOLDER]
    assert preview.suggested_folder == MOVIE_FOLDER
    assert pick_automatic(preview) is not None


def test_the_folder_is_matched_on_its_name_including_original_titles():
    media = Media(
        media_type=MediaType.movie,
        title="OSS 117 : Le Caire, nid d'espions",
        year=2006,
        alt_titles="OSS 117 Cairo Nest of Spies",
    )
    # Titre alternatif + année : correspondance la plus sûre.
    assert folder_matches(media, MOVIE_FOLDER) == 2
    assert folder_matches(media, OTHER_FOLDER) == 0
    assert pick_folder(media, [OTHER_FOLDER, MOVIE_FOLDER], []) == MOVIE_FOLDER
    # Aucun dossier ne ressemble au média : rien n'est proposé au hasard.
    assert pick_folder(media, [OTHER_FOLDER], []) is None


def test_an_identical_path_wins_over_the_name():
    media = Media(media_type=MediaType.movie, title="Autre chose")
    assert pick_folder(media, [OTHER_FOLDER, MOVIE_FOLDER], [f"{MOVIE_FOLDER}/film.mkv"]) == MOVIE_FOLDER


def test_importing_uses_radarrs_bulk_import_with_its_own_path(session, settings, fake_http):
    calls: list[tuple[str, str]] = []
    imported: list[dict] = []
    fake_http["http://radarr"] = radarr_server(calls, tmdb=MOVIE, imported=imported)
    media = add_media(session, MediaType.movie, title="OSS 117 : Le Caire, nid d'espions", year=2006, tmdb_id=5511)

    title = asyncio.run(
        link_media(session, settings, media, candidate_key="tmdb:5511", quality_profile_id=4, folder=MOVIE_FOLDER)
    )

    assert title == "OSS 117 : Le Caire, nid d'espions"
    # Import en masse : c'est lui qui fait reprendre les fichiers déjà en place.
    assert ("POST", "/api/v3/movie/import") in calls
    assert len(imported) == 1
    sent = imported[0]
    assert sent["path"] == MOVIE_FOLDER and "rootFolderPath" not in sent
    assert sent["qualityProfileId"] == 4 and sent["minimumAvailability"] == "released"
    assert sent["addOptions"] == {"searchForMovie": False}


def test_importing_a_series_goes_through_sonarrs_bulk_import(session, settings, fake_http):
    calls: list[tuple[str, str]] = []
    imported: list[dict] = []
    fake_http["http://sonarr"] = sonarr_server(
        calls, {"tvdb:121361": [{"title": "Game of Thrones", "year": 2011, "tvdbId": 121361}]}, imported=imported
    )
    media = add_media(session, MediaType.series, title="Game of Thrones", year=2011, tvdb_id=121361)

    asyncio.run(
        link_media(
            session,
            settings,
            media,
            candidate_key="tvdb:121361",
            quality_profile_id=7,
            folder="/data/tv/Game of Thrones",
            monitor="existing",
        )
    )

    assert ("POST", "/api/v3/series/import") in calls
    sent = imported[0]
    assert sent["path"] == "/data/tv/Game of Thrones" and sent["monitored"] is True
    assert sent["addOptions"]["monitor"] == "existing"


def test_linking_refuses_what_the_service_does_not_declare(session, settings, fake_http):
    calls: list[tuple[str, str]] = []
    fake_http["http://radarr"] = radarr_server(calls, tmdb=MOVIE)
    media = add_media(session, MediaType.movie, title="OSS 117 : Le Caire, nid d'espions", year=2006, tmdb_id=5511)

    for kwargs in (
        {"candidate_key": "tmdb:1", "quality_profile_id": 4, "folder": MOVIE_FOLDER},
        {"candidate_key": "tmdb:5511", "quality_profile_id": 999, "folder": MOVIE_FOLDER},
        {"candidate_key": "tmdb:5511", "quality_profile_id": 4, "folder": "/etc"},
        {"candidate_key": "tmdb:5511", "quality_profile_id": 4, "folder": MOVIE_FOLDER, "monitor": "rm -rf"},
    ):
        with pytest.raises(ArrLinkError):
            asyncio.run(link_media(session, settings, media, **kwargs))
    assert ("POST", "/api/v3/movie/import") not in calls


def test_linking_refuses_when_no_folder_is_available(session, settings, fake_http):
    """Bibliothèque montée d'un seul côté : mieux vaut refuser et l'expliquer
    que créer un média sans fichier."""
    calls: list[tuple[str, str]] = []
    fake_http["http://radarr"] = radarr_server(calls, tmdb=MOVIE, unmapped=())
    media = add_media(session, MediaType.movie, title="OSS 117 : Le Caire, nid d'espions", year=2006, tmdb_id=5511)

    with pytest.raises(ArrLinkError):
        asyncio.run(link_media(session, settings, media, candidate_key="tmdb:5511", quality_profile_id=4))
    assert ("POST", "/api/v3/movie/import") not in calls


def test_an_already_tracked_media_is_refused(session, settings):
    media = add_media(session, MediaType.movie, title="Matrix", radarr_id=42)
    with pytest.raises(ArrLinkError):
        asyncio.run(build_link_preview(session, settings, media))


def test_the_endpoint_needs_a_session(client, session, settings):
    media = add_media(session, MediaType.movie, title="Matrix")
    assert client.get(f"/api/media/{media.id}/arr-link").status_code == 401


def test_the_automation_action_only_pairs_with_the_right_trigger(admin_client):
    body = {
        "name": "Rattachement",
        "trigger": "orphan_detected",
        "action": "link_to_arr",
        "conditions": {"media_types": []},
        "max_actions": 5,
        "dry_run": False,
    }
    assert admin_client.post("/api/automations", json=body).status_code == 400
    ok = admin_client.post("/api/automations", json=body | {"trigger": "untracked_detected"})
    assert ok.status_code == 201 and ok.json()["action"] == "link_to_arr"


def test_media_rows_are_untouched_until_the_next_scan(session, settings, fake_http):
    """Le rattachement n'écrit pas d'identifiant Sonarr/Radarr en base : c'est
    l'analyse qui suit qui redonne son identité au média (une seule source de
    vérité, comme pour le reste du cache)."""
    fake_http["http://radarr"] = radarr_server([], tmdb=MOVIE)
    media = add_media(session, MediaType.movie, title="OSS 117 : Le Caire, nid d'espions", year=2006, tmdb_id=5511)

    asyncio.run(
        link_media(session, settings, media, candidate_key="tmdb:5511", quality_profile_id=4, folder=MOVIE_FOLDER)
    )

    session.expire_all()
    assert session.exec(select(Media)).one().radarr_id is None


def test_the_folder_name_is_enough_when_mounts_differ(session, settings, fake_http):
    """Cas réel : mêmes dossiers, montages différents. Le nom du dossier suffit
    à retrouver celui que Radarr voit."""
    fake_http["http://radarr"] = radarr_server([], tmdb=MOVIE)
    media = add_media(session, MediaType.movie, title="OSS 117 : Le Caire, nid d'espions", year=2006, tmdb_id=5511)
    session.add(MediaFile(media_id=media.id, path="/media/movies/OSS 117 - Cairo, Nest of Spies (2006)/film.mkv"))
    session.commit()

    preview = asyncio.run(build_link_preview(session, settings, media))

    assert preview.suggested_folder == MOVIE_FOLDER
