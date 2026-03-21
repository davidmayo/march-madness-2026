"""Update prediction checkpoint files for newly completed scoreboard games."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from march_madness.predictions import DEFAULT_RANDOM_SEED
from march_madness.predictions import DEFAULT_SIMULATION_COUNT
from march_madness.predictions import PREDICTION_CHECKPOINTS_DIR
from march_madness.predictions import PREDICTION_HISTORY_PATH
from march_madness.predictions import SCOREBOARD_PATH
from march_madness.predictions import TournamentPredictionReport
from march_madness.predictions import _build_prediction_history_from_reports
from march_madness.predictions import build_prediction_report
from march_madness.scoring import get_bracket_from_scoreboard_data_with_limit
from march_madness.scoring import get_completed_scoreboard_event_ids


@dataclass(frozen=True)
class NewCompletedGame:
    """Describe one newly completed game that requires a fresh checkpoint."""

    event_id: str
    date: str
    completed_count: int


def load_scoreboard_blob(path: Path) -> dict[str, Any]:
    """Load one scoreboard JSON blob from disk."""

    return json.loads(path.read_text())


def determine_new_completed_games(
    previous_scoreboard_blob: dict[str, Any],
    current_scoreboard_blob: dict[str, Any],
) -> list[NewCompletedGame]:
    """Return newly completed games sorted by their scoreboard start time."""

    previous_completed_event_ids = set(get_completed_scoreboard_event_ids(previous_scoreboard_blob))
    current_completed_event_ids = get_completed_scoreboard_event_ids(current_scoreboard_blob)
    current_completed_dates_by_event_id = _completed_event_dates_by_event_id(current_scoreboard_blob)

    new_games: list[NewCompletedGame] = []
    for completed_count, event_id in enumerate(current_completed_event_ids, start=1):
        if event_id in previous_completed_event_ids:
            continue
        new_games.append(
            NewCompletedGame(
                event_id=event_id,
                date=current_completed_dates_by_event_id[event_id],
                completed_count=completed_count,
            )
        )
    return new_games


def update_prediction_files_for_new_games(
    *,
    previous_scoreboard_path: Path,
    scoreboard_path: Path = SCOREBOARD_PATH,
    output_dir: Path = PREDICTION_CHECKPOINTS_DIR,
    history_output_path: Path = PREDICTION_HISTORY_PATH,
    simulation_count: int = DEFAULT_SIMULATION_COUNT,
    random_seed: int = DEFAULT_RANDOM_SEED,
) -> list[NewCompletedGame]:
    """Write checkpoint files for newly completed games and refresh history JSON."""

    previous_scoreboard_blob = load_scoreboard_blob(previous_scoreboard_path)
    current_scoreboard_blob = load_scoreboard_blob(scoreboard_path)
    new_games = determine_new_completed_games(previous_scoreboard_blob, current_scoreboard_blob)
    if not new_games:
        return []

    previous_completed_event_ids = get_completed_scoreboard_event_ids(previous_scoreboard_blob)
    current_completed_event_ids = get_completed_scoreboard_event_ids(current_scoreboard_blob)

    existing_reports = _load_existing_or_rebuild_reports(
        current_scoreboard_blob=current_scoreboard_blob,
        completed_game_count=len(previous_completed_event_ids),
        output_dir=output_dir,
        simulation_count=simulation_count,
        random_seed=random_seed,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    for new_game in new_games:
        report = _build_checkpoint_report(
            scoreboard_blob=current_scoreboard_blob,
            completed_count=new_game.completed_count,
            completed_event_ids=current_completed_event_ids,
            simulation_count=simulation_count,
            random_seed=random_seed,
        )
        checkpoint_path = output_dir / f"{new_game.completed_count:03d}-games-complete.json"
        checkpoint_path.write_text(report.model_dump_json(indent=4), encoding="utf-8")
        existing_reports.append(report)

    history = _build_prediction_history_from_reports(
        existing_reports,
        scoreboard_blob=current_scoreboard_blob,
        simulation_count=simulation_count,
        random_seed=random_seed,
    )
    history_output_path.parent.mkdir(parents=True, exist_ok=True)
    history_output_path.write_text(history.model_dump_json(indent=4), encoding="utf-8")
    return new_games


def _completed_event_dates_by_event_id(scoreboard_blob: dict[str, Any]) -> dict[str, str]:
    """Return the start-date lookup for completed events in one scoreboard blob."""

    completed_dates_by_event_id: dict[str, str] = {}
    for event in scoreboard_blob.get("events", []):
        competition = event["competitions"][0]
        if not competition["status"]["type"].get("completed", False):
            continue
        completed_dates_by_event_id[str(event["id"])] = str(event["date"])
    return completed_dates_by_event_id


def _load_existing_or_rebuild_reports(
    *,
    current_scoreboard_blob: dict[str, Any],
    completed_game_count: int,
    output_dir: Path,
    simulation_count: int,
    random_seed: int,
) -> list[TournamentPredictionReport]:
    """Load all existing reports through one checkpoint, rebuilding if needed."""

    loaded_reports: list[TournamentPredictionReport] = []
    all_reports_exist = True
    for checkpoint_count in range(completed_game_count + 1):
        checkpoint_path = output_dir / f"{checkpoint_count:03d}-games-complete.json"
        if not checkpoint_path.exists():
            all_reports_exist = False
            break
        loaded_reports.append(
            TournamentPredictionReport.model_validate_json(checkpoint_path.read_text())
        )

    if all_reports_exist:
        return loaded_reports

    rebuilt_reports: list[TournamentPredictionReport] = []
    current_completed_event_ids = get_completed_scoreboard_event_ids(current_scoreboard_blob)
    output_dir.mkdir(parents=True, exist_ok=True)
    for checkpoint_count in range(completed_game_count + 1):
        report = _build_checkpoint_report(
            scoreboard_blob=current_scoreboard_blob,
            completed_count=checkpoint_count,
            completed_event_ids=current_completed_event_ids,
            simulation_count=simulation_count,
            random_seed=random_seed,
        )
        checkpoint_path = output_dir / f"{checkpoint_count:03d}-games-complete.json"
        checkpoint_path.write_text(report.model_dump_json(indent=4), encoding="utf-8")
        rebuilt_reports.append(report)
    return rebuilt_reports


def _build_checkpoint_report(
    *,
    scoreboard_blob: dict[str, Any],
    completed_count: int,
    completed_event_ids: list[str],
    simulation_count: int,
    random_seed: int,
) -> TournamentPredictionReport:
    """Build one checkpoint report for the first ``completed_count`` results."""

    reference_bracket = get_bracket_from_scoreboard_data_with_limit(
        scoreboard_blob,
        completed_game_limit=completed_count,
    )
    return build_prediction_report(
        reference_bracket,
        games_completed=completed_count,
        completed_scoreboard_event_ids=completed_event_ids[:completed_count],
        simulation_count=simulation_count,
        random_seed=random_seed,
    )


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the incremental prediction updater."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "previous_scoreboard_path",
        help="Path to the scoreboard snapshot from before the latest refresh.",
    )
    parser.add_argument(
        "--scoreboard-path",
        default=str(SCOREBOARD_PATH),
        help="Path to the current scoreboard snapshot.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(PREDICTION_CHECKPOINTS_DIR),
        help="Directory where checkpoint reports should be written.",
    )
    parser.add_argument(
        "--history-output-path",
        default=str(PREDICTION_HISTORY_PATH),
        help="Path where the aggregate prediction-history JSON should be written.",
    )
    parser.add_argument(
        "--simulation-count",
        default=DEFAULT_SIMULATION_COUNT,
        type=int,
        help="Number of Monte Carlo simulations to run per checkpoint.",
    )
    parser.add_argument(
        "--random-seed",
        default=DEFAULT_RANDOM_SEED,
        type=int,
        help="Random seed used for deterministic Monte Carlo reports.",
    )
    return parser.parse_args()


def main() -> None:
    """Update prediction artifacts for the newly completed games and print a summary."""

    args = _parse_args()
    new_games = update_prediction_files_for_new_games(
        previous_scoreboard_path=Path(args.previous_scoreboard_path).resolve(),
        scoreboard_path=Path(args.scoreboard_path).resolve(),
        output_dir=Path(args.output_dir).resolve(),
        history_output_path=Path(args.history_output_path).resolve(),
        simulation_count=args.simulation_count,
        random_seed=args.random_seed,
    )
    summary = {
        "new_games": [asdict(new_game) for new_game in new_games],
        "last_completed_count": new_games[-1].completed_count if new_games else None,
    }
    print(json.dumps(summary, separators=(",", ":")))


if __name__ == "__main__":
    main()
