import hashlib
import ipaddress
import secrets

import bcrypt

SESSION_COOKIE_NAME = "analysarr_session"
SESSION_DURATION_DAYS = 7
LOCKOUT_THRESHOLD = 5
LOCKOUT_MINUTES = 15


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """sha256 suffit ici : contrairement à un mot de passe, un token de
    session a une entropie totale (256 bits générés par `secrets`), pas de
    dictionnaire d'attaque possible — pas besoin d'un hash volontairement
    lent comme bcrypt."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def parse_trusted_proxies(value: str | None) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Adresses ou plages (CIDR) des reverse-proxys de confiance. Tout ce qui
    n'est pas lisible est ignoré : une valeur douteuse ne doit jamais élargir
    la confiance."""
    networks = []
    for raw in (value or "").replace(";", ",").split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError:
            continue
    return networks


def _in_networks(address: str, networks: list) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return any(parsed in network for network in networks)


def client_ip(request, trusted: list) -> str:
    """Adresse réelle du client. `X-Forwarded-For` n'est lu QUE si la requête
    vient d'un proxy déclaré de confiance : cet en-tête est trivial à forger,
    et un verrouillage anti-bruteforce qui s'y fie se contourne en changeant
    une chaîne de caractères. On retient la dernière adresse de la chaîne qui
    n'appartient pas aux proxys de confiance (les précédentes sont fournies
    par le client lui-même)."""
    peer = request.client.host if request.client else ""
    if not trusted or not _in_networks(peer, trusted):
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    for candidate in reversed([part.strip() for part in forwarded.split(",") if part.strip()]):
        if not _in_networks(candidate, trusted):
            return candidate
    return peer
