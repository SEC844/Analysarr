"""Garde-fou des montages, avant toute action qui touche au disque.

Un volume démonté ne se distingue pas d'un fichier supprimé : le chemin
n'existe simplement plus pour le conteneur Analysarr. Sans vérification,
Analysarr supprimerait des lignes en base, retirerait le film de Radarr et
effacerait la fiche média alors que les fichiers, eux, sont intacts sur un
disque qui n'est plus monté — et une réparation de hardlink écrirait sous le
point de montage, où le contenu redeviendra invisible au remontage.

Symptôme retenu, volontairement conservateur (jamais de refus sur une simple
absence de fichier) : le chemin n'existe pas ET le plus proche dossier parent
existant est vide, ou la racine de la bibliothèque n'est plus un dossier
peuplé. Un fichier supprimé à la main dans un dossier qui contient encore
d'autres fichiers n'est donc pas un montage manquant."""

import os
from collections.abc import Iterable

from app.models.settings import Settings

# Échantillon affiché dans le message d'erreur : la liste complète n'aiderait
# pas, le premier chemin suffit à reconnaître le montage en cause.
_SAMPLE = 3


class MountUnavailableError(RuntimeError):
    """Action refusée : un chemin nécessaire n'est pas joignable depuis le
    conteneur. Hérite de RuntimeError pour être rattrapée comme un échec
    d'action par les automatisations (services/automations.py)."""


def _deepest_existing_dir(path: str) -> str | None:
    parent = os.path.dirname(path)
    while parent and not os.path.isdir(parent):
        higher = os.path.dirname(parent)
        if higher == parent:
            return None
        parent = higher
    return parent or None


def looks_unmounted(path: str) -> bool:
    if not path or os.path.lexists(path):
        return False
    parent = _deepest_existing_dir(path)
    if parent is None:
        return True
    try:
        return not os.listdir(parent)  # point de montage resté vide
    except OSError:
        return True


def _root_unavailable(root: str) -> bool:
    if not root:
        return False
    try:
        return not os.path.isdir(root) or not os.listdir(root)
    except OSError:
        return True


def unavailable_paths(settings: Settings | None, paths: Iterable[str]) -> list[str]:
    """Chemins (racine de la bibliothèque comprise) qui semblent appartenir à
    un volume non monté."""
    problems: list[str] = []
    root = (settings.emby_library_path or "").strip() if settings else ""
    if _root_unavailable(root):
        problems.append(root)
    problems.extend(path for path in dict.fromkeys(paths) if path and looks_unmounted(path))
    return problems


def ensure_paths_available(settings: Settings | None, paths: Iterable[str], action: str) -> None:
    """Lève `MountUnavailableError` si un montage semble absent. Appelée AVANT
    la moindre suppression : mieux vaut une action refusée qu'une bibliothèque
    oubliée parce qu'un disque n'était pas monté."""
    problems = unavailable_paths(settings, paths)
    if not problems:
        return
    shown = ", ".join(problems[:_SAMPLE])
    if len(problems) > _SAMPLE:
        shown += f" (+{len(problems) - _SAMPLE} autre(s))"
    raise MountUnavailableError(
        f"{action} annulée : ces chemins sont introuvables depuis le conteneur Analysarr, "
        f"le volume correspondant n'est probablement pas monté. Vérifiez vos montages puis relancez un scan. "
        f"Aucune modification n'a été effectuée sur le disque. Chemins : {shown}"
    )
