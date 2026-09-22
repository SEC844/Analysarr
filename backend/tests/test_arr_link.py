"""Rattachement d'un média non suivi à Sonarr/Radarr.

Le point sensible : les identifiants du serveur multimédia sont parfois faux
(identifiant IMDb d'une autre œuvre, vu en conditions réelles). Chaque
identifiant est donc résolu par Sonarr/Radarr, puis confronté au titre et à
l'année du média avant d'être proposé."""

import asyncio
import json

import httpx
import pytest
from sqlmodel import select

from app.models.media import Media, MediaFile, MediaType
from app.services.arr_link import (
    ArrLinkError,
    build_link_preview,
    link_media,
    media_folder,
    pick_automatic,
    suggest_root,
)

ROOT_FOLDERS = [
    {"path": "/data/media/films", "unmappedFolders": [{"path": "/data/media/films/Matrix (1999)"}]},
    {"path": "/data/media/series", "unmappedFolders": []},
]
PROFILES = [{"id": 4, "name": "HD-1080p"}, {"id": 7, "name": "Ultra-HD"}]


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
    bodies: list[dict] | None = None,
):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if path == "/api/v3/movie" and request.method == "POST" and bodies is not None:
            bodies.append(json.loads(request.content))
        if path == "/api/v3/movie/lookup/tmdb":
            return httpx.Response(200, json=tmdb) if tmdb else httpx.Response(404, json={})
        if path == "/api/v3/movie/lookup/imdb":
            return httpx.Response(200, json=imdb) if imdb else httpx.Response(404, json={})
        if path == "/api/v3/movie/lookup":
            return httpx.Response(200, json=list(search))
        if path == "/api/v3/rootfolder":
            return httpx.Response(200, json=ROOT_FOLDERS)
        if path == "/api/v3/qualityprofile":
            return httpx.Response(200, json=PROFILES)
        if path == "/api/v3/movie":
            return httpx.Response(201, json={"id": 51})
        if path == "/api/v3/command":
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    return handler


def sonarr_server(calls: list[tuple[str, str]], results: dict[str, list[dict]]):
    def handler(request: httpx.Request) -> httpx.Response:
        path, params = request.url.path, request.url.params
        calls.append((request.method, path))
        if path == "/api/v3/series/lookup":
            return httpx.Response(200, json=results.get(params.get("term", ""), []))
        if path == "/api/v3/rootfolder":
            return httpx.Response(200, json=ROOT_FOLDERS)
        if path == "/api/v3/qualityprofile":
            return httpx.Response(200, json=PROFILES)
        if path == "/api/v3/series":
            return httpx.Response(201, json={"id": 33})
        if path == "/api/v3/command":
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    return handler


def test_an_identifier_that_points_elsewhere_is_rejected(session, settings, fake_http):
    """Bug réel : le serveur multimédia donnait un identifiant IMDb d'une autre
    œuvre. Un candidat dont le titre ne correspond pas n'est jamais proposé."""
    calls: list[tuple[str, str]] = []
    fake_http["http://radarr"] = radarr_server(
        calls,
        imdb={"title": "Un tout autre film", "year": 1998, "tmdbId": 999, "imdbId": "tt999"},
        search=[{"title": "Le Flambeau", "year": 2024, "tmdbId": 12, "imdbId": "tt12"}],
    )
    media = add_media(session, MediaType.movie, title="Le Flambeau", year=2024, imdb_id="tt999")

    preview = asyncio.run(build_link_preview(session, settings, media))

    assert [candidate.title for candidate in preview.candidates] == ["Le Flambeau"]
    assert preview.candidates[0].confidence == "probable"  # trouvé par titre, pas par identifiant


def test_a_matching_identifier_gives_a_certain_candidate(session, settings, fake_http):
    calls: list[tuple[str, str]] = []
    fake_http["http://radarr"] = radarr_server(
        calls, tmdb={"title": "Matrix", "year": 1999, "tmdbId": 603, "imdbId": "tt0133093"}
    )
    media = add_media(session, MediaType.movie, title="Matrix", year=1999, tmdb_id=603)

    preview = asyncio.run(build_link_preview(session, settings, media))

    assert len(preview.candidates) == 1
    candidate = preview.candidates[0]
    assert (candidate.confidence, candidate.key, candidate.tmdb_id) == ("certain", "tmdb:603", 603)
    assert pick_automatic(preview) is None  # aucun dossier racine déduit : pas d'ajout automatique


def test_the_series_lookup_uses_the_only_prefix_sonarr_understands(session, settings, fake_http):
    """Sonarr ne comprend que `tvdb:` (SkyHookProxy) : un `imdb:` retomberait
    en recherche texte, donc on ne l'envoie jamais."""
    calls: list[tuple[str, str]] = []
    fake_http["http://sonarr"] = sonarr_server(
        calls,
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


def test_the_imported_folder_is_the_one_holding_the_files(session, settings, fake_http):
    """Comme l'écran « Import Existing » : on importe le dossier du média, pas
    un dossier racine — sinon Radarr crée un dossier à côté des fichiers."""
    calls: list[tuple[str, str]] = []
    fake_http["http://radarr"] = radarr_server(
        calls, tmdb={"title": "Matrix", "year": 1999, "tmdbId": 603, "imdbId": "tt0133093"}
    )
    media = add_media(session, MediaType.movie, title="Matrix", year=1999, tmdb_id=603)
    session.add(MediaFile(media_id=media.id, path="/data/media/films/Matrix (1999)/Matrix.mkv", size=1))
    session.commit()

    preview = asyncio.run(build_link_preview(session, settings, media))

    assert preview.folder == "/data/media/films/Matrix (1999)"
    assert preview.root_folder == "/data/media/films"
    # Radarr voit encore ce dossier comme non rattaché : bon signe.
    assert preview.folder_unmapped
    assert pick_automatic(preview) is not None


def test_media_folder_never_returns_a_root_folder():
    """Un film posé à plat dans le dossier racine n'a pas de dossier à lui :
    l'importer ferait entrer toute la bibliothèque."""
    assert media_folder(["/data/media/films/Matrix (1999)/Matrix.mkv"], None, "/data/media/films") == (
        "/data/media/films/Matrix (1999)"
    )
    assert media_folder(["/data/media/films/Matrix.mkv"], None, "/data/media/films") is None
    # Série rangée en saisons : on remonte au dossier de la série.
    assert media_folder(
        ["/data/media/series/Dark/Season 01/S01E01.mkv"], None, "/data/media/series"
    ) == "/data/media/series/Dark"
    # Dossier hors de tout dossier racine : rien à proposer.
    assert media_folder(["/autre/Film/Film.mkv"], None, "/data/media/films") is None


def test_suggest_root_keeps_the_deepest_match():
    roots = ["/data/media", "/data/media/films"]
    assert suggest_root(roots, ["/data/media/films/Film/Film.mkv"], None) == "/data/media/films"
    assert suggest_root(roots, [], "/data/media/series/Série") == "/data/media"
    assert suggest_root(roots, ["/autre/chemin/Film.mkv"], None) is None


def test_linking_refuses_what_sonarr_does_not_declare(session, settings, fake_http):
    """Le choix de l'interface est revérifié côté serveur, et le dossier n'est
    même pas fourni par elle : il est recalculé ici."""
    calls: list[tuple[str, str]] = []
    fake_http["http://radarr"] = radarr_server(
        calls, tmdb={"title": "Matrix", "year": 1999, "tmdbId": 603, "imdbId": "tt0133093"}
    )
    media = add_media(session, MediaType.movie, title="Matrix", year=1999, tmdb_id=603)
    session.add(MediaFile(media_id=media.id, path="/data/media/films/Matrix (1999)/Matrix.mkv", size=1))
    session.commit()

    with pytest.raises(ArrLinkError):  # profil inconnu
        asyncio.run(link_media(session, settings, media, candidate_key="tmdb:603", quality_profile_id=999))
    with pytest.raises(ArrLinkError):  # fiche inconnue
        asyncio.run(link_media(session, settings, media, candidate_key="tmdb:1", quality_profile_id=4))
    with pytest.raises(ArrLinkError):  # état de surveillance inconnu
        asyncio.run(
            link_media(session, settings, media, candidate_key="tmdb:603", quality_profile_id=4, monitor="rm -rf")
        )
    assert ("POST", "/api/v3/movie") not in calls


def test_linking_refuses_a_media_without_its_own_folder(session, settings, fake_http):
    calls: list[tuple[str, str]] = []
    fake_http["http://radarr"] = radarr_server(
        calls, tmdb={"title": "Matrix", "year": 1999, "tmdbId": 603, "imdbId": "tt0133093"}
    )
    media = add_media(session, MediaType.movie, title="Matrix", year=1999, tmdb_id=603)
    session.add(MediaFile(media_id=media.id, path="/data/media/films/Matrix.mkv", size=1))
    session.commit()

    with pytest.raises(ArrLinkError):
        asyncio.run(link_media(session, settings, media, candidate_key="tmdb:603", quality_profile_id=4))
    assert ("POST", "/api/v3/movie") not in calls


def test_linking_imports_the_folder_and_asks_for_a_rescan(session, settings, fake_http):
    calls: list[tuple[str, str]] = []
    bodies: list[dict] = []
    fake_http["http://radarr"] = radarr_server(
        calls, tmdb={"title": "Matrix", "year": 1999, "tmdbId": 603, "imdbId": "tt0133093"}, bodies=bodies
    )
    media = add_media(session, MediaType.movie, title="Matrix", year=1999, tmdb_id=603)
    session.add(MediaFile(media_id=media.id, path="/data/media/films/Matrix (1999)/Matrix.mkv", size=1))
    session.commit()

    title = asyncio.run(link_media(session, settings, media, candidate_key="tmdb:603", quality_profile_id=4))

    assert title == "Matrix"
    assert ("POST", "/api/v3/movie") in calls and ("POST", "/api/v3/command") in calls
    sent = bodies[0]
    # Dossier du média, jamais le dossier racine, et aucune recherche lancée :
    # les fichiers présents suffisent.
    assert sent["path"] == "/data/media/films/Matrix (1999)"
    assert "rootFolderPath" not in sent
    assert sent["qualityProfileId"] == 4 and sent["monitored"] is True
    assert sent["addOptions"] == {"searchForMovie": False}


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
    assert ok.status_code == 201
    assert ok.json()["action"] == "link_to_arr"
    assert admin_client.get("/api/automations").json()[0]["trigger"] == "untracked_detected"


def test_media_rows_are_untouched_until_the_next_scan(session, settings, fake_http):
    """Le rattachement n'écrit pas d'identifiant Sonarr/Radarr en base : c'est
    l'analyse qui suit qui redonne son identité au média (une seule source de
    vérité, comme pour le reste du cache)."""
    calls: list[tuple[str, str]] = []
    fake_http["http://radarr"] = radarr_server(
        calls, tmdb={"title": "Matrix", "year": 1999, "tmdbId": 603, "imdbId": "tt0133093"}
    )
    media = add_media(session, MediaType.movie, title="Matrix", year=1999, tmdb_id=603)

    session.add(MediaFile(media_id=media.id, path="/data/media/films/Matrix (1999)/Matrix.mkv", size=1))
    session.commit()
    asyncio.run(link_media(session, settings, media, candidate_key="tmdb:603", quality_profile_id=4))

    session.expire_all()
    assert session.exec(select(Media)).one().radarr_id is None
