"""Queue Steam games and save their store, review, and chart records.

Run this module directly. ``main.py`` does not collect. The run is resumable:
finished steps are skipped, and raw responses stay in the cache.

Discovery walks the public Steam search, sorted by release date. Pages are
visited in an order that covers the whole year first. From each page, the
game with the most lifetime reviews is queued when that count is at least
``MIN_NOTABLE_REVIEWS``. Smaller games usually have no public player chart,
so they cannot answer a retention question. The early-review cutoff used in
the analysis is separate and still 20 reviews.
"""

from datetime import date
import argparse
import json
import sys

from tqdm import tqdm

from src.config import (
    CANDIDATES_PER_YEAR_MAX,
    CANDIDATES_PER_YEAR_START,
    CANDIDATES_PER_YEAR_STEP,
    CHARTS_DELAY_SECONDS,
    CHARTS_URL,
    DETAILS_URL,
    MIN_NOTABLE_REVIEWS,
    RELEASE_END,
    RELEASE_START,
    REVIEWS_URL,
    SEARCH_PAGE_SIZE,
    SEARCH_URL,
    STORE_DELAY_SECONDS,
    TARGET_INCLUDED_GAMES,
)
from src.db import (
    connect,
    get_game,
    get_status,
    mark_status,
    pending_app_ids,
    replace_player_months,
    sampled_count_for_year,
    sampled_ids,
    upsert_game,
    upsert_sampled_app,
)
from src.http_client import FetchError, HttpClient
from src.sample import (
    classify_play_mode,
    exclusion_counts,
    join_labels,
    load_analysis_frame,
    review_ratio,
    review_window_unix,
    study_window_contains,
)
from src.sources import (
    first_review_timestamp,
    parse_app_details,
    parse_player_chart,
    parse_review_summary,
    parse_search_results,
    search_total_count,
)


def main() -> None:
    """Collect until the inclusion target is met or the per-year cap is reached."""

    sys.stdout.reconfigure(line_buffering=True)
    args = _parse_args()
    client = HttpClient()
    connection = connect()
    try:
        _confirm_review_window_once(client)
        spans = _year_spans(client)
        per_year = args.per_year or CANDIDATES_PER_YEAR_START
        fixed_quota = args.per_year is not None
        while True:
            can_grow = _queue_candidates(client, connection, spans, per_year)
            _collect_pending(client, connection)
            included = _included_count()
            print(f"Included games so far: {included} (target {args.max_included}).")
            if included >= args.max_included:
                break
            if fixed_quota or not can_grow or per_year >= CANDIDATES_PER_YEAR_MAX:
                print("Stopped before the target. No further candidates are available under the cap.")
                break
            per_year = min(CANDIDATES_PER_YEAR_MAX, per_year + CANDIDATES_PER_YEAR_STEP)
            print(f"Queuing more candidates, up to {per_year} per year.")
    finally:
        connection.close()


def _parse_args() -> argparse.Namespace:
    """Read optional limits used to try a short run before the full collection."""

    parser = argparse.ArgumentParser(description="Collect Steam games into data/games.sqlite")
    parser.add_argument("--max-included", type=int, default=TARGET_INCLUDED_GAMES)
    parser.add_argument(
        "--per-year",
        type=int,
        default=None,
        help="Queue this many games per year and stop, instead of raising the quota.",
    )
    return parser.parse_args()


def _confirm_review_window_once(client: HttpClient) -> None:
    """Stop the run if the review summary ignores the requested dates.

    Hades (app 1145360, released 2020-09-17) is the check. One review is fetched
    inside the 30-day window and its timestamp must fall inside that window.
    The saved summary used later asks for zero review rows and reads the totals.
    """

    release = date(2020, 9, 17)
    start, end = review_window_unix(release)
    payload = client.get_json(
        REVIEWS_URL.format(app_id=1145360, num_per_page=1, start_date=start, end_date=end),
        f"reviews/1145360-{start}-{end}-check.json",
        STORE_DELAY_SECONDS,
    )
    timestamp = first_review_timestamp(payload)
    if timestamp is None or not start <= timestamp <= end:
        raise RuntimeError("Steam review summary did not honor the 30-day window.")
    print("Review date window check passed.")


def _year_spans(client: HttpClient) -> list[tuple[int, int, int]]:
    """Return ``(year, first_offset, last_offset)`` for each study year.

    Search results are newest first. The offsets are page starts whose newest
    dated game is inside that year. ``last_offset`` is exclusive.
    """

    total = search_total_count(_search_payload(client, 0))
    if total <= 0:
        raise RuntimeError("Steam search did not report a catalog size.")
    max_start = ((total - 1) // SEARCH_PAGE_SIZE) * SEARCH_PAGE_SIZE
    spans: list[tuple[int, int, int]] = []
    for year, start_date, end_date in _study_years():
        newest = _find_start_on_or_before(client, end_date, max_start)
        oldest_exclusive = _find_start_on_or_before(client, start_date.fromordinal(start_date.toordinal() - 1), max_start)
        if oldest_exclusive < newest:
            oldest_exclusive = newest
        print(f"Year {year}: search offsets {newest} to {oldest_exclusive}.")
        spans.append((year, newest, oldest_exclusive))
    return spans


def _study_years() -> list[tuple[int, date, date]]:
    """Return inclusive date bounds for 2018-2025 and January-March 2026."""

    years = [(year, date(year, 1, 1), date(year, 12, 31)) for year in range(RELEASE_START.year, RELEASE_END.year)]
    years.append((RELEASE_END.year, date(RELEASE_END.year, 1, 1), RELEASE_END))
    return years


def _find_start_on_or_before(client: HttpClient, target: date, max_start: int) -> int:
    """Return the smallest page start whose newest dated game is on or before ``target``."""

    lo = 0
    hi = max_start
    answer = max_start
    while lo <= hi:
        mid = ((lo + hi) // 2 // SEARCH_PAGE_SIZE) * SEARCH_PAGE_SIZE
        newest = _newest_date_on_page(client, mid)
        if newest <= target:
            answer = mid
            hi = mid - SEARCH_PAGE_SIZE
        else:
            lo = mid + SEARCH_PAGE_SIZE
    return answer


def _newest_date_on_page(client: HttpClient, start: int) -> date:
    """Return the newest parsed release date on one search page.

    Undated rows are treated as newer than any real date so the search keeps
    moving toward older pages.
    """

    dates = [hit.release_date for hit in parse_search_results(_search_payload(client, start)) if hit.release_date]
    if not dates:
        return date(9999, 1, 1)
    return max(dates)


def _queue_candidates(client: HttpClient, connection, spans: list[tuple[int, int, int]], per_year: int) -> bool:
    """Add search hits until each year has ``per_year`` queued apps or no pages remain.

    Returns True when at least one year reached the quota, which means a later
    wave can raise the quota and find more games. False means every year is
    already exhausted.
    """

    known = sampled_ids(connection)
    reached_quota = False
    already_queued = sum(min(sampled_count_for_year(connection, year), per_year) for year, _, _ in spans)
    progress = tqdm(
        total=per_year * len(spans),
        initial=already_queued,
        unit="game",
        desc="Queuing",
        mininterval=1,
    )
    try:
        for year, newest, oldest in spans:
            have = sampled_count_for_year(connection, year)
            progress.set_description(f"Queuing {year}")
            if have >= per_year:
                reached_quota = True
                continue
            # Pages are ordered so the first ones already cover January through
            # December. Each page contributes its most-reviewed game, and only
            # when that game clears the notable-review bar.
            for start in _coverage_offsets(newest, oldest):
                if have >= per_year:
                    break
                best = _most_reviewed_hit(parse_search_results(_search_payload(client, start)), year, known)
                if best is None:
                    continue
                upsert_sampled_app(connection, best.app_id, year, best.lifetime_review_count or 0)
                connection.commit()
                known.add(best.app_id)
                have += 1
                progress.update(1)
            if have >= per_year:
                reached_quota = True
            else:
                # This year ran out of search pages. Drop the unfilled slots so
                # the time remaining is not waiting on games that do not exist.
                progress.total -= per_year - have
                progress.refresh()
    finally:
        progress.close()
    return reached_quota


def _hit_in_year(hit, year: int) -> bool:
    """Return whether a search hit belongs in ``year`` and inside the study window."""

    if hit.release_date is None or hit.release_date.year != year:
        return False
    return study_window_contains(hit.release_date)


def _most_reviewed_hit(hits, year: int, known: set[int]):
    """Return the most-reviewed in-year hit on a page, or None.

    ``known`` apps are skipped so a later wave does not queue the same game
    twice. The review count must reach ``MIN_NOTABLE_REVIEWS``.
    """

    best = None
    best_reviews = 0
    for hit in hits:
        if not _hit_in_year(hit, year) or hit.app_id in known:
            continue
        reviews = hit.lifetime_review_count or 0
        if reviews < MIN_NOTABLE_REVIEWS or reviews <= best_reviews:
            continue
        best = hit
        best_reviews = reviews
    return best


def _coverage_offsets(newest: int, oldest: int) -> list[int]:
    """Return page starts from ``newest`` up to but not including ``oldest``.

    The order is a halving walk: the newest page, the middle of the year, the
    quarter marks, and then finer gaps. Stopping after a quota therefore still
    spreads games across the year instead of taking only December.
    """

    pages = list(range(newest, oldest, SEARCH_PAGE_SIZE))
    count = len(pages)
    if count <= 1:
        return pages
    seen: set[int] = set()
    ordered: list[int] = []
    step = count
    while len(ordered) < count:
        step = max(1, step // 2)
        for index in range(0, count, step):
            if index in seen:
                continue
            seen.add(index)
            ordered.append(pages[index])
        if step == 1:
            break
    return ordered


def _collect_pending(client: HttpClient, connection) -> None:
    """Fetch any queued app whose details, reviews, or charts are unfinished."""

    pending = pending_app_ids(connection)
    progress = tqdm(total=len(pending), unit="game", desc="Downloading", mininterval=1)
    try:
        for app_id in pending:
            try:
                _collect_one(client, connection, app_id)
                connection.commit()
            except FetchError as exc:
                connection.rollback()
                progress.write(f"Will retry app {app_id} later: {exc}")
            progress.update(1)
    finally:
        progress.close()


def _collect_one(client: HttpClient, connection, app_id: int) -> None:
    """Finish whichever of the three steps are still open for ``app_id``."""

    status = get_status(connection, app_id)
    if not status["details_ok"]:
        if not _save_details(client, connection, app_id):
            return
    game = get_game(connection, app_id)
    if game is None or game["app_type"] != "game" or not game["release_date"]:
        mark_status(connection, app_id, reviews_ok=1, charts_ok=1, keep_existing_reason=True)
        return
    release = date.fromisoformat(game["release_date"])
    if not study_window_contains(release):
        mark_status(
            connection,
            app_id,
            reviews_ok=1,
            charts_ok=1,
            skip_reason="outside_release_window",
            keep_existing_reason=False,
        )
        return
    if not status["charts_ok"]:
        _save_charts(client, connection, app_id)
    if not get_status(connection, app_id)["reviews_ok"]:
        _save_reviews(client, connection, app_id, release)


def _save_details(client: HttpClient, connection, app_id: int) -> bool:
    """Store store metadata. Return False when the page is missing or not eligible.

    Ineligible apps are marked finished so the next run does not request them again.
    """

    result = client.fetch(
        DETAILS_URL.format(app_id=app_id),
        f"details/{app_id}.json",
        STORE_DELAY_SECONDS,
    )
    if result.status_code == 404:
        mark_status(
            connection,
            app_id,
            details_ok=1,
            reviews_ok=1,
            charts_ok=1,
            skip_reason="no_store_page",
            keep_existing_reason=False,
        )
        return False
    if result.status_code != 200:
        raise FetchError(f"Steam app {app_id} returned HTTP {result.status_code}")
    details = parse_app_details(json.loads(result.text), app_id)
    if details is None:
        mark_status(
            connection,
            app_id,
            details_ok=1,
            reviews_ok=1,
            charts_ok=1,
            skip_reason="no_store_page",
            keep_existing_reason=False,
        )
        return False

    release_text = details.release_date.isoformat() if details.release_date else None
    upsert_game(
        connection,
        {
            "app_id": details.app_id,
            "title": details.title,
            "app_type": details.app_type,
            "release_date": release_text,
            "developer": details.developer,
            "publisher": details.publisher,
            "genres": join_labels(details.genres),
            "categories": join_labels(details.categories),
            "play_mode": classify_play_mode(details.categories),
            "metacritic_score": details.metacritic_score,
            "early_review_positive": None,
            "early_review_total": None,
            "early_review_ratio": None,
        },
    )
    reason = None
    if details.app_type != "game":
        reason = "not_a_game"
    elif details.release_date is None:
        reason = "missing_release_date"
    mark_status(
        connection,
        app_id,
        details_ok=1,
        reviews_ok=1 if reason else None,
        charts_ok=1 if reason else None,
        skip_reason=reason,
        keep_existing_reason=reason is None,
    )
    return reason is None


def _save_charts(client: HttpClient, connection, app_id: int) -> None:
    """Store monthly player averages. A missing chart page is recorded and not retried."""

    result = client.fetch(
        CHARTS_URL.format(app_id=app_id),
        f"charts/{app_id}.html",
        CHARTS_DELAY_SECONDS,
    )
    # 404 and 500 both mean this app has no usable chart. Other statuses are
    # left unfinished so a later run can try again.
    if result.status_code in (404, 500):
        mark_status(connection, app_id, charts_ok=1, skip_reason="no_player_chart", keep_existing_reason=False)
        return
    if result.status_code != 200:
        raise FetchError(f"SteamCharts app {app_id} returned HTTP {result.status_code}")
    months = parse_player_chart(result.text)
    replace_player_months(
        connection,
        app_id,
        [
            {
                "year": month.year,
                "month": month.month,
                "avg_players": month.avg_players,
                "peak_players": month.peak_players,
            }
            for month in months
        ],
    )
    mark_status(
        connection,
        app_id,
        charts_ok=1,
        skip_reason=None if months else "no_player_chart",
        keep_existing_reason=bool(months),
    )


def _save_reviews(client: HttpClient, connection, app_id: int, release: date) -> None:
    """Store the positive ratio from reviews written in the first 30 days."""

    start, end = review_window_unix(release)
    result = client.fetch(
        REVIEWS_URL.format(app_id=app_id, num_per_page=0, start_date=start, end_date=end),
        f"reviews/{app_id}-{start}-{end}.json",
        STORE_DELAY_SECONDS,
    )
    if result.status_code != 200:
        raise FetchError(f"Steam reviews for {app_id} returned HTTP {result.status_code}")
    summary = parse_review_summary(json.loads(result.text))
    game = get_game(connection, app_id)
    if game is None:
        raise FetchError(f"Game {app_id} disappeared before reviews were saved.")
    updated = dict(game)
    updated["early_review_positive"] = summary.positive
    updated["early_review_total"] = summary.total
    updated["early_review_ratio"] = review_ratio(summary.positive, summary.total)
    upsert_game(connection, updated)
    mark_status(connection, app_id, reviews_ok=1, keep_existing_reason=True)


def _search_payload(client: HttpClient, start: int) -> dict:
    """Fetch one cached search page starting at ``start``."""

    return client.get_json(
        SEARCH_URL.format(start=start, count=SEARCH_PAGE_SIZE),
        f"search/start-{start}.json",
        STORE_DELAY_SECONDS,
    )


def _included_count() -> int:
    """Count games that currently pass the sample rules."""

    frame = load_analysis_frame()
    counts = exclusion_counts(frame)
    print("Exclusion counts:", counts)
    return counts.get("included", 0)


if __name__ == "__main__":
    main()
