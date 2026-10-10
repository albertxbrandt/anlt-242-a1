"""Text of the questions and the printed answers.

Numbers come from ``AnalysisResult``. This module does not refit models or
reapply sample rules. Cutoff values are read from ``config`` so the printed
rules match the code that built the sample.
"""

import math

from src.analysis import AnalysisResult, CorrelationStats
from src.config import (
    CORRELATION_TIE_MARGIN,
    LOG_PLAYER_FLOOR,
    MIN_EARLY_REVIEWS,
    MIN_MONTH1_PLAYERS,
    MONTH1_DAY_CUTOFF,
    RELEASE_END,
    RELEASE_START,
)


def print_questions() -> None:
    """State both questions before any results are shown."""

    print("Questions")
    print("1. Are critic scores or early player-review scores better predictors")
    print("   of player activity six months after launch?")
    print("2. Are early player-review scores better predictors of that activity")
    print("   for single-player games than for multiplayer games?")
    print()


def print_answers(result: AnalysisResult) -> None:
    """Print the sample, both sets of support, and a direct answer for each question."""

    _print_sample(result)
    _print_question_one(result)
    _print_question_two(result)


def _print_sample(result: AnalysisResult) -> None:
    """Describe who was included and why other stored games were left out."""

    print("Sample")
    print(
        f"Release window: {RELEASE_START.isoformat()} through {RELEASE_END.isoformat()}. "
        f"Month 1 is the release month when the game released on day {MONTH1_DAY_CUTOFF} or earlier; "
        "otherwise it is the next month. Month 6 is five months after month 1."
    )
    print(
        "Outcome: log retention, the natural log of average players in month 6 "
        f"divided by average players in month 1. A month-6 average of 0 is treated as {LOG_PLAYER_FLOOR}."
    )
    print(
        f"A game is included when it is a full game, both months are present, "
        f"month 1 averaged at least {MIN_MONTH1_PLAYERS} players, and at least "
        f"{MIN_EARLY_REVIEWS} reviews were written in the first 30 days."
    )
    share_left = math.exp(result.median_log_retention)
    print(
        f"Median log retention among included games: {result.median_log_retention:.3f} "
        f"(about {share_left:.0%} of month-1 players remained)."
    )
    print(f"Games stored in the database: {result.games_in_database}")
    print(f"Games included: {result.included}")
    print(
        "Included by play mode: "
        f"single {result.single_included}, multi {result.multi_included}, "
        f"hybrid {result.hybrid_included}, unknown {result.unknown_included}."
    )
    print("Hybrid games are stored and can be part of question 1. Question 2 uses single and multi only.")
    excluded = {key: value for key, value in result.exclusion_counts.items() if key != "included"}
    if excluded:
        print("Excluded games by reason:")
        for reason, count in sorted(excluded.items()):
            print(f"  {reason}: {count}")
    print()


def _print_question_one(result: AnalysisResult) -> None:
    """Print question 1 support and the comparison of the two scores."""

    print("Question 1")
    print("Shared sample: games with both a Metacritic score and an early player-review score.")
    print("Each line is the Spearman correlation of that score with log retention.")
    for correlation in result.q1_correlations:
        _print_correlation(correlation)
    print(
        "Check on the larger sample that does not require a critic score "
        "(early player score only):"
    )
    _print_correlation(result.q1_user_only)
    print(_question_one_answer(result))
    print()


def _print_question_two(result: AnalysisResult) -> None:
    """Print question 2 support and whether single-player associations are stronger."""

    print("Question 2")
    print("Predictor: early player-review score. Outcome: log retention. Hybrid games are omitted.")
    print("Each line is the Spearman correlation of that score with log retention.")
    for mode in result.q2_modes:
        label = "Single-player" if mode.play_mode == "single" else "Multiplayer"
        print(f"{label}:")
        _print_correlation(mode.correlation)
    print(_question_two_answer(result))
    print()


def _print_correlation(correlation: CorrelationStats) -> None:
    """Print one score's correlations, or a short note when the sample is too small."""

    if correlation.n < 3 or _missing(correlation.spearman_r):
        print(f"  {correlation.score_name}: n={correlation.n}, not enough variation to correlate.")
        return
    print(
        f"  {correlation.score_name}: n={correlation.n}, "
        f"Spearman r={correlation.spearman_r:.3f} (p={correlation.spearman_p:.3f})"
    )


def _question_one_answer(result: AnalysisResult) -> str:
    """Turn the Spearman comparison into the sentence that answers question 1."""

    critic, player = result.q1_correlations
    if result.q1_stronger == "insufficient":
        return "Answer: the shared sample is too small to say which score predicts retention better."
    if result.q1_stronger == "tie":
        return (
            "Answer: neither score is clearly better. On the shared sample their absolute "
            f"Spearman correlations differ by less than {CORRELATION_TIE_MARGIN:.2f} "
            f"(critic {critic.spearman_r:.3f}, early player {player.spearman_r:.3f})."
        )
    winner = "Critic scores" if result.q1_stronger == "critic score" else "Early player-review scores"
    other = player if result.q1_stronger == "critic score" else critic
    chosen = critic if result.q1_stronger == "critic score" else player
    return (
        f"Answer: {winner} are the better predictor of six-month player activity. "
        f"On the shared sample the Spearman correlation with log retention is {chosen.spearman_r:.3f} "
        f"versus {other.spearman_r:.3f} for the other score."
    )


def _question_two_answer(result: AnalysisResult) -> str:
    """Turn the within-mode Spearman comparison into the sentence that answers question 2."""

    single, multi = result.q2_modes
    if result.q2_stronger == "insufficient":
        return "Answer: one or both play-mode groups are too small to compare."
    if result.q2_stronger == "tie":
        return (
            "Answer: no. Early player-review scores are not clearly better predictors for "
            "single-player games than for multiplayer games. The absolute Spearman correlations "
            f"differ by less than {CORRELATION_TIE_MARGIN:.2f} "
            f"(single {single.correlation.spearman_r:.3f}, multi {multi.correlation.spearman_r:.3f})."
        )
    if result.q2_stronger == "single":
        return (
            "Answer: yes. Early player-review scores track six-month activity more closely for "
            f"single-player games (Spearman r={single.correlation.spearman_r:.3f}) than for "
            f"multiplayer games (Spearman r={multi.correlation.spearman_r:.3f})."
        )
    return (
        "Answer: no. Early player-review scores track six-month activity more closely for "
        f"multiplayer games (Spearman r={multi.correlation.spearman_r:.3f}) than for "
        f"single-player games (Spearman r={single.correlation.spearman_r:.3f})."
    )


def _missing(value: float) -> bool:
    """Return whether a correlation or fit statistic is missing."""

    return value != value
