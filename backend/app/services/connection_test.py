import httpx

from app.schemas.settings import ConnectionTestRequest, ConnectionTestResult

TIMEOUT = 8.0


def _clean_url(url: str) -> str:
    return url.rstrip("/")


async def test_emby(req: ConnectionTestRequest) -> ConnectionTestResult:
    if not req.url or not req.api_key:
        return ConnectionTestResult(success=False, message="URL et clé API requises.")

    url = f"{_clean_url(req.url)}/System/Info"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, headers={"X-Emby-Token": req.api_key})
        if resp.status_code == 401:
            return ConnectionTestResult(success=False, message="Clé API refusée (401 Unauthorized).")
        resp.raise_for_status()
        data = resp.json()
        name = data.get("ServerName", "Emby")
        version = data.get("Version", "?")
        return ConnectionTestResult(success=True, message=f"Connecté à {name} (version {version}).")
    except httpx.HTTPStatusError as exc:
        return ConnectionTestResult(success=False, message=f"Erreur HTTP {exc.response.status_code} : {exc.response.text[:200]}")
    except httpx.RequestError as exc:
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
        return ConnectionTestResult(success=False, message=f"Erreur HTTP {exc.response.status_code} : {exc.response.text[:200]}")
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
    headers = {k: v for k, v in resp.headers.items() if k.lower() in interesting_headers}
    body = resp.text.strip()[:200]
    return f"[HTTP {resp.status_code}] headers={headers or '—'} corps={body!r}"


async def test_qbittorrent(req: ConnectionTestRequest) -> ConnectionTestResult:
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
                "qu'aucun reverse proxy / VPN / pare-feu n'intercepte cette URL entre le conteneur Analysarr et qBittorrent."
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
        return ConnectionTestResult(success=False, message=f"Le service a répondu avec une erreur HTTP {resp.status_code}.")
    except httpx.RequestError as exc:
        return ConnectionTestResult(success=False, message=f"Connexion impossible : {exc}")


TESTERS = {
    "emby": test_emby,
    "sonarr": test_sonarr,
    "radarr": test_radarr,
    "qbittorrent": test_qbittorrent,
    "cross_seed": test_cross_seed,
}
