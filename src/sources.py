"""Turn Steam and SteamCharts responses into plain records.

This module does not save anything and does not decide which games belong in
the analysis. It only reads the page or JSON shape of each source.
"""

from dataclasses import dataclass
from datetime import date, datetime
import re

from bs4 import BeautifulSoup

# Tooltip text looks like "79% of the 1,304 user reviews for this game are positive."
_REVIEW_COUNT = re.compile(r"([\d,]+)\s+user reviews?", re.IGNORECASE)
_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


@dataclass(frozen=True)
class SearchHit:
    """One game row from a Steam search page.

    ``lifetime_review_count`` is the count printed on that row. It is None when
    Steam shows no review summary, which usually means almost nobody has reviewed
    the game yet. ``release_date`` is None for "Coming soon" and other undated rows.
    """

    app_id: int
    title: str
    release_date: date | None
    lifetime_review_count: int | None


@dataclass(frozen=True)
class GameDetails:
    """Store metadata needed for the study. Category text is still ungrouped."""

    app_id: int
    title: str
    app_type: str
    release_date: date | None
    coming_soon: bool
    developer: str
    publisher: str
    genres: list[str]
    categories: list[str]
    metacritic_score: int | None


@dataclass(frozen=True)
class ReviewSummary:
    """Positive and total review counts for the requested date window."""

    positive: int
    total: int


@dataclass(frozen=True)
class PlayerMonth:
    """One calendar month of concurrent-player averages from SteamCharts."""

    year: int
    month: int
    avg_players: float
    peak_players: int | None


def parse_steam_date(text: str) -> date | None:
    """Parse a Steam release string into a date.

    Accepts ``Sep 17, 2020``, ``17 Sep, 2020``, and month-only ``Sep 2020``.
    A missing day becomes the 15th so the month-1 rule treats it as that month.
    Undated labels such as "Coming soon" return None.
    """

    cleaned = " ".join(text.split())
    if not cleaned:
        return None
    for fmt in ("%b %d, %Y", "%d %b, %Y"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    try:
        parsed = datetime.strptime(cleaned, "%b %Y")
    except ValueError:
        return None
    return date(parsed.year, parsed.month, 15)


def parse_search_results(payload: dict) -> list[SearchHit]:
    """Read app id, title, release date, and lifetime review count from search JSON.

    Bundle rows have several ids in ``data-ds-appid`` and are skipped, because a
    bundle is not one game. ``payload`` is the JSON object from the search endpoint.
    """

    html = payload.get("results_html") or ""
    soup = BeautifulSoup(html, "html.parser")
    hits: list[SearchHit] = []
    for row in soup.select("a.search_result_row"):
        raw_id = (row.get("data-ds-appid") or "").strip()
        if not raw_id.isdigit():
            continue
        title_node = row.select_one("span.title")
        released_node = row.select_one("div.search_released")
        title = title_node.get_text(strip=True) if title_node else raw_id
        released = released_node.get_text(" ", strip=True) if released_node else ""
        hits.append(
            SearchHit(
                app_id=int(raw_id),
                title=title,
                release_date=parse_steam_date(released),
                lifetime_review_count=_lifetime_reviews(row),
            )
        )
    return hits


def search_total_count(payload: dict) -> int:
    """Return the catalog size reported by a search response."""

    return int(payload.get("total_count") or 0)


def parse_app_details(payload: dict, app_id: int) -> GameDetails | None:
    """Read one app-details response. Returns None when Steam has no page for it.

    ``payload`` is the full JSON object keyed by app id. Critic score is the
    Metacritic value Steam embeds, or None when Steam has no critic score.
    """

    entry = payload.get(str(app_id)) or {}
    if not entry.get("success") or not isinstance(entry.get("data"), dict):
        return None
    data = entry["data"]
    release = data.get("release_date") or {}
    metacritic = data.get("metacritic") or {}
    score = metacritic.get("score")
    return GameDetails(
        app_id=app_id,
        title=str(data.get("name") or app_id),
        app_type=str(data.get("type") or ""),
        release_date=None if release.get("coming_soon") else parse_steam_date(str(release.get("date") or "")),
        coming_soon=bool(release.get("coming_soon")),
        developer=_join_names(data.get("developers")),
        publisher=_join_names(data.get("publishers")),
        genres=_descriptions(data.get("genres")),
        categories=_descriptions(data.get("categories")),
        metacritic_score=int(score) if isinstance(score, int) else None,
    )


def parse_review_summary(payload: dict) -> ReviewSummary:
    """Read positive and total counts from an app-reviews response.

    The collector calls this only with ``date_range_type=include``. A live check
    showed ``query_summary`` then counts that window rather than all-time reviews,
    so the study does not page through individual review text.
    """

    summary = payload.get("query_summary") or {}
    positive = int(summary.get("total_positive") or 0)
    total = int(summary.get("total_reviews") or 0)
    return ReviewSummary(positive=positive, total=total)


def first_review_timestamp(payload: dict) -> int | None:
    """Return the creation time of the first review in a response, if one is present.

    Used once per run to confirm the date window actually constrained the feed.
    """

    reviews = payload.get("reviews") or []
    if not reviews:
        return None
    return int(reviews[0]["timestamp_created"])


def parse_player_chart(html: str) -> list[PlayerMonth]:
    """Read monthly average and peak players from a SteamCharts app page.

    The "Last 30 Days" row is not a calendar month and is ignored. Months that
    do not parse are skipped rather than guessed.
    """

    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.common-table")
    if table is None:
        return []
    months: list[PlayerMonth] = []
    for row in table.select("tbody tr"):
        label_node = row.select_one("td.month-cell")
        average_node = row.select_one("td.num-f")
        if label_node is None or average_node is None:
            continue
        label = label_node.get_text(" ", strip=True)
        if "last 30" in label.lower():
            continue
        parsed = _month_label(label)
        if parsed is None:
            continue
        year, month = parsed
        peak_node = row.select_one("td.num")
        months.append(
            PlayerMonth(
                year=year,
                month=month,
                avg_players=_float_from(average_node.get_text(" ", strip=True)),
                peak_players=_int_from(peak_node.get_text(" ", strip=True)) if peak_node else None,
            )
        )
    return months


def _lifetime_reviews(row) -> int | None:
    """Pull the review count out of a search row tooltip. None means no summary was shown."""

    summary = row.select_one("span.search_review_summary")
    if summary is None:
        return None
    tooltip = summary.get("data-tooltip-html") or ""
    match = _REVIEW_COUNT.search(tooltip)
    if match is None:
        return None
    return int(match.group(1).replace(",", ""))


def _month_label(label: str) -> tuple[int, int] | None:
    """Parse ``September 2026`` into ``(2026, 9)``."""

    try:
        parsed = datetime.strptime(label, "%B %Y")
    except ValueError:
        return None
    return parsed.year, parsed.month


def _descriptions(items) -> list[str]:
    """Return description strings from a Steam list of ``{description: ...}`` objects."""

    if not isinstance(items, list):
        return []
    return [str(item["description"]) for item in items if isinstance(item, dict) and item.get("description")]


def _join_names(names) -> str:
    """Join developer or publisher names. Missing lists become an empty string."""

    if not isinstance(names, list):
        return ""
    return ", ".join(str(name) for name in names)


def _float_from(text: str) -> float:
    """Read the first number in ``text``. Commas are thousands separators."""

    match = _NUMBER.search(text.replace(",", ""))
    if match is None:
        return 0.0
    return float(match.group(0))


def _int_from(text: str) -> int | None:
    """Read the first integer in ``text``, or None when no digits are present."""

    match = _NUMBER.search(text.replace(",", ""))
    if match is None:
        return None
    return int(float(match.group(0)))
