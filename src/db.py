"""SQLite schema and the only SQL in the project.

Other modules pass plain values in and receive rows back. They do not write
queries of their own.
"""

from datetime import datetime, timezone
import sqlite3
from pathlib import Path

from src.config import DB_PATH

# apps that failed a cutoff stay in ``games`` so the exclusion can be counted.
# ``collection_status`` is separate so a crashed run can resume per step.
SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    app_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    app_type TEXT,
    release_date TEXT,
    developer TEXT,
    publisher TEXT,
    genres TEXT,
    categories TEXT,
    play_mode TEXT,
    metacritic_score INTEGER,
    early_review_positive INTEGER,
    early_review_total INTEGER,
    early_review_ratio REAL
);

CREATE TABLE IF NOT EXISTS player_months (
    app_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    avg_players REAL NOT NULL,
    peak_players INTEGER,
    PRIMARY KEY (app_id, year, month),
    FOREIGN KEY (app_id) REFERENCES games(app_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS collection_status (
    app_id INTEGER PRIMARY KEY,
    release_year INTEGER,
    lifetime_review_count INTEGER,
    details_ok INTEGER NOT NULL DEFAULT 0,
    reviews_ok INTEGER NOT NULL DEFAULT 0,
    charts_ok INTEGER NOT NULL DEFAULT 0,
    skip_reason TEXT,
    updated_at TEXT NOT NULL
);
"""


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the database, create tables when missing, and return the connection.

    Rows are returned as ``sqlite3.Row``. The caller closes the connection.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection


def upsert_sampled_app(
    connection: sqlite3.Connection,
    app_id: int,
    release_year: int,
    lifetime_review_count: int,
) -> None:
    """Record an app id chosen from search results, without repeating an existing row.

    Existing progress flags are left alone so a later wave does not reset work.
    """

    connection.execute(
        """
        INSERT INTO collection_status (
            app_id, release_year, lifetime_review_count, updated_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(app_id) DO NOTHING
        """,
        (app_id, release_year, lifetime_review_count, _now()),
    )


def sampled_ids(connection: sqlite3.Connection) -> set[int]:
    """Return every app id already queued or finished."""

    rows = connection.execute("SELECT app_id FROM collection_status").fetchall()
    return {int(row["app_id"]) for row in rows}


def sampled_count_for_year(connection: sqlite3.Connection, year: int) -> int:
    """Return how many search hits from ``year`` have already been queued."""

    row = connection.execute(
        "SELECT COUNT(*) AS n FROM collection_status WHERE release_year = ?",
        (year,),
    ).fetchone()
    return int(row["n"])


def pending_app_ids(connection: sqlite3.Connection) -> list[int]:
    """Return app ids that still need details, reviews, or charts."""

    rows = connection.execute(
        """
        SELECT app_id FROM collection_status
        WHERE details_ok = 0 OR reviews_ok = 0 OR charts_ok = 0
        ORDER BY release_year, app_id
        """
    ).fetchall()
    return [int(row["app_id"]) for row in rows]


def get_status(connection: sqlite3.Connection, app_id: int) -> sqlite3.Row:
    """Return the collection row for ``app_id``. The row must already exist."""

    row = connection.execute(
        "SELECT * FROM collection_status WHERE app_id = ?",
        (app_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"No collection status for app {app_id}")
    return row


def mark_status(
    connection: sqlite3.Connection,
    app_id: int,
    *,
    details_ok: int | None = None,
    reviews_ok: int | None = None,
    charts_ok: int | None = None,
    skip_reason: str | None = None,
    keep_existing_reason: bool = True,
) -> None:
    """Update the finished flags for one app.

    Passing ``skip_reason`` replaces the stored reason. Leave it as None and
    set ``keep_existing_reason`` to preserve a reason written by an earlier step.
    """

    current = get_status(connection, app_id)
    reason = current["skip_reason"] if keep_existing_reason and skip_reason is None else skip_reason
    connection.execute(
        """
        UPDATE collection_status
        SET details_ok = ?, reviews_ok = ?, charts_ok = ?, skip_reason = ?, updated_at = ?
        WHERE app_id = ?
        """,
        (
            current["details_ok"] if details_ok is None else details_ok,
            current["reviews_ok"] if reviews_ok is None else reviews_ok,
            current["charts_ok"] if charts_ok is None else charts_ok,
            reason,
            _now(),
            app_id,
        ),
    )


def upsert_game(connection: sqlite3.Connection, game: dict) -> None:
    """Insert or update one game. ``game`` uses the ``games`` column names."""

    columns = (
        "app_id",
        "title",
        "app_type",
        "release_date",
        "developer",
        "publisher",
        "genres",
        "categories",
        "play_mode",
        "metacritic_score",
        "early_review_positive",
        "early_review_total",
        "early_review_ratio",
    )
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column} = excluded.{column}" for column in columns if column != "app_id")
    connection.execute(
        f"""
        INSERT INTO games ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(app_id) DO UPDATE SET {updates}
        """,
        tuple(game[column] for column in columns),
    )


def replace_player_months(connection: sqlite3.Connection, app_id: int, months: list[dict]) -> None:
    """Replace every stored month for ``app_id`` with ``months``.

    Each month dict has year, month, avg_players, and peak_players.
    """

    connection.execute("DELETE FROM player_months WHERE app_id = ?", (app_id,))
    connection.executemany(
        """
        INSERT INTO player_months (app_id, year, month, avg_players, peak_players)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (app_id, month["year"], month["month"], month["avg_players"], month["peak_players"])
            for month in months
        ],
    )


def get_game(connection: sqlite3.Connection, app_id: int) -> sqlite3.Row | None:
    """Return one game row, or None when details have not been saved yet."""

    return connection.execute("SELECT * FROM games WHERE app_id = ?", (app_id,)).fetchone()


def fetch_games(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every saved game."""

    return connection.execute("SELECT * FROM games ORDER BY app_id").fetchall()


def fetch_player_months(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every saved player-count month."""

    return connection.execute(
        "SELECT * FROM player_months ORDER BY app_id, year, month"
    ).fetchall()


def collection_counts(connection: sqlite3.Connection) -> dict[str, int]:
    """Return queue size and how many apps finished each step."""

    row = connection.execute(
        """
        SELECT
            COUNT(*) AS queued,
            SUM(details_ok) AS details_done,
            SUM(reviews_ok) AS reviews_done,
            SUM(charts_ok) AS charts_done
        FROM collection_status
        """
    ).fetchone()
    return {key: int(row[key] or 0) for key in ("queued", "details_done", "reviews_done", "charts_done")}


def _now() -> str:
    """Return the current UTC time as text for the status table."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
