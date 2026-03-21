"""Score saved user brackets against the canonical 2026 bracket definition.

This module intentionally avoids fuzzy matching. The reference bracket is built
from the explicit 2026 field in :mod:`march_madness.canonical_bracket`, and
scoreboard results are applied only by:
- exact ESPN event ID when that ID is known for the bracket slot
- exact participant team-ID pair once upstream winners have populated a game
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from march_madness.canonical_bracket import build_canonical_bracket
from march_madness.canonical_bracket import canonical_team_seed_by_id
from march_madness.canonical_bracket import child_game_keys_for
from march_madness.scrape import Bracket
from march_madness.scrape import Game
from march_madness.scrape import GameSlot
from march_madness.scrape import GameStatus
from march_madness.user_brackets import USER_BRACKETS_DIR
from march_madness.user_brackets import UserBracket


TRADITIONAL_SCORING_BY_ROUND: dict[int, int] = {
    1: 1,
    2: 2,
    3: 4,
    4: 8,
    5: 16,
    6: 32,
}


class UserBracketScore(BaseModel):
    """Store the score summary for one user bracket."""

    model_config = ConfigDict(extra="forbid")

    current_score: float
    max_possible_score: float | None = None
    correctly_picked_games: int | None = None
    incorrectly_picked_games: int | None = None

    def __float__(self) -> float:
        """Expose the current score as a plain float when needed."""

        return self.current_score


@dataclass(frozen=True)
class _CompletedScoreboardGame:
    """Represent the exact completed-game data needed for bracket scoring."""

    event_id: str
    date: str
    team_1_id: str
    team_2_id: str
    winner_team_id: str


def score(
    reference_bracket: Bracket,
    user_backet: UserBracket,
    *,
    scoring_mode: Literal["traditional"] = "traditional",
    calculate_details: bool = False,
) -> UserBracketScore:
    """Score a user bracket against a reference bracket state."""

    if scoring_mode != "traditional":
        msg = f"Unsupported scoring mode: {scoring_mode}"
        raise ValueError(msg)

    completed_reference_games = _completed_reference_game_lookup(reference_bracket)
    user_game_lookup = {game.game_key: game for game in user_backet.bracket_picks.games}

    current_score = 0.0
    correctly_picked_games = 0
    incorrectly_picked_games = 0

    for game_key, game in completed_reference_games.items():
        user_game = user_game_lookup.get(game_key)
        if user_game is None or game.winner_team_id is None:
            continue

        picked_winner_team_id = _picked_winner_team_id(user_game)
        if picked_winner_team_id is None:
            continue

        if picked_winner_team_id == game.winner_team_id:
            current_score += _traditional_points_for_round(game.round_id)
            if calculate_details:
                correctly_picked_games += 1
        elif calculate_details:
            incorrectly_picked_games += 1

    max_possible_score: float | None = None
    if calculate_details:
        max_possible_score = _calculate_max_possible_score(
            reference_bracket=reference_bracket,
            user_bracket=user_backet,
        )

    return UserBracketScore(
        current_score=current_score,
        max_possible_score=max_possible_score,
        correctly_picked_games=correctly_picked_games if calculate_details else None,
        incorrectly_picked_games=incorrectly_picked_games if calculate_details else None,
    )


def get_bracket_from_scoreboard_data(espn_api_scoreboard_blob: Any) -> Bracket:
    """Build the canonical reference bracket for the completed scoreboard games."""

    current_bracket = build_canonical_bracket().model_copy(deep=True)
    _apply_scoreboard_results(
        reference_bracket=current_bracket,
        espn_api_scoreboard_blob=espn_api_scoreboard_blob,
    )
    return current_bracket


def load_user_bracket(path: Path) -> UserBracket:
    """Load one exported user bracket JSON file."""

    return UserBracket.model_validate_json(path.read_text())


def load_saved_user_brackets(user_brackets_dir: Path = USER_BRACKETS_DIR) -> list[tuple[Path, UserBracket]]:
    """Load all exported user bracket JSON files from disk."""

    loaded_brackets: list[tuple[Path, UserBracket]] = []
    for path in sorted(user_brackets_dir.glob("*.json")):
        loaded_brackets.append((path, load_user_bracket(path)))
    return loaded_brackets


def score_saved_user_brackets(
    reference_bracket: Bracket,
    *,
    scoring_mode: Literal["traditional"] = "traditional",
    calculate_details: bool = False,
    user_brackets_dir: Path = USER_BRACKETS_DIR,
) -> list[tuple[Path, UserBracket, UserBracketScore]]:
    """Score every exported user bracket and return them in score order."""

    scored: list[tuple[Path, UserBracket, UserBracketScore]] = []
    for path, user_bracket in load_saved_user_brackets(user_brackets_dir):
        scored.append(
            (
                path,
                user_bracket,
                score(
                    reference_bracket=reference_bracket,
                    user_backet=user_bracket,
                    scoring_mode=scoring_mode,
                    calculate_details=calculate_details,
                ),
            )
        )

    return sorted(
        scored,
        key=lambda item: (
            item[2].current_score,
            item[2].max_possible_score or float("-inf"),
            item[1].bracket_metadata.user_name,
        ),
        reverse=True,
    )


def _completed_reference_game_lookup(reference_bracket: Bracket) -> dict[str, Game]:
    """Build a lookup of completed games keyed by canonical game key."""

    return {
        game.id: game
        for game in reference_bracket.games
        if game.round_id in TRADITIONAL_SCORING_BY_ROUND and game.winner_team_id is not None
    }


def _apply_scoreboard_results(reference_bracket: Bracket, espn_api_scoreboard_blob: Any) -> int:
    """Apply completed scoreboard results onto the canonical bracket template."""

    completed_games = _completed_games_from_scoreboard_blob(espn_api_scoreboard_blob)
    game_lookup = {game.id: game for game in reference_bracket.games}
    game_by_espn_event_id = {
        game.espn_event_id: game for game in reference_bracket.games if game.espn_event_id is not None
    }
    matched_games = 0

    for completed_game in completed_games:
        _propagate_all_known_winners(reference_bracket)

        bracket_game = game_by_espn_event_id.get(completed_game.event_id)
        if bracket_game is None:
            bracket_game = _find_game_by_exact_team_ids(
                reference_bracket=reference_bracket,
                team_1_id=completed_game.team_1_id,
                team_2_id=completed_game.team_2_id,
            )
        if bracket_game is None:
            msg = (
                "Could not map completed scoreboard game to the canonical bracket: "
                f"event_id={completed_game.event_id} teams="
                f"{completed_game.team_1_id}/{completed_game.team_2_id}"
            )
            raise ValueError(msg)

        if bracket_game.winner_team_id not in (None, completed_game.winner_team_id):
            msg = (
                f"Conflicting winners for {bracket_game.id}: "
                f"{bracket_game.winner_team_id} vs {completed_game.winner_team_id}"
            )
            raise ValueError(msg)

        bracket_game.winner_team_id = completed_game.winner_team_id
        bracket_game.status = GameStatus.FINISHED
        _propagate_winner_to_parent(game_lookup, bracket_game)
        matched_games += 1

    _propagate_all_known_winners(reference_bracket)
    return matched_games


def _completed_games_from_scoreboard_blob(espn_api_scoreboard_blob: Any) -> list[_CompletedScoreboardGame]:
    """Extract only completed scoreboard events with concrete ESPN team IDs."""

    completed_games: list[_CompletedScoreboardGame] = []
    for event in espn_api_scoreboard_blob.get("events", []):
        competition = event["competitions"][0]
        status = competition["status"]["type"]
        if not status.get("completed", False):
            continue

        competitors = sorted(competition["competitors"], key=lambda competitor: competitor["order"])
        team_1_id = _scoreboard_team_id(competitors[0]["team"])
        team_2_id = _scoreboard_team_id(competitors[1]["team"])
        winner = next(competitor for competitor in competitors if competitor.get("winner"))
        winner_team_id = _scoreboard_team_id(winner["team"])
        if team_1_id is None or team_2_id is None or winner_team_id is None:
            msg = f"Completed event {event['id']} was missing a concrete ESPN team ID."
            raise ValueError(msg)

        completed_games.append(
            _CompletedScoreboardGame(
                event_id=str(event["id"]),
                date=str(event["date"]),
                team_1_id=team_1_id,
                team_2_id=team_2_id,
                winner_team_id=winner_team_id,
            )
        )

    return sorted(completed_games, key=lambda game: (game.date, game.event_id))


def _scoreboard_team_id(team_blob: dict[str, Any]) -> str | None:
    """Extract a concrete ESPN team ID from a scoreboard team blob."""

    team_id = str(team_blob["id"])
    if team_id.startswith("-"):
        return None
    return team_id


def _find_game_by_exact_team_ids(reference_bracket: Bracket, team_1_id: str, team_2_id: str) -> Game | None:
    """Find the one canonical game whose two populated slots match both team IDs."""

    target_team_ids = {team_1_id, team_2_id}
    matches: list[Game] = []

    for game in reference_bracket.games:
        if game.round_id not in TRADITIONAL_SCORING_BY_ROUND:
            continue
        if game.team_1.team_id is None or game.team_2.team_id is None:
            continue
        if {game.team_1.team_id, game.team_2.team_id} == target_team_ids:
            matches.append(game)

    if not matches:
        return None
    if len(matches) > 1:
        msg = f"Multiple canonical games matched team IDs {team_1_id}/{team_2_id}: {[game.id for game in matches]!r}"
        raise ValueError(msg)
    return matches[0]


def _propagate_all_known_winners(reference_bracket: Bracket) -> None:
    """Propagate finished winners through the bracket graph until stable."""

    game_lookup = {game.id: game for game in reference_bracket.games}
    changed = True
    while changed:
        changed = False
        for game in reference_bracket.games:
            if game.winner_team_id is None:
                continue
            if _propagate_winner_to_parent(game_lookup, game):
                changed = True


def _propagate_winner_to_parent(game_lookup: dict[str, Game], game: Game) -> bool:
    """Push one game's winner into the correct downstream slot, if any."""

    if game.winner_team_id is None or game.feeds_to_game_id is None:
        return False

    parent_game = game_lookup[game.feeds_to_game_id]
    winner_seed = canonical_team_seed_by_id().get(game.winner_team_id)

    if parent_game.team_1.from_game_id == game.id:
        return _assign_slot_team(parent_game.team_1, game.winner_team_id, winner_seed, parent_game.id)
    if parent_game.team_2.from_game_id == game.id:
        return _assign_slot_team(parent_game.team_2, game.winner_team_id, winner_seed, parent_game.id)

    msg = f"Game {game.id} did not match either incoming slot on parent {parent_game.id}"
    raise ValueError(msg)


def _assign_slot_team(slot: GameSlot, team_id: str, seed: str | None, parent_game_id: str) -> bool:
    """Assign a propagated winner to one bracket slot with consistency checks."""

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


def _traditional_points_for_round(round_id: int) -> int:
    """Return the traditional bracket point value for a given round."""

    return TRADITIONAL_SCORING_BY_ROUND[round_id]


def _calculate_max_possible_score(reference_bracket: Bracket, user_bracket: UserBracket) -> float:
    """Calculate the maximum score the user can still reach from the current state."""

    completed_winners = {
        game_key: game.winner_team_id
        for game_key, game in _completed_reference_game_lookup(reference_bracket).items()
    }
    user_game_lookup = {game.game_key: game for game in user_bracket.bracket_picks.games}

    def is_pick_alive(game_key: str) -> bool:
        game = user_game_lookup[game_key]
        picked_winner_id = _picked_winner_team_id(game)
        actual_winner_id = completed_winners.get(game_key)

        if picked_winner_id is None:
            return False
        if actual_winner_id is not None:
            return actual_winner_id == picked_winner_id

        child_game_keys = child_game_keys_for(game_key)
        if child_game_keys is None:
            return True

        if game.picked_team_1.team_id == picked_winner_id:
            return is_pick_alive(child_game_keys[0])
        if game.picked_team_2.team_id == picked_winner_id:
            return is_pick_alive(child_game_keys[1])
        return False

    max_possible_score = 0.0
    for game in user_bracket.bracket_picks.games:
        actual_winner_id = completed_winners.get(game.game_key)
        picked_winner_id = _picked_winner_team_id(game)
        if picked_winner_id is None:
            continue

        game_points = _traditional_points_for_round(game.round_id)
        if actual_winner_id is not None:
            if actual_winner_id == picked_winner_id:
                max_possible_score += game_points
            continue

        if is_pick_alive(game.game_key):
            max_possible_score += game_points

    return max_possible_score


def _picked_winner_team_id(user_game: Any) -> str | None:
    """Resolve the picked winner to an ESPN team ID from the user-bracket JSON."""

    if user_game.picked_winner.team_id is not None:
        return user_game.picked_winner.team_id
    if _picked_teams_match(user_game.picked_winner, user_game.picked_team_1):
        return user_game.picked_team_1.team_id
    if _picked_teams_match(user_game.picked_winner, user_game.picked_team_2):
        return user_game.picked_team_2.team_id
    return None


def _picked_teams_match(team_1: Any, team_2: Any) -> bool:
    """Return whether two user-bracket pick objects represent the same team."""

    return team_1.team_name == team_2.team_name and team_1.seed == team_2.seed
