import httpx

from app.clients.emby import EmbyClient
from app.clients.torrent import build_torrent_client
from app.clients.torrent_base import TorrentAuthError
from app.schemas.settings import ConnectionTestRequest, ConnectionTestResult

TIMEOUT = 8.0


def _clean_url(url: str) -> str:
    return url.rstrip("/")


async def test_emby(req: ConnectionTestRequest) -> ConnectionTestResult:
    """Serveur multimédia : Emby ou Jellyfin (`req.media_server`)."""
    if not req.url or not req.api_key:
        return ConnectionTestResult(success=False, message="URL et clé API requises.")

    base = _clean_url(req.url)
    server = EmbyClient(base, req.api_key, req.media_server or "emby")
    expected = "Jellyfin" if server.is_jellyfin else "Emby"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            # Endpoint public : détecte un mauvais choix Emby/Jellyfin avant
            # même d'essayer la clé (message clair plutôt qu'un simple 401).
            public = await client.get(f"{base}/System/Info/Public")
            product = str(public.json().get("ProductName") or "") if public.status_code == 200 else ""
            if product and ("jellyfin" in product.lower()) != server.is_jellyfin:
                actual = "Jellyfin" if "jellyfin" in product.lower() else "Emby"
                return ConnectionTestResult(
                    success=False,
                    message=(
                        f"Le serveur à cette adresse est {actual}, pas {expected} : "
                        f"sélectionnez « {actual} » comme serveur multimédia."
                    ),
                )
            resp = await client.get(f"{base}/System/Info", headers=server.auth_headers())
        if resp.status_code in (401, 403):
            return ConnectionTestResult(success=False, message=f"Clé API refusée ({resp.status_code}).")
        resp.raise_for_status()
        data = resp.json()
        name = data.get("ServerName", expected)
        version = data.get("Version", "?")
        return ConnectionTestResult(success=True, message=f"Connecté à {name} ({expected} {version}).")
    except httpx.HTTPStatusError as exc:
        return ConnectionTestResult(
            success=False, message=f"Erreur HTTP {exc.response.status_code} : {exc.response.text[:200]}"
        )
    except (httpx.RequestError, ValueError) as exc:
        return ConnectionTestResult(success=False, message=f"Connexion impossible : {exc}")


async def _test_arr(req: ConnectionTestRequest, app_name: str) -> ConnectionTestResult:
    if not req.url or not req.api_key:
        return ConnectionTestResult(success=False, message="URL et clé API requises.")

    url = f"{_clean_url(req.url)}/api/v3/system/status"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, headers={"X-Api-Key": req.api_key})
        if resp.status_code == 401:
            return ConnectionTestResult(success=False, message="Clé API refusée (401 Unauthorized).")
        resp.raise_for_status()
        data = resp.json()
        version = data.get("version", "?")
        return ConnectionTestResult(success=True, message=f"Connecté à {app_name} (version {version}).")
    except httpx.HTTPStatusError as exc:
        return ConnectionTestResult(
            success=False, message=f"Erreur HTTP {exc.response.status_code} : {exc.response.text[:200]}"
        )
    except httpx.RequestError as exc:
        return ConnectionTestResult(success=False, message=f"Connexion impossible : {exc}")


async def test_sonarr(req: ConnectionTestRequest) -> ConnectionTestResult:
    return await _test_arr(req, "Sonarr")


async def test_radarr(req: ConnectionTestRequest) -> ConnectionTestResult:
    return await _test_arr(req, "Radarr")


def _diag(resp: httpx.Response) -> str:
    """Résumé brut de la réponse (statut, en-têtes clés, corps tronqué) pour diagnostiquer
    un éventuel reverse proxy / auth intermédiaire plutôt que qBittorrent lui-même."""
    interesting_headers = ["server", "www-authenticate", "set-cookie", "content-type", "location"]
    headers = {
        # Cookie : seul son nom aide au diagnostic, jamais sa valeur (jeton de session).
        k: (v.split("=", 1)[0] + "=…" if k.lower() == "set-cookie" else v)
        for k, v in resp.headers.items()
        if k.lower() in interesting_headers
    }
    body = resp.text.strip()[:200]
    return f"[HTTP {resp.status_code}] headers={headers or '—'} corps={body!r}"


async def test_torrent_client(req: ConnectionTestRequest) -> ConnectionTestResult:
    """Client torrent configuré : qBittorrent (diagnostic détaillé, voir plus
    bas), Deluge ou Transmission (une connexion réelle + un listing suffisent)."""
    if not req.url:
        return ConnectionTestResult(success=False, message="URL requise.")
    kind = req.torrent_client or "qbittorrent"
    if kind != "qbittorrent":
        return await _test_torrent_rpc(kind, req)
    return await _test_qbittorrent(req)


async def _test_torrent_rpc(kind: str, req: ConnectionTestRequest) -> ConnectionTestResult:
    client = build_torrent_client(kind, _clean_url(req.url or ""), req.username, req.password)
    try:
        async with client:
            torrents = await client.get_torrents()
    except TorrentAuthError as exc:
        return ConnectionTestResult(success=False, message=str(exc))
    except httpx.HTTPStatusError as exc:
        return ConnectionTestResult(
            success=False, message=f"Erreur HTTP {exc.response.status_code} : {exc.response.text[:200]}"
        )
    except (httpx.RequestError, RuntimeError, ValueError) as exc:
        return ConnectionTestResult(success=False, message=f"Connexion impossible : {exc}")
    return ConnectionTestResult(success=True, message=f"Connecté à {client.name} ({len(torrents)} torrents).")


async def _test_qbittorrent(req: ConnectionTestRequest) -> ConnectionTestResult:
    if not req.url or not req.username or req.password is None:
        return ConnectionTestResult(success=False, message="URL, identifiant et mot de passe requis.")

    base = _clean_url(req.url)
    url = f"{base}/api/v2/auth/login"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
            resp = await client.post(
                url,
                data={"username": req.username, "password": req.password},
                headers={"Referer": base, "Origin": base},
            )
    except httpx.RequestError as exc:
        return ConnectionTestResult(success=False, message=f"Connexion impossible : {exc}")

    diag = _diag(resp)

    # Un WWW-Authenticate ou un corps HTML (page de login d'un reverse proxy / Authelia / SWAG...)
    # trahit presque toujours un service intermédiaire plutôt que qBittorrent lui-même.
    if "www-authenticate" in {k.lower() for k in resp.headers}:
        return ConnectionTestResult(
            success=False,
            message=(
                "La réponse contient un en-tête WWW-Authenticate : ce n'est probablement pas qBittorrent qui "
                f"répond, mais une authentification HTTP (Basic Auth) d'un reverse proxy placé devant. {diag}"
            ),
        )
    if resp.text.strip().startswith(("<!DOCTYPE", "<html")):
        return ConnectionTestResult(
            success=False,
            message=(
                "La réponse est une page HTML, pas la réponse texte attendue de qBittorrent — l'URL passe "
                f"probablement par un reverse proxy (page de login, erreur, etc.) avant d'atteindre qBittorrent. {diag}"
            ),
        )

    # qBittorrent ne délivre un cookie de session (SID) qu'en cas d'authentification réussie.
    # C'est un signal plus fiable que le corps de la réponse, qui varie selon les versions :
    # les versions récentes renvoient 204 No Content (corps vide) au lieu de l'historique "200 Ok.".
    has_session_cookie = any("sid" in name.lower() for name in resp.cookies)
    if has_session_cookie or (resp.status_code == 200 and resp.text.strip() == "Ok."):
        return ConnectionTestResult(success=True, message="Connecté à qBittorrent.")

    if resp.status_code == 200 and resp.text.strip() == "Fails.":
        return ConnectionTestResult(
            success=False,
            message=(
                "qBittorrent a explicitement refusé ces identifiants (réponse « Fails. »). Vérifiez l'identifiant "
                "et le mot de passe WebUI (Outils → Options → WebUI dans qBittorrent), pas ceux d'Unraid. Si vous "
                f"êtes certain qu'ils sont bons, l'IP du conteneur est peut-être bannie temporairement par la "
                f"protection anti-brute-force (redémarrer qBittorrent réinitialise le ban). {diag}"
            ),
        )

    if resp.status_code in (401, 403):
        return ConnectionTestResult(
            success=False,
            message=(
                f"Accès refusé avant même la vérification des identifiants par qBittorrent. {diag} — vérifiez "
                "qu'aucun reverse proxy / VPN / pare-feu n'intercepte cette URL "
                "entre le conteneur Analysarr et qBittorrent."
            ),
        )

    return ConnectionTestResult(success=False, message=f"Réponse inattendue de qBittorrent. {diag}")


async def test_cross_seed(req: ConnectionTestRequest) -> ConnectionTestResult:
    if not req.url:
        return ConnectionTestResult(success=False, message="URL requise.")

    base = _clean_url(req.url)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(base)
        # cross-seed en mode daemon n'expose pas d'endpoint de statut dédié :
        # on se contente de vérifier que le service répond sur cette URL.
        if resp.status_code < 500:
            return ConnectionTestResult(success=True, message="Le service cross-seed répond sur cette URL.")
        return ConnectionTestResult(
            success=False, message=f"Le service a répondu avec une erreur HTTP {resp.status_code}."
        )
    except httpx.RequestError as exc:
        return ConnectionTestResult(success=False, message=f"Connexion impossible : {exc}")


class _UnexpectedAnswer(Exception):
    """Réponse qui n'a pas la forme attendue : autre service à cette adresse."""


async def _seer_version(client: httpx.AsyncClient, base: str, api_key: str) -> str:
    # /auth/me valide réellement la clé (l'API /status est publique).
    resp = await client.get(f"{base}/api/v1/auth/me", headers={"X-Api-Key": api_key})
    resp.raise_for_status()
    me = _json(resp)
    if not isinstance(me, dict) or "id" not in me:
        raise _UnexpectedAnswer
    status = await client.get(f"{base}/api/v1/status")
    body = _json(status) if status.status_code == 200 else None
    return str(body.get("version", "?")) if isinstance(body, dict) else "?"


async def _ombi_version(client: httpx.AsyncClient, base: str, api_key: str) -> str:
    # Route protégée : Ombi valide la clé sur toute requête qui la porte
    # (ApiKeyMiddlewear) ; /Status/info, publique, ne sert qu'à la version.
    resp = await client.get(f"{base}/api/v1/Request/movie/total", headers={"ApiKey": api_key})
    resp.raise_for_status()
    if not isinstance(_json(resp), int):
        raise _UnexpectedAnswer
    info = await client.get(f"{base}/api/v1/Status/info")
    version = _json(info) if info.status_code == 200 else None
    return version if isinstance(version, str) else "?"


def _json(resp: httpx.Response) -> object:
    """Corps JSON, ou None : Ombi répond à une route inconnue par la page de
    son interface (HTML, code 200), jamais par un 404."""
    try:
        return resp.json()
    except ValueError:
        return None


async def test_seer(req: ConnectionTestRequest) -> ConnectionTestResult:
    """Gestionnaire de demandes : Seer ou Ombi (voir services/seer.py)."""
    if not req.url or not req.api_key:
        return ConnectionTestResult(success=False, message="URL et clé API requises.")

    base = _clean_url(req.url)
    name, read_version = ("Ombi", _ombi_version) if req.request_manager == "ombi" else ("Seer", _seer_version)
    # Seer et Ombi n'exposent pas les mêmes routes : une route inconnue ou une
    # réponse d'une autre forme vient le plus souvent d'un mauvais choix.
    wrong_choice = f"ce service n'est peut-être pas {name} (vérifiez le choix Seer/Ombi)."
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            version = await read_version(client, base, req.api_key)
        return ConnectionTestResult(success=True, message=f"Connecté à {name} (version {version}).")
    except _UnexpectedAnswer:
        return ConnectionTestResult(success=False, message=f"Réponse inattendue : {wrong_choice}")
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            return ConnectionTestResult(success=False, message=f"Clé API refusée ({code}).")
        if code == 404:
            return ConnectionTestResult(success=False, message=f"Route introuvable (404) : {wrong_choice}")
        return ConnectionTestResult(success=False, message=f"Erreur HTTP {code} : {exc.response.text[:200]}")
    except httpx.RequestError as exc:
        return ConnectionTestResult(success=False, message=f"Connexion impossible : {exc}")


TESTERS = {
    "emby": test_emby,
    "sonarr": test_sonarr,
    "radarr": test_radarr,
    "qbittorrent": test_torrent_client,
    "cross_seed": test_cross_seed,
    "seer": test_seer,
}
