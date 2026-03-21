"""Run Monte Carlo tournament simulations from the current bracket state.

The prediction flow intentionally reuses the same canonical bracket graph used
for scoring:
- completed games come from the local ESPN scoreboard snapshot
- unfinished games are simulated with KenPom net ratings
- every simulated winner is propagated through the explicit bracket tree
- each completed simulated bracket is scored with the existing traditional
  scoring logic
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from statistics import fmean

from pydantic import BaseModel, ConfigDict, ValidationError

from march_madness.canonical_bracket import REGION_ORDER
from march_madness.canonical_bracket import canonical_team_name_by_id
from march_madness.canonical_bracket import canonical_team_seed_by_id
from march_madness.scoring import TRADITIONAL_SCORING_BY_ROUND
from march_madness.scoring import get_bracket_from_scoreboard_data_with_limit
from march_madness.scoring import get_completed_scoreboard_event_ids
from march_madness.scoring import load_saved_user_brackets
from march_madness.scoring import score
from march_madness.scrape import KENPOM_INDEX_PATH
from march_madness.scrape import KENPOM_SNAPSHOTS_DIR
from march_madness.scrape import Bracket
from march_madness.scrape import Game
from march_madness.scrape import GameSlot
from march_madness.scrape import GameStatus
from march_madness.scrape import KenPomRating
from march_madness.scrape import parse_kenpom_rows
from march_madness.user_brackets import USER_BRACKETS_DIR
from march_madness.user_brackets import UserBracket
from march_madness.user_brackets import UserCategory


ROOT = Path(__file__).resolve().parents[2]
SCOREBOARD_PATH = ROOT / "data" / "espn" / "api" / "scoreboard.json"
PREDICTIONS_DIR = ROOT / "data" / "predictions"
PREDICTION_CHECKPOINTS_DIR = PREDICTIONS_DIR / "checkpoints"
PREDICTION_HISTORY_PATH = PREDICTIONS_DIR / "prediction-history.json"

AVERAGE_TEMPO = 70.0
KENPOM_SCALE_FACTOR = 13.7420
DEFAULT_SIMULATION_COUNT = 1_000
DEFAULT_RANDOM_SEED = 20260321

# The saved KenPom snapshot is keyed by the team IDs that were available in the
# ESPN team dump at scrape time. A handful of tournament teams were missing from
# that dump, so those IDs need an explicit fallback to the raw KenPom table.
EXPLICIT_KENPOM_NAME_BY_CANONICAL_TEAM_ID: dict[str, str] = {
    "139": "Saint Louis",
    "158": "Nebraska",
    "194": "Ohio St.",
    "219": "Penn",
    "338": "Kennesaw St.",
    "2390": "Miami FL",
    "2449": "North Dakota St.",
    "2561": "Siena",
    "2628": "TCU",
}


class PredictionInterval(BaseModel):
    """Store the lower and upper bounds for one 80% prediction interval."""

    model_config = ConfigDict(extra="forbid")

    lower: float
    upper: float


class UserPredictionSummary(BaseModel):
    """Store the Monte Carlo summary for one saved user bracket."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    user_name: str
    entry_name: str
    user_categories: list[UserCategory]
    average_score: float
    average_finishing_position: float
    winning_percentage: float
    average_category_finishing_position: float | None = None
    category_winning_percentage: float | None = None
    score_interval: PredictionInterval
    finishing_position_interval: PredictionInterval
    category_finishing_position_interval: PredictionInterval | None = None


class TournamentPredictionReport(BaseModel):
    """Store the full Monte Carlo forecast used by the prediction page."""

    model_config = ConfigDict(extra="forbid")

    games_completed: int
    completed_scoreboard_event_ids: list[str]
    simulation_count: int
    random_seed: int
    completed_game_count: int
    remaining_game_count: int
    kenpom_snapshot_name: str
    users: list[UserPredictionSummary]


class UserPredictionHistoryPoint(BaseModel):
    """Store one checkpoint value for the prediction-history graphs."""

    model_config = ConfigDict(extra="forbid")

    games_completed: int
    average_score: float
    score_interval_lower: float
    score_interval_upper: float
    average_finishing_position: float
    finishing_position_interval_lower: float
    finishing_position_interval_upper: float
    average_category_finishing_position: float | None = None
    category_finishing_position_interval_lower: float | None = None
    category_finishing_position_interval_upper: float | None = None
    winning_percentage: float
    category_winning_percentage: float | None = None
    winning_percentage_interval_lower: float
    winning_percentage_interval_upper: float


class UserPredictionHistorySeries(BaseModel):
    """Store the full graph series for one user across checkpoints."""

    model_config = ConfigDict(extra="forbid")

    slug: str
    user_name: str
    entry_name: str
    user_categories: list[UserCategory]
    points: list[UserPredictionHistoryPoint]


class PredictionHistoryCheckpoint(BaseModel):
    """Describe one saved checkpoint report written to disk."""

    model_config = ConfigDict(extra="forbid")

    games_completed: int
    label: str
    completed_scoreboard_event_ids: list[str]
    report_path: str


class TournamentPredictionHistory(BaseModel):
    """Store the graph-ready prediction history across all checkpoints."""

    model_config = ConfigDict(extra="forbid")

    simulation_count: int
    random_seed: int
    completed_game_count_max: int
    checkpoints: list[PredictionHistoryCheckpoint]
    users: list[UserPredictionHistorySeries]


@dataclass
class _UserSimulationAccumulator:
    """Collect per-simulation score and finish samples for one user."""

    scores: list[float] = field(default_factory=list)
    finishing_positions: list[int] = field(default_factory=list)
    category_finishing_positions: list[int] = field(default_factory=list)
    wins: int = 0
    category_wins: int = 0


def load_latest_kenpom_ratings(
    kenpom_snapshot_dir: Path = KENPOM_SNAPSHOTS_DIR,
) -> tuple[Path, dict[str, float]]:
    """Load the newest saved KenPom snapshot as a team-ID lookup."""

    snapshot_paths = sorted(kenpom_snapshot_dir.glob("*.json"))
    if not snapshot_paths:
        msg = f"No KenPom snapshots were found in {kenpom_snapshot_dir}."
        raise FileNotFoundError(msg)

    snapshot_path = snapshot_paths[-1]
    ratings = [
        KenPomRating.model_validate(item)
        for item in json.loads(snapshot_path.read_text())
    ]
    ratings_by_team_id = {rating.team_id: rating.net_rating for rating in ratings}

    raw_rows_by_name = {
        row.team_name: row.net_rating
        for row in parse_kenpom_rows(KENPOM_INDEX_PATH)
    }
    for team_id, kenpom_name in EXPLICIT_KENPOM_NAME_BY_CANONICAL_TEAM_ID.items():
        if team_id in ratings_by_team_id:
            continue
        try:
            ratings_by_team_id[team_id] = raw_rows_by_name[kenpom_name]
        except KeyError as error:
            msg = f"Explicit KenPom fallback name {kenpom_name!r} was missing from the raw table."
            raise ValueError(msg) from error

    return snapshot_path, ratings_by_team_id


def simulate_remaining_tournament(
    reference_bracket: Bracket,
    kenpom_ratings_by_team_id: dict[str, float],
    *,
    random_generator: random.Random,
) -> Bracket:
    """Simulate every unfinished game in a copied bracket and return it."""

    _validate_kenpom_coverage(kenpom_ratings_by_team_id)

    simulated_bracket = reference_bracket.model_copy(deep=True)
    game_lookup = {game.id: game for game in simulated_bracket.games}

    # The scoreboard builder already propagates completed winners, but this
    # keeps the simulator robust if a partially populated bracket is passed in.
    _propagate_all_known_winners(simulated_bracket)

    for game in sorted(simulated_bracket.games, key=_simulation_game_sort_key):
        if game.round_id not in TRADITIONAL_SCORING_BY_ROUND:
            continue
        if game.winner_team_id is not None:
            continue

        team_1_id = game.team_1.team_id
        team_2_id = game.team_2.team_id
        if team_1_id is None or team_2_id is None:
            msg = f"Game {game.id} was missing a populated matchup before simulation."
            raise ValueError(msg)

        team_1_win_probability = _team_1_win_probability(
            team_1_rating=kenpom_ratings_by_team_id[team_1_id],
            team_2_rating=kenpom_ratings_by_team_id[team_2_id],
        )
        winner_team_id = team_1_id if random_generator.random() < team_1_win_probability else team_2_id

        game.winner_team_id = winner_team_id
        game.status = GameStatus.SIMULATED
        _propagate_winner_to_parent(game_lookup, game)

    for game in simulated_bracket.games:
        if game.round_id in TRADITIONAL_SCORING_BY_ROUND and game.winner_team_id is None:
            msg = f"Simulation did not produce a winner for {game.id}."
            raise ValueError(msg)

    return simulated_bracket


def build_prediction_report(
    reference_bracket: Bracket,
    *,
    games_completed: int | None = None,
    completed_scoreboard_event_ids: list[str] | None = None,
    simulation_count: int = DEFAULT_SIMULATION_COUNT,
    random_seed: int = DEFAULT_RANDOM_SEED,
    user_brackets_dir: Path = USER_BRACKETS_DIR,
    kenpom_snapshot_dir: Path = KENPOM_SNAPSHOTS_DIR,
) -> TournamentPredictionReport:
    """Run repeated tournament simulations and summarize every bracket."""

    if simulation_count <= 0:
        msg = f"simulation_count must be positive, got {simulation_count}."
        raise ValueError(msg)

    kenpom_snapshot_path, kenpom_ratings_by_team_id = load_latest_kenpom_ratings(kenpom_snapshot_dir)
    loaded_user_brackets = load_saved_user_brackets(user_brackets_dir)
    random_generator = random.Random(random_seed)

    user_records: list[tuple[str, UserBracket, UserCategory | None]] = [
        (
            path.stem,
            user_bracket,
            _primary_user_category(user_bracket),
        )
        for path, user_bracket in loaded_user_brackets
    ]
    accumulators = {
        slug: _UserSimulationAccumulator()
        for slug, _user_bracket, _user_category in user_records
    }

    for _simulation_index in range(simulation_count):
        simulated_bracket = simulate_remaining_tournament(
            reference_bracket,
            kenpom_ratings_by_team_id,
            random_generator=random_generator,
        )

        score_by_slug: dict[str, float] = {}
        category_by_slug: dict[str, UserCategory | None] = {}
        for slug, user_bracket, user_category in user_records:
            user_score = score(simulated_bracket, user_bracket)
            score_by_slug[slug] = user_score.current_score
            category_by_slug[slug] = user_category
            accumulators[slug].scores.append(user_score.current_score)

        global_ranks = _competition_ranks(score_by_slug)
        for slug, rank in global_ranks.items():
            accumulators[slug].finishing_positions.append(rank)
            if rank == 1:
                accumulators[slug].wins += 1

        for user_category in sorted({category for category in category_by_slug.values() if category is not None}):
            category_scores = {
                slug: user_score
                for slug, user_score in score_by_slug.items()
                if category_by_slug[slug] == user_category
            }
            category_ranks = _competition_ranks(category_scores)
            for slug, rank in category_ranks.items():
                accumulators[slug].category_finishing_positions.append(rank)
                if rank == 1:
                    accumulators[slug].category_wins += 1

    user_summaries = [
        _build_user_prediction_summary(
            slug=slug,
            user_bracket=user_bracket,
            accumulator=accumulators[slug],
        )
        for slug, user_bracket, _user_category in user_records
    ]
    user_summaries.sort(
        key=lambda summary: (
            summary.average_finishing_position,
            -summary.average_score,
            summary.user_name,
        )
    )

    completed_game_count = sum(
        1
        for game in reference_bracket.games
        if game.round_id in TRADITIONAL_SCORING_BY_ROUND and game.winner_team_id is not None
    )
    total_scored_games = sum(
        1
        for game in reference_bracket.games
        if game.round_id in TRADITIONAL_SCORING_BY_ROUND
    )

    return TournamentPredictionReport(
        games_completed=completed_game_count if games_completed is None else games_completed,
        completed_scoreboard_event_ids=list(completed_scoreboard_event_ids or []),
        simulation_count=simulation_count,
        random_seed=random_seed,
        completed_game_count=completed_game_count,
        remaining_game_count=total_scored_games - completed_game_count,
        kenpom_snapshot_name=kenpom_snapshot_path.stem,
        users=user_summaries,
    )


def build_prediction_history(
    scoreboard_blob: dict[str, object],
    *,
    simulation_count: int = DEFAULT_SIMULATION_COUNT,
    random_seed: int = DEFAULT_RANDOM_SEED,
    user_brackets_dir: Path = USER_BRACKETS_DIR,
    kenpom_snapshot_dir: Path = KENPOM_SNAPSHOTS_DIR,
) -> tuple[TournamentPredictionHistory, list[TournamentPredictionReport]]:
    """Build checkpoint reports for 0 through N completed games."""

    completed_scoreboard_event_ids = get_completed_scoreboard_event_ids(scoreboard_blob)
    checkpoint_reports: list[TournamentPredictionReport] = []

    for completed_game_count in range(len(completed_scoreboard_event_ids) + 1):
        reference_bracket = get_bracket_from_scoreboard_data_with_limit(
            scoreboard_blob,
            completed_game_limit=completed_game_count,
        )
        checkpoint_reports.append(
            build_prediction_report(
                reference_bracket,
                games_completed=completed_game_count,
                completed_scoreboard_event_ids=completed_scoreboard_event_ids[:completed_game_count],
                simulation_count=simulation_count,
                random_seed=random_seed,
                user_brackets_dir=user_brackets_dir,
                kenpom_snapshot_dir=kenpom_snapshot_dir,
            )
        )

    return (
        _build_prediction_history_from_reports(
            checkpoint_reports,
            scoreboard_blob=scoreboard_blob,
            simulation_count=simulation_count,
            random_seed=random_seed,
        ),
        checkpoint_reports,
    )


def write_prediction_history_files(
    *,
    scoreboard_path: Path = SCOREBOARD_PATH,
    output_dir: Path = PREDICTION_CHECKPOINTS_DIR,
    history_output_path: Path = PREDICTION_HISTORY_PATH,
    simulation_count: int = DEFAULT_SIMULATION_COUNT,
    random_seed: int = DEFAULT_RANDOM_SEED,
    user_brackets_dir: Path = USER_BRACKETS_DIR,
    kenpom_snapshot_dir: Path = KENPOM_SNAPSHOTS_DIR,
) -> TournamentPredictionHistory:
    """Write every checkpoint report plus the aggregate history JSON."""

    scoreboard_blob = json.loads(scoreboard_path.read_text())
    prediction_history, checkpoint_reports = build_prediction_history(
        scoreboard_blob,
        simulation_count=simulation_count,
        random_seed=random_seed,
        user_brackets_dir=user_brackets_dir,
        kenpom_snapshot_dir=kenpom_snapshot_dir,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    for checkpoint_report in checkpoint_reports:
        checkpoint_path = output_dir / f"{checkpoint_report.games_completed:03d}-games-complete.json"
        checkpoint_path.write_text(checkpoint_report.model_dump_json(indent=4))

    history_output_path.parent.mkdir(parents=True, exist_ok=True)
    history_output_path.write_text(prediction_history.model_dump_json(indent=4))
    return prediction_history


def load_prediction_history(history_path: Path = PREDICTION_HISTORY_PATH) -> TournamentPredictionHistory:
    """Load the saved prediction-history JSON, generating it if needed."""

    if not history_path.exists():
        return write_prediction_history_files(history_output_path=history_path)
    try:
        return TournamentPredictionHistory.model_validate_json(history_path.read_text())
    except ValidationError:
        return write_prediction_history_files(history_output_path=history_path)


def _build_user_prediction_summary(
    *,
    slug: str,
    user_bracket: UserBracket,
    accumulator: _UserSimulationAccumulator,
) -> UserPredictionSummary:
    """Convert collected simulation samples into one user-facing summary."""

    category_interval: PredictionInterval | None = None
    average_category_finishing_position: float | None = None
    category_winning_percentage: float | None = None
    if accumulator.category_finishing_positions:
        average_category_finishing_position = fmean(accumulator.category_finishing_positions)
        category_winning_percentage = (
            accumulator.category_wins / len(accumulator.category_finishing_positions)
        ) * 100.0
        category_interval = PredictionInterval(
            lower=float(_nearest_rank_percentile(accumulator.category_finishing_positions, 0.10)),
            upper=float(_nearest_rank_percentile(accumulator.category_finishing_positions, 0.90)),
        )

    return UserPredictionSummary(
        slug=slug,
        user_name=user_bracket.bracket_metadata.user_name,
        entry_name=user_bracket.bracket_metadata.entry_name,
        user_categories=list(user_bracket.bracket_metadata.user_categories),
        average_score=fmean(accumulator.scores),
        average_finishing_position=fmean(accumulator.finishing_positions),
        winning_percentage=(accumulator.wins / len(accumulator.finishing_positions)) * 100.0,
        average_category_finishing_position=average_category_finishing_position,
        category_winning_percentage=category_winning_percentage,
        score_interval=PredictionInterval(
            lower=float(_nearest_rank_percentile(accumulator.scores, 0.10)),
            upper=float(_nearest_rank_percentile(accumulator.scores, 0.90)),
        ),
        finishing_position_interval=PredictionInterval(
            lower=float(_nearest_rank_percentile(accumulator.finishing_positions, 0.10)),
            upper=float(_nearest_rank_percentile(accumulator.finishing_positions, 0.90)),
        ),
        category_finishing_position_interval=category_interval,
    )


def _build_prediction_history_from_reports(
    checkpoint_reports: list[TournamentPredictionReport],
    *,
    scoreboard_blob: dict[str, object],
    simulation_count: int,
    random_seed: int,
) -> TournamentPredictionHistory:
    """Convert checkpoint reports into graph-ready per-user history series."""

    user_series_by_slug: dict[str, UserPredictionHistorySeries] = {}
    checkpoint_metadata: list[PredictionHistoryCheckpoint] = []
    checkpoint_label_by_completed_games = _build_checkpoint_labels(scoreboard_blob)

    for checkpoint_report in checkpoint_reports:
        checkpoint_metadata.append(
            PredictionHistoryCheckpoint(
                games_completed=checkpoint_report.games_completed,
                label=checkpoint_label_by_completed_games[checkpoint_report.games_completed],
                completed_scoreboard_event_ids=checkpoint_report.completed_scoreboard_event_ids,
                report_path=f"checkpoints/{checkpoint_report.games_completed:03d}-games-complete.json",
            )
        )
        for user_summary in checkpoint_report.users:
            series = user_series_by_slug.setdefault(
                user_summary.slug,
                UserPredictionHistorySeries(
                    slug=user_summary.slug,
                    user_name=user_summary.user_name,
                    entry_name=user_summary.entry_name,
                    user_categories=user_summary.user_categories,
                    points=[],
                ),
            )
            series.points.append(
                _build_user_prediction_history_point(
                    checkpoint_report=checkpoint_report,
                    user_summary=user_summary,
                )
            )

    ordered_users = sorted(
        user_series_by_slug.values(),
        key=lambda series: (
            series.points[-1].average_finishing_position if series.points else float("inf"),
            series.user_name,
        ),
    )
    return TournamentPredictionHistory(
        simulation_count=simulation_count,
        random_seed=random_seed,
        completed_game_count_max=checkpoint_reports[-1].games_completed if checkpoint_reports else 0,
        checkpoints=checkpoint_metadata,
        users=ordered_users,
    )


def _build_user_prediction_history_point(
    *,
    checkpoint_report: TournamentPredictionReport,
    user_summary: UserPredictionSummary,
) -> UserPredictionHistoryPoint:
    """Convert one report summary into a graph-ready checkpoint point."""

    winning_interval = _winning_percentage_interval(
        winning_percentage=user_summary.winning_percentage,
        simulation_count=checkpoint_report.simulation_count,
    )
    return UserPredictionHistoryPoint(
        games_completed=checkpoint_report.games_completed,
        average_score=user_summary.average_score,
        score_interval_lower=user_summary.score_interval.lower,
        score_interval_upper=user_summary.score_interval.upper,
        average_finishing_position=user_summary.average_finishing_position,
        finishing_position_interval_lower=user_summary.finishing_position_interval.lower,
        finishing_position_interval_upper=user_summary.finishing_position_interval.upper,
        average_category_finishing_position=user_summary.average_category_finishing_position,
        category_finishing_position_interval_lower=(
            user_summary.category_finishing_position_interval.lower
            if user_summary.category_finishing_position_interval is not None
            else None
        ),
        category_finishing_position_interval_upper=(
            user_summary.category_finishing_position_interval.upper
            if user_summary.category_finishing_position_interval is not None
            else None
        ),
        winning_percentage=user_summary.winning_percentage,
        category_winning_percentage=user_summary.category_winning_percentage,
        winning_percentage_interval_lower=winning_interval.lower,
        winning_percentage_interval_upper=winning_interval.upper,
    )


def _winning_percentage_interval(
    *,
    winning_percentage: float,
    simulation_count: int,
) -> PredictionInterval:
    """Return an 80% Wilson interval for one simulated win percentage."""

    if simulation_count <= 0:
        msg = f"simulation_count must be positive, got {simulation_count}."
        raise ValueError(msg)

    z_score = 1.2815515655446004  # Two-sided 80% interval.
    win_count = round((winning_percentage / 100.0) * simulation_count)
    win_fraction = win_count / simulation_count
    denominator = 1.0 + ((z_score**2) / simulation_count)
    center = (
        win_fraction
        + ((z_score**2) / (2.0 * simulation_count))
    ) / denominator
    margin = (
        z_score
        * math.sqrt(
            (
                (win_fraction * (1.0 - win_fraction)) / simulation_count
            ) + ((z_score**2) / (4.0 * (simulation_count**2)))
        )
    ) / denominator
    return PredictionInterval(
        lower=max(0.0, (center - margin) * 100.0),
        upper=min(100.0, (center + margin) * 100.0),
    )


def _build_checkpoint_labels(scoreboard_blob: dict[str, object]) -> dict[int, str]:
    """Build the x-axis label used for each completed-game checkpoint."""

    completed_event_ids = get_completed_scoreboard_event_ids(scoreboard_blob)
    event_lookup = {
        str(event["id"]): event
        for event in scoreboard_blob["events"]  # type: ignore[index]
    }
    checkpoint_labels = {0: "Initial"}
    for completed_game_count, event_id in enumerate(completed_event_ids, start=1):
        scoreboard_event = event_lookup[event_id]
        checkpoint_labels[completed_game_count] = _scoreboard_event_label(scoreboard_event)
    return checkpoint_labels


def _scoreboard_event_label(scoreboard_event: dict[str, object]) -> str:
    """Return a compact winner-beats-loser label for one completed game."""

    competition = scoreboard_event["competitions"][0]  # type: ignore[index]
    competitors = competition["competitors"]  # type: ignore[index]
    winner = next(
        competitor
        for competitor in competitors
        if competitor.get("winner")
    )
    loser = next(
        competitor
        for competitor in competitors
        if not competitor.get("winner")
    )
    winner_label = _scoreboard_team_axis_label(winner["team"])  # type: ignore[index]
    loser_label = _scoreboard_team_axis_label(loser["team"])  # type: ignore[index]
    return f"{winner_label} beats {loser_label}"


def _scoreboard_team_axis_label(scoreboard_team: dict[str, object]) -> str:
    """Return the short team label used on the prediction chart x axis."""

    abbreviation = scoreboard_team.get("abbreviation")
    if isinstance(abbreviation, str) and abbreviation and abbreviation != "TBD":
        return abbreviation

    short_display_name = scoreboard_team.get("shortDisplayName")
    if isinstance(short_display_name, str) and short_display_name:
        return short_display_name

    display_name = scoreboard_team.get("displayName")
    if isinstance(display_name, str) and display_name:
        return display_name

    team_id = scoreboard_team.get("id")
    return str(team_id) if team_id is not None else "Unknown"


def _primary_user_category(user_bracket: UserBracket) -> UserCategory | None:
    """Return the single configured category for a user bracket, if present."""

    user_categories = user_bracket.bracket_metadata.user_categories
    if not user_categories:
        return None
    return user_categories[0]


def _validate_kenpom_coverage(kenpom_ratings_by_team_id: dict[str, float]) -> None:
    """Ensure every canonical tournament team has a KenPom rating."""

    missing_team_ids = sorted(
        team_id
        for team_id in canonical_team_name_by_id()
        if team_id not in kenpom_ratings_by_team_id
    )
    if not missing_team_ids:
        return

    missing_team_names = [
        canonical_team_name_by_id()[team_id]
        for team_id in missing_team_ids
    ]
    msg = f"KenPom ratings were missing for canonical tournament teams: {missing_team_names!r}"
    raise ValueError(msg)


def _simulation_game_sort_key(game: Game) -> tuple[int, int, int]:
    """Order games so every child matchup is simulated before its parent."""

    region_order = {region: index for index, region in enumerate(REGION_ORDER, start=1)}
    return (
        game.round_id,
        region_order.get(game.region_name or "", len(region_order) + 1),
        game.bracket_location,
    )


def _team_1_win_probability(*, team_1_rating: float, team_2_rating: float) -> float:
    """Calculate the team-1 win probability from two KenPom net ratings."""

    team_2_scoring_margin = (team_2_rating - team_1_rating) * (AVERAGE_TEMPO / 100.0)
    return 1.0 / (1.0 + (10.0 ** (team_2_scoring_margin / KENPOM_SCALE_FACTOR)))


def _competition_ranks(score_by_slug: dict[str, float]) -> dict[str, int]:
    """Assign shared-place ranks where ties occupy the same finishing position."""

    score_counts = Counter(score_by_slug.values())
    rank_by_score: dict[float, int] = {}
    higher_score_count = 0

    for score_value in sorted(score_counts, reverse=True):
        rank_by_score[score_value] = higher_score_count + 1
        higher_score_count += score_counts[score_value]

    return {
        slug: rank_by_score[user_score]
        for slug, user_score in score_by_slug.items()
    }


def _nearest_rank_percentile(values: list[float] | list[int], percentile: float) -> float | int:
    """Return one percentile using the nearest-rank definition."""

    if not values:
        msg = "Cannot calculate a percentile from an empty sample."
        raise ValueError(msg)
    if not 0.0 <= percentile <= 1.0:
        msg = f"percentile must be between 0 and 1 inclusive, got {percentile}."
        raise ValueError(msg)

    ordered_values = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered_values)) - 1)
    return ordered_values[index]


def _propagate_all_known_winners(bracket: Bracket) -> None:
    """Propagate every known winner through downstream bracket slots."""

    game_lookup = {game.id: game for game in bracket.games}
    changed = True
    while changed:
        changed = False
        for game in bracket.games:
            if game.winner_team_id is None:
                continue
            if _propagate_winner_to_parent(game_lookup, game):
                changed = True


def _propagate_winner_to_parent(game_lookup: dict[str, Game], game: Game) -> bool:
    """Push one winner into the exact downstream slot fed by that game."""

    if game.winner_team_id is None or game.feeds_to_game_id is None:
        return False

    parent_game = game_lookup[game.feeds_to_game_id]
    winner_seed = canonical_team_seed_by_id().get(game.winner_team_id)

    if parent_game.team_1.from_game_id == game.id:
        return _assign_slot_team(parent_game.team_1, game.winner_team_id, winner_seed, parent_game.id)
    if parent_game.team_2.from_game_id == game.id:
        return _assign_slot_team(parent_game.team_2, game.winner_team_id, winner_seed, parent_game.id)

    msg = f"Game {game.id} did not match either incoming slot on parent {parent_game.id}."
    raise ValueError(msg)


def _assign_slot_team(slot: GameSlot, team_id: str, seed: str | None, parent_game_id: str) -> bool:
    """Assign a propagated team into a bracket slot with consistency checks."""

    if slot.team_id not in (None, team_id):
        msg = f"Conflicting team assignment on {parent_game_id}: {slot.team_id} vs {team_id}"
        raise ValueError(msg)
    if seed is not None and slot.seed not in (None, seed):
        msg = f"Conflicting seed assignment on {parent_game_id}: {slot.seed} vs {seed}"
        raise ValueError(msg)

    changed = slot.team_id != team_id or slot.seed != seed
    slot.team_id = team_id
    slot.seed = seed
    return changed


def main() -> None:
    """Write the full prediction-history checkpoint set to disk."""

    prediction_history = write_prediction_history_files()
    print(
        "Wrote prediction history for "
        f"{prediction_history.completed_game_count_max + 1} checkpoints "
        f"to {PREDICTIONS_DIR}."
    )


if __name__ == "__main__":
    main()
