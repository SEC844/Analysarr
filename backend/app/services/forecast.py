"""Prévisions d'espace disque, à partir de l'historique quotidien de la
bibliothèque (services/library_history.py). Calcul PUR (`forecast()` reçoit
les photographies) ; seule `load_forecast` lit la base, jamais le disque ni le
réseau.

Méthode, volontairement simple et explicable :
- régression linéaire (moindres carrés) de l'espace occupé sur les
  `WINDOW_DAYS` derniers jours de mesures ;
- fourchette = intervalle de confiance à 90 % de la pente (loi de Student) :
  une croissance irrégulière (gros téléchargements ponctuels) donne une
  fourchette large, ce qui est honnête ;
- projection à partir de la DERNIÈRE mesure (ce que l'utilisateur voit
  aujourd'hui), pas de la droite ajustée : un grand ménage récent ne fait
  pas « remonter » le disque sur le graphique ;
- « plein dans X à Y semaines » : X avec la pente haute, Y avec la pente
  basse ; pente basse nulle ou négative = pas de date au plus tard.

Moins de `MIN_HISTORY_DAYS` mesures dans la fenêtre : aucune tendance, et
l'interface le dit au lieu d'extrapoler deux points.

Un disque est regroupé par système de fichiers à la dernière mesure : la
bibliothèque et les téléchargements sur un même disque (le cas recommandé
pour les hardlinks) ne forment qu'une seule prévision."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlmodel import Session

from app.services.library_history import DiskRole, DiskUsage, disks_of, history

WINDOW_DAYS = 30
MIN_HISTORY_DAYS = 14
HORIZON_DAYS = 90
# Historique affiché sur les graphiques, plus long que la fenêtre de calcul
# pour situer la tendance.
CHART_DAYS = 90
# Au-delà, une date de remplissage n'a plus de sens (dix ans).
MAX_FILL_DAYS = 3650
PROJECTION_STEP_DAYS = 7

ROLE_ORDER: tuple[DiskRole, ...] = ("library", "downloads")

# Quantile 0,95 de la loi de Student (intervalle bilatéral à 90 %), par
# degré de liberté ; au-delà de 30, la loi normale.
_T_95 = (
    6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812,
    1.796, 1.782, 1.771, 1.761, 1.753, 1.746, 1.740, 1.734, 1.729, 1.725,
    1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699, 1.697,
)  # fmt: skip
_T_NORMAL = 1.645


def t_value(degrees_of_freedom: int) -> float:
    if degrees_of_freedom < 1:
        raise ValueError("Au moins un degré de liberté.")
    return _T_95[degrees_of_freedom - 1] if degrees_of_freedom <= len(_T_95) else _T_NORMAL


@dataclass(frozen=True)
class Trend:
    """Croissance en octets par jour, avec sa fourchette basse et haute."""

    per_day: float
    low: float
    high: float
    points: int


def fit_trend(points: Sequence[tuple[date, int]]) -> Trend | None:
    """Moindres carrés sur (jour, octets). None : moins de trois points ou
    tous le même jour (pente indéfinie)."""
    n = len(points)
    if n < 3:
        return None
    origin = points[0][0]
    xs = [float((day - origin).days) for day, _ in points]
    ys = [float(value) for _, value in points]
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    if sxx == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / sxx
    intercept = mean_y - slope * mean_x
    residuals = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys, strict=True))
    margin = t_value(n - 2) * math.sqrt(residuals / (n - 2) / sxx)
    return Trend(per_day=slope, low=slope - margin, high=slope + margin, points=n)


@dataclass(frozen=True)
class FillEstimate:
    """Jours avant que le disque soit plein : au plus tôt (pente haute), au
    plus tard (pente basse ; None = pas de date au plus tard, la tendance
    basse ne remplit jamais le disque)."""

    earliest_days: float
    latest_days: float | None


def fill_estimate(free: int, trend: Trend) -> FillEstimate | None:
    """None : le disque ne se remplit pas, même avec la pente haute, ou pas
    avant `MAX_FILL_DAYS`."""
    if trend.high <= 0:
        return None
    remaining = max(0, free)
    earliest = remaining / trend.high
    if earliest > MAX_FILL_DAYS:
        return None
    latest = remaining / trend.low if trend.low > 0 else None
    return FillEstimate(earliest, latest if latest is not None and latest <= MAX_FILL_DAYS else None)


def bytes_to_free(free: int, trend: Trend, days: int, margin: float) -> int:
    """Espace à libérer pour que le disque tienne `days` jours avec la pente
    HAUTE (prudente), majoré de `margin` (0,05 = 5 %). 0 : il tient déjà."""
    missing = trend.high * days - free
    return math.ceil(missing * (1 + margin)) if missing > 0 else 0


# --- Photographies ----------------------------------------------------------


@dataclass(frozen=True)
class Snapshot:
    """Ce que la prévision lit d'une photographie (voir LibrarySnapshot)."""

    day: date
    total_size: int
    disks: list[DiskUsage]


@dataclass(frozen=True)
class DiskPoint:
    day: date
    used: int
    total: int


@dataclass(frozen=True)
class ProjectionPoint:
    day: date
    used: int
    low: int
    high: int


@dataclass(frozen=True)
class DiskForecast:
    key: str
    roles: list[DiskRole]
    paths: list[str]
    available: bool
    total: int | None
    free: int | None
    history: list[DiskPoint]
    # Mesures de ce disque dans la fenêtre de calcul.
    history_days: int
    trend: Trend | None
    fill: FillEstimate | None
    projection: list[ProjectionPoint]

    @property
    def used(self) -> int | None:
        return None if self.total is None or self.free is None else self.total - self.free


@dataclass(frozen=True)
class Forecast:
    latest_day: date | None
    history_days: int
    library_size: int | None
    library_trend: Trend | None
    disks: list[DiskForecast]

    @property
    def enough_history(self) -> bool:
        return self.history_days >= MIN_HISTORY_DAYS

    def disk(self, key: str) -> DiskForecast | None:
        return next((d for d in self.disks if d.key == key), None)


def _role_rank(role: DiskRole) -> int:
    return ROLE_ORDER.index(role)


def _groups(latest: Sequence[DiskUsage]) -> list[list[DiskUsage]]:
    """Racines de la dernière mesure, regroupées par système de fichiers. Une
    racine indisponible reste seule (son disque est inconnu)."""
    by_device: dict[int, list[DiskUsage]] = {}
    groups: list[list[DiskUsage]] = []
    for usage in sorted(latest, key=lambda u: _role_rank(u.role)):
        if usage.available and usage.device is not None:
            if usage.device not in by_device:
                by_device[usage.device] = []
                groups.append(by_device[usage.device])
            by_device[usage.device].append(usage)
        else:
            groups.append([usage])
    return groups


def _measure(snapshot: Snapshot, roles: Sequence[DiskRole]) -> DiskPoint | None:
    """Mesure du disque ce jour-là : la première racine du groupe disponible
    (même disque, mêmes chiffres)."""
    for usage in sorted(snapshot.disks, key=lambda u: _role_rank(u.role)):
        if usage.role in roles and usage.available and usage.total is not None and usage.free is not None:
            return DiskPoint(snapshot.day, usage.total - usage.free, usage.total)
    return None


def _window(points: Sequence[tuple[date, int]], latest_day: date) -> list[tuple[date, int]]:
    start = latest_day - timedelta(days=WINDOW_DAYS - 1)
    return [point for point in points if point[0] >= start]


def _projection(anchor: DiskPoint, trend: Trend) -> list[ProjectionPoint]:
    """Une semaine sur l'autre jusqu'à l'horizon, bornée à la capacité du
    disque (il ne peut pas contenir plus) et à zéro."""

    def clamp(value: float) -> int:
        return int(min(anchor.total, max(0.0, value)))

    return [
        ProjectionPoint(
            day=anchor.day + timedelta(days=offset),
            used=clamp(anchor.used + trend.per_day * offset),
            low=clamp(anchor.used + trend.low * offset),
            high=clamp(anchor.used + trend.high * offset),
        )
        for offset in range(0, HORIZON_DAYS + 1, PROJECTION_STEP_DAYS)
    ]


def _disk_forecast(group: list[DiskUsage], snapshots: Sequence[Snapshot]) -> DiskForecast:
    roles = [usage.role for usage in group]
    current = group[0]
    latest_day = snapshots[-1].day
    chart_start = latest_day - timedelta(days=CHART_DAYS - 1)
    history = [
        point for snapshot in snapshots if snapshot.day >= chart_start and (point := _measure(snapshot, roles))
    ]
    window = _window([(p.day, p.used) for p in history], latest_day)
    trend = fill = None
    projection: list[ProjectionPoint] = []
    anchor = history[-1] if history and history[-1].day == latest_day else None
    if len(window) >= MIN_HISTORY_DAYS and current.available and anchor is not None:
        trend = fit_trend(window)
        if trend is not None:
            fill = fill_estimate(anchor.total - anchor.used, trend)
            projection = _projection(anchor, trend)
    return DiskForecast(
        key="+".join(roles),
        roles=roles,
        paths=[usage.path for usage in group],
        available=current.available,
        total=current.total if current.available else None,
        free=current.free if current.available else None,
        history=history,
        history_days=len(window),
        trend=trend,
        fill=fill,
        projection=projection,
    )


def forecast(snapshots: Sequence[Snapshot]) -> Forecast:
    """`snapshots` : du plus ancien au plus récent, au moins `CHART_DAYS`
    jours pour des graphiques complets."""
    if not snapshots:
        return Forecast(latest_day=None, history_days=0, library_size=None, library_trend=None, disks=[])
    latest = snapshots[-1]
    window = _window([(s.day, s.total_size) for s in snapshots], latest.day)
    enough = len(window) >= MIN_HISTORY_DAYS
    return Forecast(
        latest_day=latest.day,
        history_days=len(window),
        library_size=latest.total_size,
        library_trend=fit_trend(window) if enough else None,
        disks=[_disk_forecast(group, snapshots) for group in _groups(latest.disks)],
    )


def load_forecast(session: Session) -> Forecast:
    return forecast(
        [Snapshot(s.day, s.total_size, disks_of(s)) for s in history(session, max(CHART_DAYS, WINDOW_DAYS))]
    )
