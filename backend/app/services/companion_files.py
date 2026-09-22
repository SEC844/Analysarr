"""Fichiers annexes d'un média : NFO, sous-titres, jaquettes, fanarts.

Un fichier vidéo ne vit jamais seul. Supprimer `Film.mkv` sans son `Film.nfo`
laissait des métadonnées orphelines dans la bibliothèque, et une restauration
depuis la corbeille rendait la vidéo sans ses NFO — perdus, puisqu'ils
n'avaient jamais été mis de côté (bug réel).

Deux règles :

- un fichier vidéo emporte ses annexes, reconnues par leur NOM : même dossier
  et même radical (`Film.nfo`, `Film.fr.srt`, `Film-fanart.jpg`) ;
- quand un dossier ne contient plus aucune vidéo, ce qui reste (NFO du dossier,
  affiches, dossiers de saison vides) n'a plus d'objet : le dossier part avec,
  sans jamais remonter au-dessus de la racine de la bibliothèque.

Le nettoyage ne touche QUE des dossiers situés sous la racine déclarée, et
jamais la racine elle-même."""

import os

# Extensions considérées comme le contenu lui-même : tant qu'il en reste une
# dans un dossier, ce dossier a encore une raison d'exister.
VIDEO_EXTENSIONS = frozenset(
    {".mkv", ".mp4", ".avi", ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".wmv", ".mov", ".flv", ".webm", ".iso"}
)
# Annexes reconnues : métadonnées, sous-titres et images.
COMPANION_EXTENSIONS = frozenset(
    {".nfo", ".xml", ".srt", ".sub", ".idx", ".ass", ".ssa", ".vtt", ".jpg", ".jpeg", ".png", ".webp", ".txt"}
)


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def is_companion_name(video_name: str, candidate: str) -> bool:
    """Un fichier annexe porte le radical de la vidéo, éventuellement suivi
    d'un suffixe : `Film.fr.srt`, `Film-fanart.jpg`, `Film.nfo`."""
    stem, extension = os.path.splitext(candidate)
    if extension.lower() not in COMPANION_EXTENSIONS:
        return False
    if stem == video_name:
        return True
    return stem.startswith(video_name) and stem[len(video_name)] in ".-_"


def companions(path: str) -> list[str]:
    """Annexes du fichier vidéo, dans son dossier. Liste vide si le dossier
    n'est pas lisible : une annexe manquée ne doit jamais bloquer l'action."""
    folder = os.path.dirname(path)
    video_name = _stem(path)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(
        os.path.join(folder, name)
        for name in names
        if name != os.path.basename(path) and is_companion_name(video_name, name)
    )


def has_video(folder: str) -> bool:
    try:
        entries = os.scandir(folder)
    except OSError:
        return True  # illisible : on n'y touche pas
    with entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if has_video(entry.path):
                    return True
            elif os.path.splitext(entry.name)[1].lower() in VIDEO_EXTENSIONS:
                return True
    return False


def is_inside(folder: str, root: str) -> bool:
    """Vrai si `folder` est STRICTEMENT sous `root` : la racine elle-même ne
    doit jamais être nettoyée."""
    folder, root = folder.replace("\\", "/").rstrip("/"), root.replace("\\", "/").rstrip("/")
    return bool(root) and folder.startswith(root + "/")


def leftovers(folder: str) -> list[str]:
    """Fichiers qui subsistent dans un dossier vidé de ses vidéos : NFO du
    dossier, affiches, fanarts. Ce sont eux qu'il reste à emporter."""
    found: list[str] = []
    for directory, _dirs, names in os.walk(folder):
        found.extend(os.path.join(directory, name) for name in names)
    return sorted(found)


def prune_empty_dirs(folder: str, root: str) -> list[str]:
    """Supprime le dossier s'il est vide, puis remonte tant que les parents le
    sont aussi — en s'arrêtant à la racine de la bibliothèque, jamais au-delà.
    Renvoie les dossiers réellement supprimés."""
    removed: list[str] = []
    current = folder
    while is_inside(current, root):
        try:
            os.rmdir(current)
        except OSError:
            break  # non vide, ou déjà parti : on s'arrête là
        removed.append(current)
        current = os.path.dirname(current)
    return removed


def folders_of(paths: list[str]) -> list[str]:
    """Dossiers concernés par ces fichiers, du plus profond au moins profond :
    un dossier de saison doit être traité avant celui de la série."""
    folders = {os.path.dirname(path) for path in paths if path}
    return sorted((folder for folder in folders if folder), key=lambda folder: folder.count(os.sep), reverse=True)
