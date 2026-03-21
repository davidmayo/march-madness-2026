"""Score saved user brackets against a reference bracket state.

This module has two jobs:
- build a ``Bracket`` instance that reflects the games already finished
- score one or many ``UserBracket`` objects against that reference state

The repository currently contains two different bracket templates:
- ``data/structs/2026-starting-bracket.json`` from ESPN
- the saved NCAA bracket pages in ``data/brackets``

When the ESPN template can be matched to the scoreboard feed, we use it. If the
scoreboard and saved user brackets clearly belong to a different bracket layout,
we fall back to a template reconstructed from one saved NCAA bracket page so the
scoring still reflects the user bracket data.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from march_madness.scrape import Bracket
from march_madness.scrape import BracketRegion
from march_madness.scrape import BracketRound
from march_madness.scrape import Game
from march_madness.scrape import GameSlot
from march_madness.scrape import GameStatus
from march_madness.user_brackets import BRACKETS_DIR
from march_madness.user_brackets import REGION_ORDER
from march_madness.user_brackets import ROUND_NAMES
from march_madness.user_brackets import USER_BRACKETS_DIR
from march_madness.user_brackets import UserBracket
from march_madness.user_brackets import _parse_saved_iframe_page


ROOT = Path(__file__).resolve().parents[2]
STARTING_BRACKET_PATH = ROOT / "data" / "structs" / "2026-starting-bracket.json"
TEAMS_PATH = ROOT / "data" / "structs" / "2026-teams.json"

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
    """Represent the completed subset of scoreboard data needed for scoring."""

    event_id: str
    team_1_name: str
    team_2_name: str
    winner_name: str


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
        if user_game is None:
            continue

        actual_winner_key = _game_winner_compare_key(game)
        if actual_winner_key is None:
            continue

        picked_winner_key = _normalize_team_key(user_game.picked_winner.team_name)
        if picked_winner_key == actual_winner_key:
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
    """Build a reference bracket containing the scoreboard's completed results.

    The preferred path starts from the saved ESPN bracket template. If that
    template cannot be matched back to the scoreboard feed, we rebuild the
    bracket structure from one saved NCAA bracket page instead. That fallback
    keeps scoring aligned with the user bracket JSON exports.
    """

    current_bracket = Bracket.model_validate_json(STARTING_BRACKET_PATH.read_text())
    matched_games = _apply_scoreboard_results(
        reference_bracket=current_bracket,
        espn_api_scoreboard_blob=espn_api_scoreboard_blob,
    )
    if matched_games > 0:
        return current_bracket

    current_bracket = _build_saved_ncaa_reference_bracket()
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
    """Build a game-key lookup for completed games in the reference bracket."""

    regional_groups: dict[tuple[int, str], list[Game]] = {}
    semifinal_games: list[Game] = []
    championship_games: list[Game] = []

    for game in reference_bracket.games:
        if game.round_id not in TRADITIONAL_SCORING_BY_ROUND:
            continue
        if game.round_id <= 4:
            if game.region_name is None:
                continue
            regional_groups.setdefault((game.round_id, game.region_name.upper()), []).append(game)
        elif game.round_id == 5:
            semifinal_games.append(game)
        elif game.round_id == 6:
            championship_games.append(game)

    completed_games: dict[str, Game] = {}

    for region in REGION_ORDER:
        for round_id in range(1, 5):
            games = sorted(
                regional_groups.get((round_id, region), []),
                key=lambda game: game.bracket_location,
            )
            for matchup_index, game in enumerate(games, start=1):
                if game.winner_team_id is None:
                    continue
                completed_games[_regional_game_key(region, round_id, matchup_index)] = game

    for matchup_index, game in enumerate(sorted(semifinal_games, key=lambda item: item.bracket_location), start=1):
        if game.winner_team_id is not None:
            completed_games[f"final-four-{matchup_index}"] = game

    if championship_games:
        championship_game = sorted(championship_games, key=lambda item: item.bracket_location)[0]
        if championship_game.winner_team_id is not None:
            completed_games["championship-1"] = championship_game

    return completed_games


def _game_winner_compare_key(game: Game) -> str | None:
    """Resolve the winning team in a reference game to a normalized compare key."""

    if game.winner_team_id is None:
        return None

    winning_slot = None
    if game.team_1.team_id == game.winner_team_id:
        winning_slot = game.team_1
    elif game.team_2.team_id == game.winner_team_id:
        winning_slot = game.team_2
    else:
        winning_slot = GameSlot(team_id=game.winner_team_id)

    if winning_slot.team_id is None:
        return None

    winning_name = _resolve_team_identifier_to_name(winning_slot.team_id)
    return _normalize_team_key(winning_name)


def _apply_scoreboard_results(reference_bracket: Bracket, espn_api_scoreboard_blob: Any) -> int:
    """Apply completed scoreboard results onto a reference bracket template."""

    completed_games = _completed_games_from_scoreboard_blob(espn_api_scoreboard_blob)
    matched_games = 0

    for completed_game in completed_games:
        bracket_game = _find_reference_game_for_scoreboard_result(reference_bracket, completed_game)
        if bracket_game is None:
            continue

        winner_key = _normalize_team_key(completed_game.winner_name)
        team_1_key = _normalize_team_key(_resolve_slot_name(bracket_game.team_1))
        team_2_key = _normalize_team_key(_resolve_slot_name(bracket_game.team_2))

        if winner_key == team_1_key and bracket_game.team_1.team_id is not None:
            bracket_game.winner_team_id = bracket_game.team_1.team_id
            bracket_game.status = GameStatus.FINISHED
            matched_games += 1
        elif winner_key == team_2_key and bracket_game.team_2.team_id is not None:
            bracket_game.winner_team_id = bracket_game.team_2.team_id
            bracket_game.status = GameStatus.FINISHED
            matched_games += 1

    return matched_games


def _completed_games_from_scoreboard_blob(espn_api_scoreboard_blob: Any) -> list[_CompletedScoreboardGame]:
    """Extract the scoreboard's completed games into a smaller comparable shape."""

    completed_games: list[_CompletedScoreboardGame] = []
    for event in espn_api_scoreboard_blob.get("events", []):
        competition = event["competitions"][0]
        status = competition["status"]["type"]
        if not status.get("completed", False):
            continue

        competitors = sorted(competition["competitors"], key=lambda competitor: competitor["order"])
        team_1_name = _scoreboard_team_name(competitors[0]["team"])
        team_2_name = _scoreboard_team_name(competitors[1]["team"])
        winner = next(competitor for competitor in competitors if competitor.get("winner"))
        winner_name = _scoreboard_team_name(winner["team"])

        completed_games.append(
            _CompletedScoreboardGame(
                event_id=str(event["id"]),
                team_1_name=team_1_name,
                team_2_name=team_2_name,
                winner_name=winner_name,
            )
        )

    return completed_games


def _scoreboard_team_name(team_blob: dict[str, Any]) -> str:
    """Choose the scoreboard team label that best matches the NCAA saved HTML."""

    return (
        team_blob.get("shortDisplayName")
        or team_blob.get("location")
        or team_blob.get("displayName")
        or str(team_blob["id"])
    )


def _find_reference_game_for_scoreboard_result(
    reference_bracket: Bracket,
    completed_game: _CompletedScoreboardGame,
) -> Game | None:
    """Find the best matching reference game for one completed scoreboard result."""

    for game in reference_bracket.games:
        if game.id == completed_game.event_id:
            return game

    scoreboard_teams = {
        _normalize_team_key(completed_game.team_1_name),
        _normalize_team_key(completed_game.team_2_name),
    }
    matches: list[Game] = []

    for game in reference_bracket.games:
        slot_name_1 = _resolve_slot_name(game.team_1)
        slot_name_2 = _resolve_slot_name(game.team_2)
        if slot_name_1 is None or slot_name_2 is None:
            continue

        reference_teams = {
            _normalize_team_key(slot_name_1),
            _normalize_team_key(slot_name_2),
        }
        if reference_teams == scoreboard_teams:
            matches.append(game)

    if len(matches) == 1:
        return matches[0]
    return None


def _resolve_slot_name(slot: GameSlot) -> str | None:
    """Resolve a game slot into the display name used for scoreboard comparisons."""

    if slot.team_id is None:
        return None
    return _resolve_team_identifier_to_name(slot.team_id)


@lru_cache(maxsize=1)
def _load_team_id_to_name() -> dict[str, str]:
    """Load the saved ESPN team lookup used by the scored bracket template."""

    if not TEAMS_PATH.exists():
        return {}
    teams = json.loads(TEAMS_PATH.read_text())
    return {str(team["id"]): team["display_name"] for team in teams}


def _resolve_team_identifier_to_name(team_identifier: str) -> str:
    """Resolve either an ESPN team ID or a synthetic name-based ID to a name."""

    team_id_to_name = _load_team_id_to_name()
    return team_id_to_name.get(team_identifier, team_identifier)


def _normalize_team_key(team_name: str) -> str:
    """Normalize team names so ESPN and NCAA labels compare cleanly."""

    normalized = team_name.lower()
    replacements = {
        "&": "and",
        ".": "",
        "'": "",
        "(": " ",
        ")": " ",
        ",": " ",
        "-": " ",
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)

    word_replacements = {
        "saint": "st",
        "state": "st",
        "university": "u",
        "ohio": "oh",
    }
    for old, new in word_replacements.items():
        normalized = re.sub(rf"\b{old}\b", new, normalized)

    normalized = re.sub(r"\s+", "", normalized)
    return normalized


def _traditional_points_for_round(round_id: int) -> int:
    """Return the traditional bracket point value for a given round."""

    return TRADITIONAL_SCORING_BY_ROUND[round_id]


def _calculate_max_possible_score(reference_bracket: Bracket, user_bracket: UserBracket) -> float:
    """Calculate the maximum score the user can still reach from the current state."""

    completed_winners = {
        game_key: _game_winner_compare_key(game)
        for game_key, game in _completed_reference_game_lookup(reference_bracket).items()
    }
    user_game_lookup = {game.game_key: game for game in user_bracket.bracket_picks.games}

    @lru_cache(maxsize=None)
    def is_pick_alive(game_key: str) -> bool:
        game = user_game_lookup[game_key]
        picked_winner_key = _normalize_team_key(game.picked_winner.team_name)
        actual_winner_key = completed_winners.get(game_key)

        if actual_winner_key is not None:
            return actual_winner_key == picked_winner_key

        child_game_keys = _child_game_keys_for(game_key)
        if child_game_keys is None:
            return True

        child_game_key_1, child_game_key_2 = child_game_keys
        slot_1_key = _normalize_team_key(game.picked_team_1.team_name)
        slot_2_key = _normalize_team_key(game.picked_team_2.team_name)

        if picked_winner_key == slot_1_key:
            return is_pick_alive(child_game_key_1)
        if picked_winner_key == slot_2_key:
            return is_pick_alive(child_game_key_2)
        return False

    max_possible_score = 0.0
    for game in user_bracket.bracket_picks.games:
        actual_winner_key = completed_winners.get(game.game_key)
        picked_winner_key = _normalize_team_key(game.picked_winner.team_name)
        game_points = _traditional_points_for_round(game.round_id)

        if actual_winner_key is not None:
            if actual_winner_key == picked_winner_key:
                max_possible_score += game_points
            continue

        if is_pick_alive(game.game_key):
            max_possible_score += game_points

    return max_possible_score


def _child_game_keys_for(game_key: str) -> tuple[str, str] | None:
    """Return the two child game keys that feed into the given game key."""

    if game_key == "championship-1":
        return ("final-four-1", "final-four-2")

    if game_key.startswith("final-four-"):
        matchup_index = int(game_key.rsplit("-", maxsplit=1)[1])
        if matchup_index == 1:
            return ("east-round-4-game-1", "south-round-4-game-1")
        return ("west-round-4-game-1", "midwest-round-4-game-1")

    match = re.fullmatch(r"(?P<region>[a-z]+)-round-(?P<round_id>\d+)-game-(?P<matchup>\d+)", game_key)
    if match is None:
        return None

    region = match.group("region")
    round_id = int(match.group("round_id"))
    matchup_index = int(match.group("matchup"))
    if round_id == 1:
        return None

    child_round_id = round_id - 1
    child_matchup_1 = (matchup_index * 2) - 1
    child_matchup_2 = matchup_index * 2
    return (
        f"{region}-round-{child_round_id}-game-{child_matchup_1}",
        f"{region}-round-{child_round_id}-game-{child_matchup_2}",
    )


def _build_saved_ncaa_reference_bracket() -> Bracket:
    """Build a bracket template from one saved NCAA bracket page."""

    source_page = next(path for path in sorted(BRACKETS_DIR.glob("*.html")) if path.name != "bracket-homepage.html")
    iframe_path = source_page.with_name(f"{source_page.stem}_files") / "a.html"
    parsed_page = _parse_saved_iframe_page(iframe_path)

    grouped: dict[int, list[Any]] = {round_id: [] for round_id in ROUND_NAMES}
    for matchup in parsed_page.matchups:
        grouped[matchup.round_id].append(matchup)

    games: list[Game] = []

    for region_index, region in enumerate(REGION_ORDER, start=1):
        for round_id in range(1, 5):
            regional_matchups = [matchup for matchup in grouped[round_id] if matchup.region == region]
            for matchup_index, matchup in enumerate(regional_matchups, start=1):
                games.append(
                    Game(
                        id=_regional_game_key(region, round_id, matchup_index),
                        round_id=round_id,
                        round_name=ROUND_NAMES[round_id],
                        bracket_location=matchup_index,
                        region_id=region_index,
                        region_name=region,
                        status=GameStatus.FUTURE,
                        team_1=GameSlot(team_id=_actual_slot_name(matchup.slot_1)),
                        team_2=GameSlot(team_id=_actual_slot_name(matchup.slot_2)),
                    )
                )

    semifinal_matchups = grouped[5]
    for matchup_index, matchup in enumerate(semifinal_matchups, start=1):
        games.append(
            Game(
                id=f"final-four-{matchup_index}",
                round_id=5,
                round_name=ROUND_NAMES[5],
                bracket_location=matchup_index,
                region_id=None,
                region_name=None,
                status=GameStatus.FUTURE,
                team_1=GameSlot(team_id=_actual_slot_name(matchup.slot_1)),
                team_2=GameSlot(team_id=_actual_slot_name(matchup.slot_2)),
            )
        )

    championship_matchup = grouped[6][0]
    games.append(
        Game(
            id="championship-1",
            round_id=6,
            round_name=ROUND_NAMES[6],
            bracket_location=1,
            region_id=None,
            region_name=None,
            status=GameStatus.FUTURE,
            team_1=GameSlot(team_id=_actual_slot_name(championship_matchup.slot_1)),
            team_2=GameSlot(team_id=_actual_slot_name(championship_matchup.slot_2)),
        )
    )

    return Bracket(
        id="saved-ncaa-reference",
        name="Saved NCAA Bracket Reference",
        season="2025-26",
        league="NCAAM",
        active_round=1,
        regions=[
            BracketRegion(id=index, name=region, slug=region.lower())
            for index, region in enumerate(REGION_ORDER, start=1)
        ],
        rounds=[
            BracketRound(id=round_id, name=round_name, num_games=len(grouped[round_id]))
            for round_id, round_name in ROUND_NAMES.items()
        ],
        games=games,
    )


def _actual_slot_name(slot: Any) -> str | None:
    """Return the actual team occupying a saved NCAA matchup slot, if known."""

    if slot.actual is None:
        return None
    return slot.actual.name


def _regional_game_key(region: str, round_id: int, matchup_index: int) -> str:
    """Build the standardized game key used by the user bracket JSON exports."""

    return f"{region.lower()}-round-{round_id}-game-{matchup_index}"
