"""Rules that turn stored rows into the analysis sample.

Play mode, month 1, month 6, the retention ratio, and the inclusion cutoffs
are implemented here only. Collection stores raw fields. Analysis reads the
frame this module builds.
"""

from datetime import date, datetime, timedelta, timezone
import math
from pathlib import Path

import pandas as pd

from src.config import (
    DB_PATH,
    EARLY_REVIEW_DAYS,
    LABEL_SEPARATOR,
    LOG_PLAYER_FLOOR,
    MIN_EARLY_REVIEWS,
    MIN_MONTH1_PLAYERS,
    MONTH1_DAY_CUTOFF,
    RELEASE_END,
    RELEASE_START,
)
from src.db import connect, fetch_games, fetch_player_months

# Store categories that mean people are expected to play together. Matching is
# done on the lowercase description. "Single-player" does not contain these.
_MULTIPLAYER_MARKERS = ("multi-player", "multiplayer", "co-op", "pvp", "mmo")


def classify_play_mode(categories: list[str]) -> str:
    """Label categories as ``single``, ``multi``, ``hybrid``, or ``unknown``.

    Single means Single-player is listed and no multiplayer-style category is.
    Multi means a multiplayer-style category is listed and Single-player is not.
    Hybrid games have both and stay in the database, but question 2 leaves them out.
    """

    lowered = [category.lower() for category in categories]
    has_single = any(category == "single-player" for category in lowered)
    has_multi = any(any(marker in category for marker in _MULTIPLAYER_MARKERS) for category in lowered)
    if has_single and has_multi:
        return "hybrid"
    if has_single:
        return "single"
    if has_multi:
        return "multi"
    return "unknown"


def split_labels(stored: str | None) -> list[str]:
    """Split a stored label list back into separate category or genre names."""

    if not stored:
        return []
    return [part for part in stored.split(LABEL_SEPARATOR) if part]


def join_labels(labels: list[str]) -> str:
    """Store labels with the shared separator."""

    return LABEL_SEPARATOR.join(labels)


def review_ratio(positive: int | None, total: int | None) -> float | None:
    """Return positive reviews divided by total reviews, or None when there are none."""

    if positive is None or total is None or total <= 0:
        return None
    return positive / total


def study_window_contains(release: date) -> bool:
    """Return whether ``release`` falls inside the study's inclusive date window."""

    return RELEASE_START <= release <= RELEASE_END


def month_one(release: date) -> tuple[int, int]:
    """Return the calendar month used as month 1 for a release date.

    A game released on or before the cutoff day uses its release month. A later
    release uses the next month, because the release month is only partly observed.
    The tuple is ``(year, month)``.
    """

    if release.day <= MONTH1_DAY_CUTOFF:
        return release.year, release.month
    return _add_months(release.year, release.month, 1)


def month_six(release: date) -> tuple[int, int]:
    """Return the sixth calendar month, five months after month 1.

    January then June is the example for a release in the first half of January:
    January, February, March, April, May, June.
    """

    year, month = month_one(release)
    return _add_months(year, month, 5)


def review_window_unix(release: date) -> tuple[int, int]:
    """Return UTC unix timestamps covering the first ``EARLY_REVIEW_DAYS`` after release.

    Steam's release date has no clock time, so the window starts at midnight UTC
    on the release day. Lifetime scores are not used: later reviews are written
    after players have already stayed or left.
    """

    start = datetime(release.year, release.month, release.day, tzinfo=timezone.utc)
    end = start + timedelta(days=EARLY_REVIEW_DAYS)
    return int(start.timestamp()), int(end.timestamp())


def load_analysis_frame(db_path: Path = DB_PATH) -> pd.DataFrame:
    """Build one row per stored game, including games that fail a cutoff.

    Included rows have ``included`` true and a ``log_retention`` value.
    ``exclusion`` is a short reason for every other row. Question 1 uses rows
    that are included and have a critic score. Question 2 uses included rows
    whose play mode is single or multi.
    """

    connection = connect(db_path)
    try:
        games = fetch_games(connection)
        months = fetch_player_months(connection)
    finally:
        connection.close()

    month_lookup = _months_by_app(months)
    rows = [_analysis_row(game, month_lookup.get(int(game["app_id"]), {})) for game in games]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return _empty_frame()
    return frame


def exclusion_counts(frame: pd.DataFrame) -> dict[str, int]:
    """Count games by inclusion flag and exclusion reason."""

    if frame.empty:
        return {"included": 0}
    counts = {"included": int(frame["included"].sum())}
    excluded = frame.loc[~frame["included"], "exclusion"].value_counts()
    for reason, count in excluded.items():
        counts[str(reason)] = int(count)
    return counts


def _analysis_row(game, months_for_app: dict[tuple[int, int], float]) -> dict:
    """Apply the inclusion rules to one game and its month map."""

    categories = split_labels(game["categories"])
    play_mode = classify_play_mode(categories)
    release = _iso_date(game["release_date"])
    ratio = review_ratio(game["early_review_positive"], game["early_review_total"])
    month1_avg = None
    month6_avg = None
    log_retention = None
    if release is not None:
        month1_avg = months_for_app.get(month_one(release))
        month6_avg = months_for_app.get(month_six(release))
        if month1_avg is not None and month6_avg is not None and month1_avg > 0:
            log_retention = math.log(max(month6_avg, LOG_PLAYER_FLOOR) / month1_avg)

    exclusion = _exclusion_reason(
        app_type=game["app_type"],
        release=release,
        month1_avg=month1_avg,
        month6_avg=month6_avg,
        early_total=game["early_review_total"],
    )
    return {
        "app_id": int(game["app_id"]),
        "title": game["title"],
        "app_type": game["app_type"],
        "release_date": game["release_date"],
        "play_mode": play_mode,
        "metacritic_score": game["metacritic_score"],
        "early_review_positive": game["early_review_positive"],
        "early_review_total": game["early_review_total"],
        "early_review_ratio": ratio,
        "month1_avg": month1_avg,
        "month6_avg": month6_avg,
        "log_retention": log_retention,
        "included": exclusion is None,
        "exclusion": exclusion or "",
    }


def _exclusion_reason(
    *,
    app_type: str | None,
    release: date | None,
    month1_avg: float | None,
    month6_avg: float | None,
    early_total: int | None,
) -> str | None:
    """Return the first failing cutoff, or None when the game stays in the sample.

    Order matters so each excluded game is counted under one reason.
    """

    if app_type != "game":
        return "not_a_game"
    if release is None:
        return "missing_release_date"
    if not study_window_contains(release):
        return "outside_release_window"
    if month1_avg is None or month6_avg is None:
        return "missing_player_month"
    if month1_avg < MIN_MONTH1_PLAYERS:
        return "low_month1_players"
    if early_total is None or early_total < MIN_EARLY_REVIEWS:
        return "few_early_reviews"
    return None


def _months_by_app(months) -> dict[int, dict[tuple[int, int], float]]:
    """Map app id to ``{(year, month): average players}``."""

    lookup: dict[int, dict[tuple[int, int], float]] = {}
    for month in months:
        lookup.setdefault(int(month["app_id"]), {})[(int(month["year"]), int(month["month"]))] = float(
            month["avg_players"]
        )
    return lookup


def _iso_date(value: str | None) -> date | None:
    """Parse a stored ``YYYY-MM-DD`` value. Empty values return None."""

    if not value:
        return None
    return date.fromisoformat(value)


def _add_months(year: int, month: int, count: int) -> tuple[int, int]:
    """Move ``count`` calendar months forward from ``(year, month)``."""

    index = year * 12 + (month - 1) + count
    return index // 12, index % 12 + 1


def _empty_frame() -> pd.DataFrame:
    """Return a frame with the expected columns when the database has no games."""

    return pd.DataFrame(
        columns=[
            "app_id",
            "title",
            "app_type",
            "release_date",
            "play_mode",
            "metacritic_score",
            "early_review_positive",
            "early_review_total",
            "early_review_ratio",
            "month1_avg",
            "month6_avg",
            "log_retention",
            "included",
            "exclusion",
        ]
    )
