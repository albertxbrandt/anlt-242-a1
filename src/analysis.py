"""Correlations and regressions that answer the two study questions.

The outcome is always the log of six-month player retention built by
``sample.py``. Scores are standardized inside the sample used for each model
so a 0-100 critic score and a 0-1 review ratio can be compared. This module
returns numbers. It does not print them.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

from src.config import CORRELATION_TIE_MARGIN, DB_PATH
from src.sample import exclusion_counts, load_analysis_frame


@dataclass(frozen=True)
class CorrelationStats:
    """Association between one score and log retention."""

    score_name: str
    n: int
    pearson_r: float
    pearson_p: float
    spearman_r: float
    spearman_p: float


@dataclass(frozen=True)
class RegressionStats:
    """One ordinary least-squares fit."""

    n: int
    r_squared: float
    coefficients: dict[str, float]
    pvalues: dict[str, float]


@dataclass(frozen=True)
class ModeComparison:
    """User-score results inside one play-mode group."""

    play_mode: str
    correlation: CorrelationStats
    regression: RegressionStats


@dataclass(frozen=True)
class AnalysisResult:
    """Everything the report needs, including the sample that was too small to use."""

    games_in_database: int
    exclusion_counts: dict[str, int]
    included: int
    single_included: int
    multi_included: int
    hybrid_included: int
    unknown_included: int
    median_log_retention: float
    q1_correlations: list[CorrelationStats]
    q1_regression: RegressionStats
    q1_stronger: str
    q1_user_only: CorrelationStats
    q1_user_only_regression: RegressionStats
    q2_modes: list[ModeComparison]
    q2_stronger: str
    q2_interaction: RegressionStats


def run_analysis(db_path: Path = DB_PATH) -> AnalysisResult:
    """Load the database and fit both questions. Raises if nothing is included."""

    frame = load_analysis_frame(db_path)
    included = frame.loc[frame["included"]].copy() if not frame.empty else frame
    if included.empty:
        raise RuntimeError("No games passed the inclusion rules. Collect data before running main.")

    q1 = included.dropna(subset=["metacritic_score", "early_review_ratio", "log_retention"])
    q1_correlations = [
        _correlate(q1, "metacritic_score", "critic score"),
        _correlate(q1, "early_review_ratio", "early player score"),
    ]
    q1_regression = _fit_ols(q1, ["metacritic_score", "early_review_ratio"], ["critic_z", "user_z"])
    user_only = included.dropna(subset=["early_review_ratio", "log_retention"])
    modes = []
    for play_mode in ("single", "multi"):
        group = included.loc[included["play_mode"] == play_mode].dropna(
            subset=["early_review_ratio", "log_retention"]
        )
        modes.append(
            ModeComparison(
                play_mode=play_mode,
                correlation=_correlate(group, "early_review_ratio", "early player score"),
                regression=_fit_ols(group, ["early_review_ratio"], ["user_z"]),
            )
        )
    return AnalysisResult(
        games_in_database=int(len(frame)),
        exclusion_counts=exclusion_counts(frame),
        included=int(len(included)),
        single_included=int((included["play_mode"] == "single").sum()),
        multi_included=int((included["play_mode"] == "multi").sum()),
        hybrid_included=int((included["play_mode"] == "hybrid").sum()),
        unknown_included=int((included["play_mode"] == "unknown").sum()),
        median_log_retention=float(included["log_retention"].median()),
        q1_correlations=q1_correlations,
        q1_regression=q1_regression,
        q1_stronger=_stronger_name(q1_correlations[0], q1_correlations[1]),
        q1_user_only=_correlate(user_only, "early_review_ratio", "early player score"),
        q1_user_only_regression=_fit_ols(user_only, ["early_review_ratio"], ["user_z"]),
        q2_modes=modes,
        q2_stronger=_stronger_mode(modes),
        q2_interaction=_fit_interaction(included),
    )


def _correlate(frame: pd.DataFrame, column: str, score_name: str) -> CorrelationStats:
    """Pearson and Spearman correlation of ``column`` with log retention.

    Spearman is the ranking used to call one score a better predictor, because
    the relationship does not have to be a straight line. Pearson is reported
    beside it. p-values are support, not the decision rule.
    """

    pair = frame.dropna(subset=[column, "log_retention"])
    if len(pair) < 3 or pair[column].nunique() < 2 or pair["log_retention"].nunique() < 2:
        return CorrelationStats(score_name, int(len(pair)), float("nan"), float("nan"), float("nan"), float("nan"))
    pearson = stats.pearsonr(pair[column], pair["log_retention"])
    spearman = stats.spearmanr(pair[column], pair["log_retention"])
    return CorrelationStats(
        score_name=score_name,
        n=int(len(pair)),
        pearson_r=float(pearson.statistic),
        pearson_p=float(pearson.pvalue),
        spearman_r=float(spearman.statistic),
        spearman_p=float(spearman.pvalue),
    )


def _fit_ols(frame: pd.DataFrame, columns: list[str], names: list[str]) -> RegressionStats:
    """Fit log retention on the z-scores of ``columns``. ``names`` label the output.

    Standardization uses the rows in ``frame`` only, so each model is scaled to
    the sample it is actually fit on.
    """

    pair = frame.dropna(subset=columns + ["log_retention"]).copy()
    if len(pair) <= len(columns) + 1:
        return RegressionStats(int(len(pair)), float("nan"), {}, {})
    design = pd.DataFrame({name: _zscore(pair[column]) for column, name in zip(columns, names)})
    return _ols(pair["log_retention"], design)


def _fit_interaction(included: pd.DataFrame) -> RegressionStats:
    """Fit log retention on user score, multiplayer, and their product.

    The user score is standardized on the pooled single-player and multiplayer
    rows. Hybrid games are not in this model. ``multi`` is 1 for multiplayer.
    """

    pooled = included.loc[included["play_mode"].isin(["single", "multi"])].dropna(
        subset=["early_review_ratio", "log_retention"]
    ).copy()
    if len(pooled) < 8:
        return RegressionStats(int(len(pooled)), float("nan"), {}, {})
    pooled["user_z"] = _zscore(pooled["early_review_ratio"])
    pooled["multi"] = (pooled["play_mode"] == "multi").astype(float)
    pooled["user_z_x_multi"] = pooled["user_z"] * pooled["multi"]
    design = pooled[["user_z", "multi", "user_z_x_multi"]]
    return _ols(pooled["log_retention"], design)


def _ols(outcome: pd.Series, design: pd.DataFrame) -> RegressionStats:
    """Fit an intercept plus ``design`` and return coefficients and p-values."""

    try:
        model = sm.OLS(outcome.astype(float), sm.add_constant(design.astype(float), has_constant="add")).fit()
    except (ValueError, np.linalg.LinAlgError):
        return RegressionStats(int(len(outcome)), float("nan"), {}, {})
    coefficients = {str(name): float(value) for name, value in model.params.items()}
    pvalues = {str(name): float(value) for name, value in model.pvalues.items()}
    return RegressionStats(n=int(model.nobs), r_squared=float(model.rsquared), coefficients=coefficients, pvalues=pvalues)


def _zscore(values: pd.Series) -> pd.Series:
    """Standardize ``values``. A constant column becomes zeros."""

    std = float(values.std(ddof=0))
    if std == 0:
        return values * 0
    return (values - float(values.mean())) / std


def _stronger_name(left: CorrelationStats, right: CorrelationStats) -> str:
    """Return which score has the larger absolute Spearman correlation, or ``tie``."""

    return _label_from_gap(left.score_name, left.spearman_r, right.score_name, right.spearman_r)


def _stronger_mode(modes: list[ModeComparison]) -> str:
    """Return ``single``, ``multi``, or ``tie`` from the user-score Spearman values."""

    single, multi = modes
    return _label_from_gap("single", single.correlation.spearman_r, "multi", multi.correlation.spearman_r)


def _label_from_gap(left_name: str, left_r: float, right_name: str, right_r: float) -> str:
    """Compare two Spearman values. A gap under the configured margin is a tie."""

    if pd.isna(left_r) or pd.isna(right_r):
        return "insufficient"
    gap = abs(abs(left_r) - abs(right_r))
    if gap < CORRELATION_TIE_MARGIN:
        return "tie"
    if abs(left_r) > abs(right_r):
        return left_name
    return right_name
