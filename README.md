# Steam review scores and player retention

This project collects public Steam and SteamCharts pages into a SQLite database and tests two questions:

1. Are critic scores or early player-review scores better predictors of player activity six months after launch?
2. Are early player-review scores better predictors of that activity for single-player games than for multiplayer games?

`python main.py` states the questions, reads `data/games.sqlite`, and prints the answers with sample sizes and Spearman correlations against log retention. It does not scrape.

## Submission components

1. SQLite database file: [`data/games.sqlite`](data/games.sqlite)
2. Python code packaged so that running `main.py` states the questions, runs the analysis on the database, and answers them with support. This repository is that package.
3. A synthesis of the process used to answer the questions: [`docs/answering_the_question.md`](docs/answering_the_question.md)
4. A synthesis of the process used to gather the data (it is not a preexisting dataset): [`docs/gathering_the_data.md`](docs/gathering_the_data.md)
5. A summary of how AI was used and the difficulties encountered: [`docs/ai_usage.md`](docs/ai_usage.md)

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
- `src/analysis.py` computes Spearman correlations of the scores with log retention.
- `src/report.py` formats the printed report.
- `src/config.py` holds the shared cutoffs and paths.
- `docs/` explains the question, the collection, and the use of AI.
