"""Entry point for the retention study.

Running this file states the questions, reads data/games.sqlite, and prints
answers with the supporting sample size and statistics. It does not scrape.
"""

from src.analysis import run_analysis
from src.report import print_answers, print_questions


def main() -> None:
    """Print the questions, fit the models, and print the answers."""

    print_questions()
    print_answers(run_analysis())


if __name__ == "__main__":
    main()
