"""Test de caractérisation du scan complet.

Il fige le résultat ACTUEL d'un scan sur une bibliothèque représentative, pour
que la restructuration du moteur de scan ne change rien sans qu'on le voie :
statuts, fichiers retenus, torrents rattachés, hardlinks et trackers.

La bibliothèque est faite de vrais fichiers et de vrais hardlinks (le scan lit
les inodes sur le disque) ; seuls les services externes sont simulés :
- Matrix : sain, seedé sur deux trackers dont une copie cross-seed hardlinkée ;
- Inception : doublon après un upgrade Radarr, l'ancien torrent est orphelin ;
- Arrival : copie non hardlinkée de même taille, rattachée par son nom ;
- Alien : suivi par Radarr, aucun torrent ;
- Heat : dans la bibliothèque, suivi par aucun Radarr ;
- Dark : pack saison hardlinké, un épisode suivi par Sonarr absent du serveur ;
- un torrent sans rapport avec la bibliothèque."""

import asyncio
import json
import os
from pathlib import Path

import httpx
from sqlmodel import select

from app.models.media import Media, MediaFile, ScanRun, Torrent
from app.services import scan

TRACKER_A = "https://tracker-a.example/announce?passkey=secret"
TRACKER_B = "https://tracker-b.example/announce/secret"


def _write(path: Path, size: int) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return str(path)


def _link(source: str, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    os.link(source, target)
    return str(target)


def build_world(tmp_path: Path) -> dict:
    media, torrents = tmp_path / "media", tmp_path / "torrents"
    movies, tv = media / "movies", media / "tv"
    for root in (media, torrents):
        root.mkdir(parents=True, exist_ok=True)

    matrix = _write(movies / "Matrix (1999)" / "Matrix.1999.1080p.mkv", 1000)
    _link(matrix, torrents / "movies" / "Matrix.1999.1080p.mkv")
    _link(matrix, torrents / "cross-seed" / "Matrix.1999.1080p.mkv")

    inception_new = _write(movies / "Inception (2010)" / "Inception.2010.2160p.mkv", 3000)
    inception_old = _write(movies / "Inception (2010)" / "Inception.2010.1080p.mkv", 2000)
    _link(inception_new, torrents / "movies" / "Inception.2010.2160p.mkv")
    _write(torrents / "movies" / "Inception.2010.720p.mkv", 1700)

    arrival = _write(movies / "Arrival (2016)" / "Arrival.2016.1080p.mkv", 1500)
    _write(torrents / "movies" / "Arrival.2016.1080p.mkv", 1500)

    alien = _write(movies / "Alien (1979)" / "Alien.1979.mkv", 900)
    heat = _write(movies / "Heat (1995)" / "Heat.1995.mkv", 800)

    dark = tv / "Dark"
    dark_e1 = _write(dark / "Season 01" / "Dark.S01E01.mkv", 1100)
    dark_e2 = _write(dark / "Season 01" / "Dark.S01E02.mkv", 1200)
    _link(dark_e1, torrents / "tv" / "Dark.S01" / "Dark.S01E01.mkv")
    _link(dark_e2, torrents / "tv" / "Dark.S01" / "Dark.S01E02.mkv")

    _write(torrents / "other" / "linux.iso", 50)

    return {
        "media": str(media),
        "torrents": str(torrents),
        "matrix": matrix,
        "inception_new": inception_new,
        "inception_old": inception_old,
        "arrival": arrival,
        "alien": alien,
        "heat": heat,
        "dark": str(dark),
        "dark_e1": dark_e1,
        "dark_e2": dark_e2,
    }


def _source(path: str, size: int) -> dict:
    return {"Path": path, "Size": size}


def emby_handler(w: dict):
    movies = [
        {
            "Id": "e-matrix",
            "Name": "Matrix",
            "ProductionYear": 1999,
            "ProviderIds": {"Tmdb": "603"},
            "ImageTags": {"Primary": "t1"},
            "MediaSources": [_source(w["matrix"], 1000)],
        },
        {
            "Id": "e-inception",
            "Name": "Inception",
            "ProductionYear": 2010,
            "ProviderIds": {"Tmdb": "27205"},
            "MediaSources": [_source(w["inception_new"], 3000), _source(w["inception_old"], 2000)],
        },
        {
            "Id": "e-arrival",
            "Name": "Arrival",
            "ProductionYear": 2016,
            "ProviderIds": {"Tmdb": "329865"},
            "MediaSources": [_source(w["arrival"], 1500)],
        },
        {
            "Id": "e-alien",
            "Name": "Alien",
            "ProductionYear": 1979,
            "ProviderIds": {"Tmdb": "348"},
            "MediaSources": [_source(w["alien"], 900)],
        },
        {
            "Id": "e-heat",
            "Name": "Heat",
            "ProductionYear": 1995,
            "ProviderIds": {"Tmdb": "949", "Imdb": "tt0113277"},
            "MediaSources": [_source(w["heat"], 800)],
        },
    ]
    series = [
        {"Id": "s-dark", "Name": "Dark", "ProductionYear": 2017, "Path": w["dark"], "ProviderIds": {"Tvdb": "334824"}},
    ]
    episodes = [
        {"Id": "ep-1", "ParentIndexNumber": 1, "IndexNumber": 1, "MediaSources": [_source(w["dark_e1"], 1100)]},
        {"Id": "ep-2", "ParentIndexNumber": 1, "IndexNumber": 2, "MediaSources": [_source(w["dark_e2"], 1200)]},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if request.url.path == "/Items":
            if params.get("ParentId") == "s-dark":
                return httpx.Response(200, json={"Items": episodes})
            kind = params.get("IncludeItemTypes")
            return httpx.Response(
                200, json={"Items": movies if kind == "Movie" else series if kind == "Series" else []}
            )
        if request.url.path == "/Users":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    return handler


RADARR_MOVIES = [
    {
        "id": 1,
        "title": "Matrix",
        "year": 1999,
        "tmdbId": 603,
        "hasFile": True,
        "path": "/movies/Matrix (1999)",
        "movieFile": {"id": 11, "path": "/movies/Matrix (1999)/Matrix.1999.1080p.mkv", "size": 1000},
    },
    {
        "id": 2,
        "title": "Inception",
        "year": 2010,
        "tmdbId": 27205,
        "hasFile": True,
        "path": "/movies/Inception (2010)",
        "movieFile": {"id": 22, "path": "/movies/Inception (2010)/Inception.2010.2160p.mkv", "size": 3000},
    },
    {
        "id": 3,
        "title": "Arrival",
        "year": 2016,
        "tmdbId": 329865,
        "hasFile": True,
        "path": "/movies/Arrival (2016)",
        "movieFile": {"id": 33, "path": "/movies/Arrival (2016)/Arrival.2016.1080p.mkv", "size": 1500},
    },
    {
        "id": 4,
        "title": "Alien",
        "year": 1979,
        "tmdbId": 348,
        "hasFile": True,
        "path": "/movies/Alien (1979)",
        "movieFile": {"id": 44, "path": "/movies/Alien (1979)/Alien.1979.mkv", "size": 900},
    },
]
RADARR_HISTORY = {1: ["MATRIXHASH"], 2: ["INCEPTION4K", "INCEPTIONOLD"]}


def radarr_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/v3/movie":
        return httpx.Response(200, json=RADARR_MOVIES)
    if path == "/api/v3/history/movie":
        movie_id = int(request.url.params["movieId"])
        return httpx.Response(200, json=[{"downloadId": h} for h in RADARR_HISTORY.get(movie_id, [])])
    if path == "/api/v3/queue":
        return httpx.Response(200, json={"records": [], "totalRecords": 0})
    return httpx.Response(404)


def sonarr_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/api/v3/series":
        return httpx.Response(
            200,
            json=[
                {
                    "id": 10,
                    "title": "Dark",
                    "year": 2017,
                    "tvdbId": 334824,
                    "path": "/tv/Dark",
                    "statistics": {"episodeFileCount": 3},
                }
            ],
        )
    if path == "/api/v3/episodefile":
        return httpx.Response(
            200,
            json=[
                {"id": 101, "path": "/tv/Dark/Season 01/Dark.S01E01.mkv", "size": 1100, "seasonNumber": 1},
                {"id": 102, "path": "/tv/Dark/Season 01/Dark.S01E02.mkv", "size": 1200, "seasonNumber": 1},
                {"id": 103, "path": "/tv/Dark/Season 01/Dark.S01E03.mkv", "size": 1300, "seasonNumber": 1},
            ],
        )
    if path == "/api/v3/episode":
        return httpx.Response(
            200,
            json=[
                {"id": 1000 + n, "seasonNumber": 1, "episodeNumber": n, "hasFile": True, "episodeFileId": 100 + n}
                for n in (1, 2, 3)
            ],
        )
    if path == "/api/v3/history/series":
        return httpx.Response(200, json=[{"downloadId": "DARKPACK"}])
    if path == "/api/v3/queue":
        return httpx.Response(200, json={"records": [], "totalRecords": 0})
    return httpx.Response(404)


def qbit_handler(w: dict):
    t = w["torrents"]
    torrents = [
        ("matrixhash", "Matrix.1999.1080p.mkv", f"{t}/movies", "radarr", 1000, [TRACKER_A]),
        ("matrixcross", "Matrix.1999.1080p.mkv", f"{t}/cross-seed", "cross-seed", 1000, [TRACKER_B]),
        ("inception4k", "Inception.2010.2160p.mkv", f"{t}/movies", "radarr", 3000, [TRACKER_A]),
        ("inceptionold", "Inception.2010.720p.mkv", f"{t}/movies", "radarr", 1700, [TRACKER_A]),
        ("arrivalcopy", "Arrival.2016.1080p.mkv", f"{t}/movies", "radarr", 1500, [TRACKER_B]),
        ("darkpack", "Dark.S01", f"{t}/tv", "sonarr", 2300, [TRACKER_A]),
        ("linuxiso", "linux.iso", f"{t}/other", "", 50, [TRACKER_B]),
    ]
    files = {
        "darkpack": [
            {"name": "Dark.S01/Dark.S01E01.mkv", "size": 1100},
            {"name": "Dark.S01/Dark.S01E02.mkv", "size": 1200},
        ],
    }
    info = [
        {
            "hash": h,
            "name": name,
            "save_path": save,
            "content_path": f"{save}/{name}",
            "category": category,
            "size": size,
            "ratio": 1.5,
            "num_seeds": 3,
            "num_leechs": 0,
            "added_on": 1_700_000_000,
            "completion_on": 1_700_000_100,
        }
        for h, name, save, category, size, _ in torrents
    ]
    trackers = {h: urls for h, *_, urls in torrents}

    def handler(request: httpx.Request) -> httpx.Response:
        path, wanted = request.url.path, request.url.params.get("hash")
        if path == "/api/v2/auth/login":
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=abc; path=/"})
        if path == "/api/v2/torrents/info":
            return httpx.Response(200, json=info)
        if path == "/api/v2/torrents/trackers":
            return httpx.Response(200, json=[{"url": url, "status": 2} for url in trackers[wanted]])
        if path == "/api/v2/torrents/files":
            entry = next(i for i in info if i["hash"] == wanted)
            return httpx.Response(200, json=files.get(wanted, [{"name": entry["name"], "size": entry["size"]}]))
        return httpx.Response(404)

    return handler


def snapshot(session, w: dict) -> dict:
    """État du cache après le scan, sans identifiant généré ni chemin absolu."""

    def rel(path: str | None) -> str | None:
        # Chemins de la bibliothèque rendus relatifs ; ceux vus par Sonarr/Radarr
        # (autre montage) restent tels quels.
        if path and path.startswith(w["media"]):
            return os.path.relpath(path, w["media"]).replace("\\", "/")
        return path

    medias = {}
    for media in session.exec(select(Media)).all():
        files = session.exec(select(MediaFile).where(MediaFile.media_id == media.id)).all()
        torrents = session.exec(select(Torrent).where(Torrent.media_id == media.id)).all()
        medias[f"{media.media_type.value}:{media.title}"] = {
            "statuses": media.statuses,
            "reclaimable_bytes": media.reclaimable_bytes,
            "total_size": media.total_size,
            "arr": (media.radarr_id, media.sonarr_id),
            "emby_item_id": media.emby_item_id,
            "missing_emby_episodes": media.missing_emby_episodes,
            "root_path": rel(media.root_path),
            "files": sorted(
                (rel(f.path), f.size, f.is_current, f.episode_label, f.arr_file_id, f.inode is not None) for f in files
            ),
            "torrents": sorted(
                (
                    t.hash,
                    bool(t.is_hardlinked),
                    t.repairable,
                    t.matched_by_name,
                    t.category,
                    tuple(sorted(tr["domain"] for tr in json.loads(t.trackers_json))),
                )
                for t in torrents
            ),
        }
    unmatched = sorted(t.hash for t in session.exec(select(Torrent).where(Torrent.media_id == None)).all())  # noqa: E711
    run = session.exec(select(ScanRun)).one()
    return {
        "medias": medias,
        "unmatched": unmatched,
        "run": (
            run.status.value,
            run.media_count,
            run.duplicate_count,
            run.orphan_count,
            run.non_hardlink_count,
            run.tracker_unique_count,
            run.qbittorrent_torrent_count,
            run.qbittorrent_matched_count,
        ),
    }


def test_full_scan_result_is_unchanged(fake_http, portable_inodes, session, settings, tmp_path):
    w = build_world(tmp_path)
    settings.emby_library_path = w["media"]
    settings.qbittorrent_download_path = w["torrents"]
    session.add(settings)
    session.commit()
    fake_http["http://emby"] = emby_handler(w)
    fake_http["http://radarr"] = radarr_handler
    fake_http["http://sonarr"] = sonarr_handler
    fake_http["http://qbit"] = qbit_handler(w)

    asyncio.run(scan.run_scan())

    session.expire_all()
    assert snapshot(session, w) == EXPECTED


EXPECTED: dict = {
    "medias": {
        "movie:Alien": {
            "arr": (4, None),
            "emby_item_id": "e-alien",
            "files": [("movies/Alien (1979)/Alien.1979.mkv", 900, True, None, 44, True)],
            "missing_emby_episodes": "",
            "reclaimable_bytes": 0,
            "root_path": "/movies/Alien (1979)",
            "statuses": "manquant_qbit",
            "torrents": [],
            "total_size": 900,
        },
        "movie:Arrival": {
            "arr": (3, None),
            "emby_item_id": "e-arrival",
            "files": [("movies/Arrival (2016)/Arrival.2016.1080p.mkv", 1500, True, None, 33, True)],
            "missing_emby_episodes": "",
            "reclaimable_bytes": 0,
            "root_path": "/movies/Arrival (2016)",
            "statuses": "non_hardlink,tracker_unique",
            "torrents": [("arrivalcopy", False, True, True, "radarr", ("tracker-b.example",))],
            "total_size": 1500,
        },
        "movie:Heat": {
            "arr": (None, None),
            "emby_item_id": "e-heat",
            "files": [("movies/Heat (1995)/Heat.1995.mkv", 800, True, None, None, True)],
            "missing_emby_episodes": "",
            "reclaimable_bytes": 0,
            "root_path": "movies/Heat (1995)",
            "statuses": "manquant_arr,manquant_qbit",
            "torrents": [],
            "total_size": 800,
        },
        "movie:Inception": {
            "arr": (2, None),
            "emby_item_id": "e-inception",
            "files": [
                ("movies/Inception (2010)/Inception.2010.1080p.mkv", 2000, False, None, None, True),
                ("movies/Inception (2010)/Inception.2010.2160p.mkv", 3000, True, None, 22, True),
            ],
            "missing_emby_episodes": "",
            "reclaimable_bytes": 3700,
            "root_path": "/movies/Inception (2010)",
            "statuses": "doublon,orphelin_qbit,tracker_unique",
            "torrents": [
                ("inception4k", True, False, False, "radarr", ("tracker-a.example",)),
                ("inceptionold", False, False, False, "radarr", ("tracker-a.example",)),
            ],
            "total_size": 3000,
        },
        "movie:Matrix": {
            "arr": (1, None),
            "emby_item_id": "e-matrix",
            "files": [("movies/Matrix (1999)/Matrix.1999.1080p.mkv", 1000, True, None, 11, True)],
            "missing_emby_episodes": "",
            "reclaimable_bytes": 0,
            "root_path": "/movies/Matrix (1999)",
            "statuses": "cross_seed",
            "torrents": [
                ("matrixcross", True, False, False, "cross-seed", ("tracker-b.example",)),
                ("matrixhash", True, False, False, "radarr", ("tracker-a.example",)),
            ],
            "total_size": 1000,
        },
        "series:Dark": {
            "arr": (None, 10),
            "emby_item_id": "s-dark",
            "files": [
                ("tv/Dark/Season 01/Dark.S01E01.mkv", 1100, True, "S01E01", 101, True),
                ("tv/Dark/Season 01/Dark.S01E02.mkv", 1200, True, "S01E02", 102, True),
            ],
            "missing_emby_episodes": "S01E03",
            "reclaimable_bytes": 0,
            "root_path": "/tv/Dark",
            "statuses": "manquant_emby,tracker_unique",
            "torrents": [("darkpack", True, False, False, "sonarr", ("tracker-a.example",))],
            "total_size": 2300,
        },
    },
    # statut, médias, doublons, orphelins, non hardlinkés, tracker unique,
    # torrents lus, torrents rattachés (le torrent sans rapport ne l'est pas).
    "run": ("completed", 6, 1, 1, 1, 3, 7, 6),
    "unmatched": [],
}
