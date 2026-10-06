"""Shared constants for collection and analysis.

Paths, request timing, study window, and inclusion cutoffs are defined here
so every other module reads the same rules.
"""

from datetime import date
from pathlib import Path

# Project root is the parent of this src/ directory, so the database is found
# no matter which folder the command is launched from.
ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "games.sqlite"
RAW_DIR = ROOT / "data" / "raw"

# Identifies this course project on every request. Public store pages only.
USER_AGENT = "UOP-242-steam-retention-study/1.0 (course assignment; public pages)"

# Steam search returns 25 rows per page even when a smaller count is asked for.
SEARCH_PAGE_SIZE = 25
# Pause between live Steam store calls. Cached responses do not wait.
STORE_DELAY_SECONDS = 1.25
# SteamCharts is a single-operator site, so chart pages wait longer.
CHARTS_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 8

# Games must be released in this inclusive window. March 2026 is the latest
# month that can still show six later player-count months in October 2026.
RELEASE_START = date(2018, 1, 1)
RELEASE_END = date(2026, 3, 31)

# How many candidate apps to queue per release year before measuring how many
# of them actually pass the inclusion cutoffs. The collector raises this in
# steps until it reaches the target or runs out of search pages.
CANDIDATES_PER_YEAR_START = 60
CANDIDATES_PER_YEAR_STEP = 50
CANDIDATES_PER_YEAR_MAX = 400
TARGET_INCLUDED_GAMES = 400

# Discovery filter. On each search page the collector keeps the game with the
# most lifetime reviews, and only when that count reaches this number. Smaller
# games rarely have a public player chart. The analysis cutoff is separate:
# 20 reviews inside the first 30 days.
MIN_NOTABLE_REVIEWS = 1000

# Analysis inclusion. Both numbers are applied in sample.py and nowhere else.
MIN_MONTH1_PLAYERS = 50
MIN_EARLY_REVIEWS = 20
# Reviews written after this many days can mention whether players stayed.
EARLY_REVIEW_DAYS = 30
# Releases after this day of the month use the next calendar month as month 1,
# because a late-month launch does not fill that month's average.
MONTH1_DAY_CUTOFF = 15
# A recorded average of zero cannot be logged. The floor keeps "players left"
# in the sample as a very small ratio.
LOG_PLAYER_FLOOR = 0.5

# Absolute Spearman values closer than this are treated as a tie.
CORRELATION_TIE_MARGIN = 0.03

# Stored lists use this separator because some Steam labels contain commas.
LABEL_SEPARATOR = " | "

SEARCH_URL = (
    "https://store.steampowered.com/search/results/"
    "?query&start={start}&count={count}&sort_by=Released_DESC"
    "&category1=998&infinite=1&cc=US&l=english&json=1&ignore_preferences=1"
)
DETAILS_URL = (
    "https://store.steampowered.com/api/appdetails?appids={app_id}&cc=US&l=english"
)
REVIEWS_URL = (
    "https://store.steampowered.com/appreviews/{app_id}?json=1"
    "&language=all&purchase_type=all&review_type=all&filter=recent"
    "&num_per_page={num_per_page}&start_date={start_date}&end_date={end_date}"
    "&date_range_type=include"
)
CHARTS_URL = "https://steamcharts.com/app/{app_id}"
