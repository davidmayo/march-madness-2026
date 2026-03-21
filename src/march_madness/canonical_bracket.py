"""Define the canonical 2026 NCAA men's tournament bracket used by this repo.

This module is the single source of truth for the saved NCAA bracket pages and
the ESPN scoreboard snapshot:
- Every round-1 slot is assigned to an explicit ESPN team ID and seed.
- Every later game is wired to the exact child games that feed into it.
- ESPN event IDs are attached only where the current local data identifies them
  unambiguously. Remaining games can still be mapped exactly from participant
  team IDs once upstream winners are known.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from march_madness.scrape import Bracket
from march_madness.scrape import BracketRegion
from march_madness.scrape import BracketRound
from march_madness.scrape import Game
from march_madness.scrape import GameSlot
from march_madness.scrape import GameStatus


ROUND_NAMES: dict[int, str] = {
    1: "1st Round",
    2: "2nd Round",
    3: "Sweet 16",
    4: "Elite 8",
    5: "Final Four",
    6: "Championship",
}
REGION_ORDER: tuple[str, ...] = ("EAST", "SOUTH", "WEST", "MIDWEST")
REGION_TO_FINAL_FOUR_SLOT: dict[str, tuple[int, int]] = {
    "EAST": (1, 1),
    "SOUTH": (1, 2),
    "WEST": (2, 1),
    "MIDWEST": (2, 2),
}


@dataclass(frozen=True)
class CanonicalTeam:
    """Represent one fixed team placement in the canonical bracket."""

    team_id: str
    ncaa_name: str
    seed: str


@dataclass(frozen=True)
class FirstRoundGame:
    """Represent one explicit round-1 game in the canonical bracket."""

    game_key: str
    espn_event_id: str
    location: str
    team_1: CanonicalTeam
    team_2: CanonicalTeam


def _team(team_id: str, ncaa_name: str, seed: str) -> CanonicalTeam:
    """Create a small canonical-team record."""

    return CanonicalTeam(team_id=team_id, ncaa_name=ncaa_name, seed=seed)


FIRST_ROUND_GAMES: tuple[FirstRoundGame, ...] = (
    FirstRoundGame(
        game_key="east-round-1-game-1",
        espn_event_id="401856478",
        location="Bon Secours Wellness Arena",
        team_1=_team("150", "Duke", "1"),
        team_2=_team("2561", "Siena", "16"),
    ),
    FirstRoundGame(
        game_key="east-round-1-game-2",
        espn_event_id="401856479",
        location="Bon Secours Wellness Arena",
        team_1=_team("194", "Ohio St.", "8"),
        team_2=_team("2628", "TCU", "9"),
    ),
    FirstRoundGame(
        game_key="east-round-1-game-3",
        espn_event_id="401856494",
        location="Viejas Arena",
        team_1=_team("2599", "St. John's", "5"),
        team_2=_team("2460", "Northern Iowa", "12"),
    ),
    FirstRoundGame(
        game_key="east-round-1-game-4",
        espn_event_id="401856495",
        location="Viejas Arena",
        team_1=_team("2305", "Kansas", "4"),
        team_2=_team("2856", "Cal Baptist", "13"),
    ),
    FirstRoundGame(
        game_key="east-round-1-game-5",
        espn_event_id="401856482",
        location="KeyBank Center",
        team_1=_team("97", "Louisville", "6"),
        team_2=_team("58", "South Florida", "11"),
    ),
    FirstRoundGame(
        game_key="east-round-1-game-6",
        espn_event_id="401856483",
        location="KeyBank Center",
        team_1=_team("127", "Michigan St.", "3"),
        team_2=_team("2449", "North Dakota St.", "14"),
    ),
    FirstRoundGame(
        game_key="east-round-1-game-7",
        espn_event_id="401856496",
        location="Xfinity Mobile Arena",
        team_1=_team("26", "UCLA", "7"),
        team_2=_team("2116", "UCF", "10"),
    ),
    FirstRoundGame(
        game_key="east-round-1-game-8",
        espn_event_id="401856497",
        location="Xfinity Mobile Arena",
        team_1=_team("41", "UConn", "2"),
        team_2=_team("231", "Furman", "15"),
    ),
    FirstRoundGame(
        game_key="south-round-1-game-1",
        espn_event_id="401856523",
        location="Benchmark International Arena",
        team_1=_team("57", "Florida", "1"),
        team_2=_team("2504", "Prairie View A&M", "16"),
    ),
    FirstRoundGame(
        game_key="south-round-1-game-2",
        espn_event_id="401856522",
        location="Benchmark International Arena",
        team_1=_team("228", "Clemson", "8"),
        team_2=_team("2294", "Iowa", "9"),
    ),
    FirstRoundGame(
        game_key="south-round-1-game-3",
        espn_event_id="401856488",
        location="Paycom Center",
        team_1=_team("238", "Vanderbilt", "5"),
        team_2=_team("2377", "McNeese", "12"),
    ),
    FirstRoundGame(
        game_key="south-round-1-game-4",
        espn_event_id="401856489",
        location="Paycom Center",
        team_1=_team("158", "Nebraska", "4"),
        team_2=_team("2653", "Troy", "13"),
    ),
    FirstRoundGame(
        game_key="south-round-1-game-5",
        espn_event_id="401856490",
        location="Bon Secours Wellness Arena",
        team_1=_team("153", "North Carolina", "6"),
        team_2=_team("2670", "VCU", "11"),
    ),
    FirstRoundGame(
        game_key="south-round-1-game-6",
        espn_event_id="401856491",
        location="Bon Secours Wellness Arena",
        team_1=_team("356", "Illinois", "3"),
        team_2=_team("219", "Penn", "14"),
    ),
    FirstRoundGame(
        game_key="south-round-1-game-7",
        espn_event_id="401856492",
        location="Paycom Center",
        team_1=_team("2608", "Saint Mary's", "7"),
        team_2=_team("245", "Texas A&M", "10"),
    ),
    FirstRoundGame(
        game_key="south-round-1-game-8",
        espn_event_id="401856493",
        location="Paycom Center",
        team_1=_team("248", "Houston", "2"),
        team_2=_team("70", "Idaho", "15"),
    ),
    FirstRoundGame(
        game_key="west-round-1-game-1",
        espn_event_id="401856529",
        location="Viejas Arena",
        team_1=_team("12", "Arizona", "1"),
        team_2=_team("112358", "Long Island", "16"),
    ),
    FirstRoundGame(
        game_key="west-round-1-game-2",
        espn_event_id="401856528",
        location="Viejas Arena",
        team_1=_team("222", "Villanova", "8"),
        team_2=_team("328", "Utah St.", "9"),
    ),
    FirstRoundGame(
        game_key="west-round-1-game-3",
        espn_event_id="401856480",
        location="Moda Center",
        team_1=_team("275", "Wisconsin", "5"),
        team_2=_team("2272", "High Point", "12"),
    ),
    FirstRoundGame(
        game_key="west-round-1-game-4",
        espn_event_id="401856481",
        location="Moda Center",
        team_1=_team("8", "Arkansas", "4"),
        team_2=_team("62", "Hawaii", "13"),
    ),
    FirstRoundGame(
        game_key="west-round-1-game-5",
        espn_event_id="401856484",
        location="Moda Center",
        team_1=_team("252", "BYU", "6"),
        team_2=_team("251", "Texas", "11"),
    ),
    FirstRoundGame(
        game_key="west-round-1-game-6",
        espn_event_id="401856485",
        location="Moda Center",
        team_1=_team("2250", "Gonzaga", "3"),
        team_2=_team("338", "Kennesaw St.", "14"),
    ),
    FirstRoundGame(
        game_key="west-round-1-game-7",
        espn_event_id="401856518",
        location="Enterprise Center",
        team_1=_team("2390", "Miami (FL)", "7"),
        team_2=_team("142", "Missouri", "10"),
    ),
    FirstRoundGame(
        game_key="west-round-1-game-8",
        espn_event_id="401856519",
        location="Enterprise Center",
        team_1=_team("2509", "Purdue", "2"),
        team_2=_team("2511", "Queens (N.C.)", "15"),
    ),
    FirstRoundGame(
        game_key="midwest-round-1-game-1",
        espn_event_id="401856486",
        location="KeyBank Center",
        team_1=_team("130", "Michigan", "1"),
        team_2=_team("47", "Howard", "16"),
    ),
    FirstRoundGame(
        game_key="midwest-round-1-game-2",
        espn_event_id="401856487",
        location="KeyBank Center",
        team_1=_team("61", "Georgia", "8"),
        team_2=_team("139", "Saint Louis", "9"),
    ),
    FirstRoundGame(
        game_key="midwest-round-1-game-3",
        espn_event_id="401856520",
        location="Benchmark International Arena",
        team_1=_team("2641", "Texas Tech", "5"),
        team_2=_team("2006", "Akron", "12"),
    ),
    FirstRoundGame(
        game_key="midwest-round-1-game-4",
        espn_event_id="401856521",
        location="Benchmark International Arena",
        team_1=_team("333", "Alabama", "4"),
        team_2=_team("2275", "Hofstra", "13"),
    ),
    FirstRoundGame(
        game_key="midwest-round-1-game-5",
        espn_event_id="401856527",
        location="Xfinity Mobile Arena",
        team_1=_team("2633", "Tennessee", "6"),
        team_2=_team("193", "Miami (Ohio)", "11"),
    ),
    FirstRoundGame(
        game_key="midwest-round-1-game-6",
        espn_event_id="401856526",
        location="Xfinity Mobile Arena",
        team_1=_team("258", "Virginia", "3"),
        team_2=_team("2750", "Wright St.", "14"),
    ),
    FirstRoundGame(
        game_key="midwest-round-1-game-7",
        espn_event_id="401856525",
        location="Enterprise Center",
        team_1=_team("96", "Kentucky", "7"),
        team_2=_team("2541", "Santa Clara", "10"),
    ),
    FirstRoundGame(
        game_key="midwest-round-1-game-8",
        espn_event_id="401856524",
        location="Enterprise Center",
        team_1=_team("66", "Iowa St.", "2"),
        team_2=_team("2634", "Tennessee St.", "15"),
    ),
)

# These event IDs are explicit because the current scoreboard identifies the
# exact round-2 game for each bracket slot through site + participants.
KNOWN_ESPN_EVENT_IDS_BY_GAME_KEY: dict[str, str] = {
    **{game.game_key: game.espn_event_id for game in FIRST_ROUND_GAMES},
    "east-round-2-game-1": "401856530",
    "east-round-2-game-2": "401856558",
    "east-round-2-game-3": "401856531",
    "east-round-2-game-4": "401856559",
    "south-round-2-game-1": "401856563",
    "south-round-2-game-2": "401856534",
    "south-round-2-game-3": "401856533",
    "south-round-2-game-4": "401856535",
    "west-round-2-game-1": "401856565",
    "west-round-2-game-2": "401856536",
    "west-round-2-game-3": "401856537",
    "west-round-2-game-4": "401856564",
    "midwest-round-2-game-1": "401856532",
    "midwest-round-2-game-2": "401856560",
    "midwest-round-2-game-3": "401856562",
    "midwest-round-2-game-4": "401856561",
    # Elite Eight sites each host only one regional final, so these IDs are
    # also unambiguous in the current local scoreboard snapshot.
    "west-round-4-game-1": "401856575",
    "south-round-4-game-1": "401856574",
    "east-round-4-game-1": "401856577",
    "midwest-round-4-game-1": "401856576",
    "championship-1": "401856600",
}

# Sweet 16 and Final Four sites currently expose paired TBD-vs-TBD events with
# no participant-level distinction between the two sibling games. The bracket
# graph below still lets us map those games exactly once upstream winners are
# known, so event IDs are intentionally omitted here instead of guessed.
GAME_LOCATION_BY_KEY: dict[str, str] = {
    **{game.game_key: game.location for game in FIRST_ROUND_GAMES},
    "east-round-2-game-1": "Bon Secours Wellness Arena",
    "east-round-2-game-2": "Viejas Arena",
    "east-round-2-game-3": "KeyBank Center",
    "east-round-2-game-4": "Xfinity Mobile Arena",
    "south-round-2-game-1": "Benchmark International Arena",
    "south-round-2-game-2": "Paycom Center",
    "south-round-2-game-3": "Bon Secours Wellness Arena",
    "south-round-2-game-4": "Paycom Center",
    "west-round-2-game-1": "Viejas Arena",
    "west-round-2-game-2": "Moda Center",
    "west-round-2-game-3": "Moda Center",
    "west-round-2-game-4": "Enterprise Center",
    "midwest-round-2-game-1": "KeyBank Center",
    "midwest-round-2-game-2": "Benchmark International Arena",
    "midwest-round-2-game-3": "Xfinity Mobile Arena",
    "midwest-round-2-game-4": "Enterprise Center",
    "east-round-3-game-1": "Capital One Arena",
    "east-round-3-game-2": "Capital One Arena",
    "south-round-3-game-1": "Toyota Center (Houston)",
    "south-round-3-game-2": "Toyota Center (Houston)",
    "west-round-3-game-1": "SAP Center at San Jose",
    "west-round-3-game-2": "SAP Center at San Jose",
    "midwest-round-3-game-1": "United Center",
    "midwest-round-3-game-2": "United Center",
    "east-round-4-game-1": "Capital One Arena",
    "south-round-4-game-1": "Toyota Center (Houston)",
    "west-round-4-game-1": "SAP Center at San Jose",
    "midwest-round-4-game-1": "United Center",
    "final-four-1": "Lucas Oil Stadium",
    "final-four-2": "Lucas Oil Stadium",
    "championship-1": "Lucas Oil Stadium",
}


def build_game_key(region: str, round_id: int, matchup_index: int) -> str:
    """Build the canonical game key for one regional game."""

    return f"{region.lower()}-round-{round_id}-game-{matchup_index}"


def child_game_keys_for(game_key: str) -> tuple[str, str] | None:
    """Return the two child games that feed into the given canonical game."""

    if game_key == "championship-1":
        return ("final-four-1", "final-four-2")

    if game_key.startswith("final-four-"):
        matchup_index = int(game_key.rsplit("-", maxsplit=1)[1])
        if matchup_index == 1:
            return ("east-round-4-game-1", "south-round-4-game-1")
        return ("west-round-4-game-1", "midwest-round-4-game-1")

    prefix, matchup_index_text = game_key.rsplit("-game-", maxsplit=1)
    region, round_id_text = prefix.rsplit("-round-", maxsplit=1)
    round_id = int(round_id_text)
    matchup_index = int(matchup_index_text)
    if round_id == 1:
        return None

    child_round_id = round_id - 1
    child_matchup_1 = (matchup_index * 2) - 1
    child_matchup_2 = matchup_index * 2
    return (
        f"{region}-round-{child_round_id}-game-{child_matchup_1}",
        f"{region}-round-{child_round_id}-game-{child_matchup_2}",
    )


@lru_cache(maxsize=1)
def canonical_team_name_by_id() -> dict[str, str]:
    """Return the NCAA display-name mapping for the canonical 64-team field."""

    mapping: dict[str, str] = {}
    for game in FIRST_ROUND_GAMES:
        mapping[game.team_1.team_id] = game.team_1.ncaa_name
        mapping[game.team_2.team_id] = game.team_2.ncaa_name
    return mapping


@lru_cache(maxsize=1)
def canonical_team_seed_by_id() -> dict[str, str]:
    """Return the original seed mapping for the canonical 64-team field."""

    mapping: dict[str, str] = {}
    for game in FIRST_ROUND_GAMES:
        mapping[game.team_1.team_id] = game.team_1.seed
        mapping[game.team_2.team_id] = game.team_2.seed
    return mapping


@lru_cache(maxsize=1)
def canonical_first_round_game_lookup() -> dict[str, FirstRoundGame]:
    """Return the round-1 game definitions keyed by canonical game key."""

    return {game.game_key: game for game in FIRST_ROUND_GAMES}


@lru_cache(maxsize=1)
def build_canonical_bracket() -> Bracket:
    """Build the canonical 2026 NCAA bracket as a typed ``Bracket`` instance."""

    games: list[Game] = []
    region_id_by_name = {region: index for index, region in enumerate(REGION_ORDER, start=1)}

    for game in FIRST_ROUND_GAMES:
        prefix, matchup_index_text = game.game_key.rsplit("-game-", maxsplit=1)
        region_key, _round_text = prefix.rsplit("-round-", maxsplit=1)
        region_name = region_key.upper()
        matchup_index = int(matchup_index_text)
        games.append(
            Game(
                id=game.game_key,
                espn_event_id=game.espn_event_id,
                round_id=1,
                round_name=ROUND_NAMES[1],
                bracket_location=matchup_index,
                region_id=region_id_by_name[region_name],
                region_name=region_name,
                location=game.location,
                status=GameStatus.FUTURE,
                team_1=GameSlot(team_id=game.team_1.team_id, seed=game.team_1.seed),
                team_2=GameSlot(team_id=game.team_2.team_id, seed=game.team_2.seed),
                feed_in_game_ids=[],
                feeds_to_game_id=None,
            )
        )

    round_game_counts = {2: 4, 3: 2, 4: 1}
    for region_name in REGION_ORDER:
        region_id = region_id_by_name[region_name]
        for round_id, game_count in round_game_counts.items():
            for matchup_index in range(1, game_count + 1):
                game_key = build_game_key(region_name, round_id, matchup_index)
                child_1, child_2 = child_game_keys_for(game_key) or ("", "")
                games.append(
                    Game(
                        id=game_key,
                        espn_event_id=KNOWN_ESPN_EVENT_IDS_BY_GAME_KEY.get(game_key),
                        round_id=round_id,
                        round_name=ROUND_NAMES[round_id],
                        bracket_location=matchup_index,
                        region_id=region_id,
                        region_name=region_name,
                        location=GAME_LOCATION_BY_KEY.get(game_key),
                        status=GameStatus.FUTURE,
                        team_1=GameSlot(from_game_id=child_1),
                        team_2=GameSlot(from_game_id=child_2),
                        feed_in_game_ids=[child_1, child_2],
                        feeds_to_game_id=None,
                    )
                )

    final_four_children = {
        "final-four-1": ("east-round-4-game-1", "south-round-4-game-1"),
        "final-four-2": ("west-round-4-game-1", "midwest-round-4-game-1"),
    }
    for matchup_index in (1, 2):
        game_key = f"final-four-{matchup_index}"
        child_1, child_2 = final_four_children[game_key]
        games.append(
            Game(
                id=game_key,
                espn_event_id=KNOWN_ESPN_EVENT_IDS_BY_GAME_KEY.get(game_key),
                round_id=5,
                round_name=ROUND_NAMES[5],
                bracket_location=matchup_index,
                region_id=None,
                region_name=None,
                location=GAME_LOCATION_BY_KEY.get(game_key),
                status=GameStatus.FUTURE,
                team_1=GameSlot(from_game_id=child_1),
                team_2=GameSlot(from_game_id=child_2),
                feed_in_game_ids=[child_1, child_2],
                feeds_to_game_id="championship-1",
            )
        )

    games.append(
        Game(
            id="championship-1",
            espn_event_id=KNOWN_ESPN_EVENT_IDS_BY_GAME_KEY.get("championship-1"),
            round_id=6,
            round_name=ROUND_NAMES[6],
            bracket_location=1,
            region_id=None,
            region_name=None,
            location=GAME_LOCATION_BY_KEY.get("championship-1"),
            status=GameStatus.FUTURE,
            team_1=GameSlot(from_game_id="final-four-1"),
            team_2=GameSlot(from_game_id="final-four-2"),
            feed_in_game_ids=["final-four-1", "final-four-2"],
            feeds_to_game_id=None,
        )
    )

    game_lookup = {game.id: game for game in games}
    for game in games:
        for child_game_id in game.feed_in_game_ids:
            if not child_game_id:
                continue
            game_lookup[child_game_id].feeds_to_game_id = game.id

    return Bracket(
        id="2026-ncaa-men-canonical",
        name="2026 NCAA Men's Tournament Canonical Bracket",
        season="2025-26",
        league="NCAAM",
        active_round=1,
        regions=[
            BracketRegion(id=index, name=region, slug=region.lower())
            for index, region in enumerate(REGION_ORDER, start=1)
        ],
        rounds=[
            BracketRound(id=round_id, name=round_name, num_games={1: 32, 2: 16, 3: 8, 4: 4, 5: 2, 6: 1}[round_id])
            for round_id, round_name in ROUND_NAMES.items()
        ],
        games=games,
    )
