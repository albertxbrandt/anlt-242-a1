# Steam review scores and player retention

This project collects public Steam and SteamCharts pages into a SQLite database and tests two questions:

1. Are critic scores or early player-review scores better predictors of player activity six months after launch?
2. Are early player-review scores better predictors of that activity for single-player games than for multiplayer games?

`python main.py` states the questions, reads `data/games.sqlite`, and prints the answers with the sample size and statistics. It does not scrape.

## Setup

Use Python 3.11 or newer.

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

On macOS or Linux, the virtualenv executable is `.venv/bin/pip`.

## Run the analysis

From the project folder:

```bash
.venv\Scripts\python main.py
```

The database is already filled. Collection is a separate step and is only needed to rebuild the data:

```bash
.venv\Scripts\python -m src.collect
```

That command is resumable. Raw responses are cached in `data/raw/`, which is not part of the submission.

## Layout

- `main.py` prints the questions and the answers.
- `src/collect.py` queues games and saves them.
- `src/sources.py` parses Steam and SteamCharts pages.
- `src/http_client.py` waits, retries, and caches requests.
- `src/db.py` owns the SQLite schema and queries.
- `src/sample.py` defines month 1, month 6, play mode, and inclusion.
- `src/analysis.py` fits the correlations and regressions.
- `src/report.py` formats the printed report.
- `src/config.py` holds the shared cutoffs and paths.
- `docs/` explains the question, the collection, and the use of AI.
