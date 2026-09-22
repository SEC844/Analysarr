import asyncio

import httpx

from app.clients.seer import SeerClient
from app.models.media import EmbyUser, Media, MediaRequest, MediaType
from app.models.settings import Settings
from app.schemas.media import DeleteStepResult
from app.services import seer

MARIE = {"id": 2, "displayName": "Marie", "jellyfinUserId": "ABCD-1234"}
ADMIN = {"id": 1, "displayName": "Admin"}


def request(**overrides):
    base = {"id": 10, "status": 2, "createdAt": "2026-03-12T10:00:00.000Z", "media": {"id": 100, "tmdbId": 603, "mediaType": "movie"}}
    return base | overrides


def test_auto_approved_manual_and_declined_requests():
    auto = seer.parse_request(request(requestedBy=MARIE, modifiedBy=MARIE))[1]
    manual = seer.parse_request(request(requestedBy=MARIE, modifiedBy=ADMIN))[1]
    declined = seer.parse_request(request(status=3, requestedBy=MARIE, modifiedBy=ADMIN))[1]

    assert auto["auto_approved"] and auto["modified_by_name"] is None
    assert not manual["auto_approved"] and manual["modified_by_name"] == "Admin"
    assert declined["status"] == "declined" and declined["modified_by_name"] == "Admin"
    assert auto["requested_by_emby_id"] == "abcd1234"  # tirets retirés, minuscules


def test_requests_are_matched_by_tmdb_for_movies_and_tvdb_for_series():
    index = seer.index_requests(
        [
            request(requestedBy=MARIE),
            request(id=11, media={"id": 200, "tmdbId": 1399, "tvdbId": 121361, "mediaType": "tv"}, requestedBy=ADMIN),
            {"id": "invalide", "media": {}},
        ]
    )
    movie = Media(id=1, media_type=MediaType.movie, title="Matrix", tmdb_id=603)
    series = Media(id=2, media_type=MediaType.series, title="GoT", tvdb_id=121361)

    assert [r.seer_request_id for r in seer.build_request_rows(movie, index)] == [10]
    assert [r.seer_request_id for r in seer.build_request_rows(series, index)] == [11]
    assert movie.requested_by == "Marie"


def test_requests_pagination_stops_at_total(fake_http):
    pages = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["X-Api-Key"] == "k"
        skip = int(req.url.params["skip"])
        pages.append(skip)
        return httpx.Response(200, json={"pageInfo": {"results": 150}, "results": [request(id=i) for i in range(skip, min(skip + 100, 150))]})

    fake_http["http://seer"] = handler
    assert len(asyncio.run(SeerClient("http://seer", "k").get_requests())) == 150
    assert pages == [0, 100]


def test_requester_avatar_comes_from_the_media_server_account(session):
    session.add(EmbyUser(id="abcd1234", name="Marie", image_tag="t1"))
    media = Media(media_type=MediaType.movie, title="Matrix", tmdb_id=603)
    session.add(media)
    session.commit()
    for row in seer.build_request_rows(media, seer.index_requests([request(requestedBy=MARIE, modifiedBy=MARIE)])):
        session.add(row)
    session.commit()

    [read] = seer.build_requests_read(session, media)
    assert read.requested_by.emby_user_id == "abcd1234" and read.requested_by.image_tag == "t1"


