"""Generate typed tournament structs from the saved ESPN and KenPom samples.

This module keeps the project data generation in one place:
- ESPN team API data becomes ``Team`` objects.
- ESPN bracket page state becomes a pre-1st-round ``Bracket``.
- KenPom ratings are matched back to ESPN team IDs and written as dated snapshots.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from enum import StrEnum
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


ROOT = Path(__file__).resolve().parents[2]

ESPN_TEAMS_PATH = ROOT / "data" / "espn" / "api" / "teams.json"
ESPN_SCOREBOARD_PATH = ROOT / "data" / "espn" / "api" / "scoreboard.json"
ESPN_BRACKET_PATH = ROOT / "data" / "espn" / "webpages" / "bracket.html"
KENPOM_INDEX_PATH = ROOT / "data" / "kenpom" / "webpages" / "index.html"
STRUCTS_DIR = ROOT / "data" / "structs"
KENPOM_SNAPSHOTS_DIR = STRUCTS_DIR / "kenpoms"

BRACKET_OUTPUT_PATH = STRUCTS_DIR / "2026-starting-bracket.json"
TEAMS_OUTPUT_PATH = STRUCTS_DIR / "2026-teams.json"
KENPOM_OUTPUT_PATH = KENPOM_SNAPSHOTS_DIR / "2026-03-21T01-00-00Z.json"
LEGACY_KENPOM_OUTPUT_PATH = STRUCTS_DIR / "2026-kenpoms.json"


class GameStatus(StrEnum):
    """Represent the allowed lifecycle states for a game."""

    FINISHED = "finished"
    FUTURE = "future"
    SIMULATED = "simulated"


class Team(BaseModel):
    """Represent the normalized team data used by the project."""

    model_config = ConfigDict(extra="forbid")

    id: str
    uid: str | None = None
    slug: str | None = None
    abbreviation: str | None = None
    display_name: str
    short_display_name: str | None = None
    school_name: str | None = None
    location: str | None = None
    mascot: str | None = None
    kenpom_name: str | None = None
    color: str | None = None
    alternate_color: str | None = None
    logo: str | None = None
    logo_dark: str | None = None
    links: list[str] = Field(default_factory=list)
    is_active: bool | None = None


class GameSlot(BaseModel):
    """Represent one side of a bracket game."""

    model_config = ConfigDict(extra="forbid")

    team_id: str | None = None
    seed: str | None = None
    from_game_id: str | None = None


class BracketRegion(BaseModel):
    """Represent one tournament region."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    slug: str | None = None


class BracketRound(BaseModel):
    """Represent round metadata from the ESPN bracket payload."""

    model_config = ConfigDict(extra="forbid")

    id: int
    name: str
    num_games: int
    start_at: datetime | None = None
    end_at: datetime | None = None


class Game(BaseModel):
    """Represent a bracket game and its graph relationships."""

    model_config = ConfigDict(extra="forbid")

    id: str
    espn_event_id: str | None = None
    round_id: int
    round_name: str
    bracket_location: int
    region_id: int | None = None
    region_name: str | None = None
    scheduled_at: datetime | None = None
    location: str | None = None
    status: GameStatus
    team_1: GameSlot
    team_2: GameSlot
    winner_team_id: str | None = None
    feed_in_game_ids: list[str] = Field(default_factory=list)
    feeds_to_game_id: str | None = None


class Bracket(BaseModel):
    """Represent the full tournament bracket."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    season: str
    league: str
    active_round: int
    regions: list[BracketRegion]
    rounds: list[BracketRound]
    games: list[Game]


class KenPomRating(BaseModel):
    """Represent a dated KenPom snapshot entry."""

    model_config = ConfigDict(extra="forbid")

    team_id: str
    net_rating: float


@dataclass
class TeamAccumulator:
    """Accumulate team fields from multiple ESPN sources with precedence."""

    team_id: str
    data: dict[str, Any] = field(default_factory=dict)
    priorities: dict[str, int] = field(default_factory=dict)
    aliases: set[str] = field(default_factory=set)

    def update_field(self, key: str, value: Any, priority: int) -> None:
        """Store a field when the new source is better than the current one."""

        if value in (None, "", []):
            return
        current_priority = self.priorities.get(key, -1)
        if priority >= current_priority:
            self.data[key] = value
            self.priorities[key] = priority

    def add_aliases(self, *values: str | None) -> None:
        """Register raw names that can later be used for KenPom matching."""

        for value in values:
            if value:
                self.aliases.add(value)


@dataclass
class KenPomCell:
    """Track both the full cell text and the linked text inside a KenPom cell."""

    text: str = ""
    anchor_text: str = ""


@dataclass
class KenPomRow:
    """Represent the subset of KenPom data that this project cares about."""

    team_name: str
    net_rating: float


class KenPomTableParser(HTMLParser):
    """Extract team names and NetRtg values from the saved KenPom index table."""

    def __init__(self) -> None:
        """Initialize the parser state."""

        super().__init__()
        self.in_table = False
        self.in_tbody = False
        self.in_row = False
        self.in_cell = False
        self.in_anchor = False
        self.current_text: list[str] = []
        self.current_anchor_text: list[str] = []
        self.current_row: list[KenPomCell] = []
        self.rows: list[KenPomRow] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track entry into the ratings table and its cells."""

        attrs_map = dict(attrs)
        if tag == "table" and attrs_map.get("id") == "ratings-table":
            self.in_table = True
            return
        if not self.in_table:
            return
        if tag == "tbody":
            self.in_tbody = True
        elif self.in_tbody and tag == "tr":
            self.in_row = True
            self.current_row = []
        elif self.in_row and tag == "td":
            self.in_cell = True
            self.current_text = []
            self.current_anchor_text = []
        elif self.in_cell and tag == "a":
            self.in_anchor = True

    def handle_endtag(self, tag: str) -> None:
        """Track exit from the ratings table and emit parsed rows."""

        if tag == "table" and self.in_table:
            self.in_table = False
            return
        if not self.in_table:
            return
        if tag == "tbody" and self.in_tbody:
            self.in_tbody = False
        elif tag == "a" and self.in_anchor:
            self.in_anchor = False
        elif tag == "td" and self.in_cell:
            self.current_row.append(
                KenPomCell(
                    text=_collapse_whitespace("".join(self.current_text)),
                    anchor_text=_collapse_whitespace("".join(self.current_anchor_text)),
                )
            )
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            self.in_row = False
            if len(self.current_row) >= 5:
                team_name = self.current_row[1].anchor_text or self.current_row[1].text
                team_name = re.sub(r"\s+\d+$", "", team_name)
                net_rating = float(self.current_row[4].text.replace("+", ""))
                self.rows.append(KenPomRow(team_name=team_name, net_rating=net_rating))

    def handle_data(self, data: str) -> None:
        """Collect text from the current cell."""

        if not self.in_cell:
            return
        self.current_text.append(data)
        if self.in_anchor:
            self.current_anchor_text.append(data)


TEAM_ALIAS_OVERRIDES: dict[str, tuple[str, ...]] = {
    "41": ("Connecticut", "UConn"),
    "145": ("Mississippi", "Ole Miss"),
    "252": ("BYU", "Brigham Young"),
    "2437": ("Nebraska Omaha",),
    "344": ("Saint Mary's", "St. Mary's"),
    "2599": ("St. John's", "Saint John's", "St Johns"),
    "350": ("UNC Wilmington", "UNCW"),
}


def _collapse_whitespace(value: str) -> str:
    """Collapse repeated whitespace into single spaces."""

    return " ".join(value.split())


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp into a UTC datetime."""

    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _sort_key(value: str) -> tuple[int, str]:
    """Sort numeric IDs numerically while keeping non-numeric IDs stable."""

    if value.isdigit():
        return (0, f"{int(value):08d}")
    return (1, value)


def _normalize_name(value: str) -> str:
    """Normalize names so ESPN and KenPom strings are comparable."""

    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = normalized.lower()
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("a&m", "am")
    normalized = re.sub(r"[.'’`]", "", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return _collapse_whitespace(normalized)


def _name_variants(value: str) -> set[str]:
    """Generate a small family of equivalent team-name variants."""

    base = _normalize_name(value)
    if not base:
        return set()

    variants = {base, base.replace(" ", "")}
    swaps = [
        (" saint ", " st "),
        (" st ", " saint "),
        (" state ", " st "),
        (" st ", " state "),
        (" and ", " "),
        (" university ", " "),
        (" college ", " "),
    ]

    queue = {f" {base} "}
    seen = set(queue)
    while queue:
        current = queue.pop()
        stripped = _collapse_whitespace(current.strip())
        variants.add(stripped)
        variants.add(stripped.replace(" ", ""))
        for before, after in swaps:
            swapped = current.replace(before, after)
            if swapped not in seen:
                seen.add(swapped)
                queue.add(swapped)

    cleaned_variants: set[str] = set()
    for variant in variants:
        variant = _collapse_whitespace(variant)
        if variant:
            cleaned_variants.add(variant)
    return cleaned_variants


def _extract_espnfitt_state(path: Path) -> dict[str, Any]:
    """Extract ESPN's inline JSON state object from the saved bracket page."""

    text = path.read_text()
    marker = "window['__espnfitt__']="
    start = text.index(marker) + len(marker)
    end = text.index(";</script>", start)
    return json.loads(text[start:end])


def _iter_api_teams(path: Path) -> list[dict[str, Any]]:
    """Read team records from the saved ESPN teams API payload."""

    payload = json.loads(path.read_text())
    sports = payload.get("sports", [])
    if not sports:
        return []
    leagues = sports[0].get("leagues", [])
    if not leagues:
        return []
    return [wrapper["team"] for wrapper in leagues[0].get("teams", []) if wrapper.get("team")]


def _iter_scoreboard_teams(path: Path) -> list[dict[str, Any]]:
    """Read team records embedded in the saved ESPN scoreboard payload."""

    payload = json.loads(path.read_text())
    teams: list[dict[str, Any]] = []
    for event in payload.get("events", []):
        for competition in event.get("competitions", []):
            for competitor in competition.get("competitors", []):
                team = competitor.get("team")
                if team and team.get("id"):
                    teams.append(team)
    return teams


def _iter_bracket_competitors(bracket_state: dict[str, Any]) -> list[dict[str, Any]]:
    """Read competitor records from the saved ESPN bracket payload."""

    teams: list[dict[str, Any]] = []
    for matchup in bracket_state["page"]["content"]["bracket"]["matchups"]:
        for key in ("competitorOne", "competitorTwo"):
            competitor = matchup.get(key)
            if competitor and competitor.get("id"):
                teams.append(competitor)
    return teams


def _choose_logo(logos: list[dict[str, Any]], rel_value: str) -> str | None:
    """Pick the logo URL with the requested ESPN relation tag."""

    for logo in logos:
        rel = logo.get("rel") or []
        if rel_value in rel:
            return logo.get("href")
    return None


def _merge_team_data() -> tuple[list[Team], dict[str, set[str]]]:
    """Merge ESPN team sources into one normalized list plus a name lookup map."""

    bracket_state = _extract_espnfitt_state(ESPN_BRACKET_PATH)
    accumulators: dict[str, TeamAccumulator] = {}

    def get_accumulator(team_id: str) -> TeamAccumulator:
        """Create or fetch the accumulator for one ESPN team ID."""

        accumulator = accumulators.get(team_id)
        if accumulator is None:
            accumulator = TeamAccumulator(team_id=team_id)
            accumulators[team_id] = accumulator
        return accumulator

    # Prefer the dedicated teams API data when it exists.
    for raw_team in _iter_api_teams(ESPN_TEAMS_PATH):
        team_id = raw_team["id"]
        accumulator = get_accumulator(team_id)
        priority = 3
        logos = raw_team.get("logos") or []
        links = [link.get("href") for link in raw_team.get("links", []) if link.get("href")]
        accumulator.update_field("uid", raw_team.get("uid"), priority)
        accumulator.update_field("slug", raw_team.get("slug"), priority)
        accumulator.update_field("abbreviation", raw_team.get("abbreviation"), priority)
        accumulator.update_field("display_name", raw_team.get("displayName"), priority)
        accumulator.update_field("short_display_name", raw_team.get("shortDisplayName"), priority)
        accumulator.update_field("school_name", raw_team.get("nickname"), priority)
        accumulator.update_field("location", raw_team.get("location"), priority)
        accumulator.update_field("mascot", raw_team.get("name"), priority)
        accumulator.update_field("color", raw_team.get("color"), priority)
        accumulator.update_field("alternate_color", raw_team.get("alternateColor"), priority)
        accumulator.update_field("logo", _choose_logo(logos, "default"), priority)
        accumulator.update_field("logo_dark", _choose_logo(logos, "dark"), priority)
        accumulator.update_field("links", links, priority)
        accumulator.update_field("is_active", raw_team.get("isActive"), priority)
        accumulator.add_aliases(
            raw_team.get("displayName"),
            raw_team.get("shortDisplayName"),
            raw_team.get("nickname"),
            raw_team.get("location"),
            raw_team.get("name"),
        )

    # Scoreboard team objects are less complete, but they help fill gaps.
    for raw_team in _iter_scoreboard_teams(ESPN_SCOREBOARD_PATH):
        team_id = raw_team["id"]
        accumulator = get_accumulator(team_id)
        priority = 2
        accumulator.update_field("uid", raw_team.get("uid"), priority)
        accumulator.update_field("abbreviation", raw_team.get("abbreviation"), priority)
        accumulator.update_field("display_name", raw_team.get("displayName"), priority)
        accumulator.update_field("short_display_name", raw_team.get("shortDisplayName"), priority)
        accumulator.update_field("school_name", raw_team.get("shortDisplayName"), priority)
        accumulator.update_field("location", raw_team.get("location"), priority)
        accumulator.update_field("mascot", raw_team.get("name"), priority)
        accumulator.update_field("color", raw_team.get("color"), priority)
        accumulator.update_field("alternate_color", raw_team.get("alternateColor"), priority)
        accumulator.update_field("logo", raw_team.get("logo"), priority)
        accumulator.add_aliases(
            raw_team.get("displayName"),
            raw_team.get("shortDisplayName"),
            raw_team.get("location"),
            raw_team.get("name"),
        )

    # The bracket page gives us tournament teams even when the API sample is incomplete.
    for raw_team in _iter_bracket_competitors(bracket_state):
        team_id = raw_team["id"]
        accumulator = get_accumulator(team_id)
        priority = 1
        short_display_name = raw_team.get("name")
        location = raw_team.get("location")
        display_name = location or short_display_name or raw_team.get("abbreviation")
        accumulator.update_field("abbreviation", raw_team.get("abbreviation"), priority)
        accumulator.update_field("display_name", display_name, priority)
        accumulator.update_field("short_display_name", short_display_name, priority)
        accumulator.update_field("school_name", short_display_name, priority)
        accumulator.update_field("location", location, priority)
        accumulator.update_field("color", raw_team.get("color"), priority)
        accumulator.update_field("alternate_color", raw_team.get("alternateColor"), priority)
        accumulator.update_field("logo", raw_team.get("logo"), priority)
        accumulator.update_field("logo_dark", raw_team.get("logoDark"), priority)
        accumulator.add_aliases(
            short_display_name,
            location,
            raw_team.get("abbreviation"),
        )

    teams: list[Team] = []
    alias_map: dict[str, set[str]] = defaultdict(set)
    for team_id, accumulator in sorted(accumulators.items(), key=lambda item: _sort_key(item[0])):
        display_name = (
            accumulator.data.get("display_name")
            or accumulator.data.get("location")
            or accumulator.data.get("short_display_name")
            or accumulator.data.get("school_name")
            or team_id
        )
        short_display_name = (
            accumulator.data.get("short_display_name")
            or accumulator.data.get("school_name")
            or accumulator.data.get("location")
            or display_name
        )
        school_name = accumulator.data.get("school_name") or short_display_name

        team = Team(
            id=team_id,
            uid=accumulator.data.get("uid"),
            slug=accumulator.data.get("slug"),
            abbreviation=accumulator.data.get("abbreviation"),
            display_name=display_name,
            short_display_name=short_display_name,
            school_name=school_name,
            location=accumulator.data.get("location"),
            mascot=accumulator.data.get("mascot"),
            color=accumulator.data.get("color"),
            alternate_color=accumulator.data.get("alternate_color"),
            logo=accumulator.data.get("logo"),
            logo_dark=accumulator.data.get("logo_dark"),
            links=accumulator.data.get("links", []),
            is_active=accumulator.data.get("is_active"),
        )
        teams.append(team)

        alias_values = set(accumulator.aliases)
        alias_values.update(
            value
            for value in (
                team.display_name,
                team.short_display_name,
                team.school_name,
                team.location,
                team.mascot,
                team.abbreviation,
            )
            if value
        )
        alias_values.update(TEAM_ALIAS_OVERRIDES.get(team_id, ()))
        for alias in alias_values:
            for variant in _name_variants(alias):
                alias_map[variant].add(team_id)

    return teams, alias_map


def _winner_team_id(matchup: dict[str, Any]) -> str | None:
    """Return the winning team ID from an ESPN matchup payload."""

    for key in ("competitorOne", "competitorTwo"):
        competitor = matchup.get(key) or {}
        if competitor.get("winner"):
            return competitor.get("id")
    return None


def _region_for_game(
    matchup: dict[str, Any],
    round_id: int,
    regions: list[BracketRegion],
) -> tuple[int | None, str | None]:
    """Infer the region attached to a game from ESPN's bracket ordering."""

    if round_id == 0:
        region_id = matchup.get("regionId")
        if region_id is None:
            return (None, None)
        region = next((region for region in regions if region.id == region_id), None)
        return (region_id, region.name if region else matchup.get("label"))

    # Final Four and Championship games no longer belong to a single region.
    if round_id >= 5:
        return (None, None)

    block_size_by_round = {1: 8, 2: 4, 3: 2, 4: 1}
    block_size = block_size_by_round[round_id]
    region_index = (matchup["bracketLocation"] - 1) // block_size
    region = regions[region_index]
    return (region.id, region.name)


def _slot_from_competitor(competitor: dict[str, Any], from_game_id: str | None = None) -> GameSlot:
    """Convert an ESPN competitor payload into a normalized game slot."""

    return GameSlot(
        team_id=competitor.get("id"),
        seed=competitor.get("seed"),
        from_game_id=from_game_id,
    )


def build_starting_bracket() -> Bracket:
    """Build the bracket as it stood before the first round started.

    The saved ESPN page contains later results, so this function intentionally resets
    rounds 1-6 to ``future`` while keeping First Four results intact.
    """

    bracket_state = _extract_espnfitt_state(ESPN_BRACKET_PATH)["page"]["content"]["bracket"]
    regions = [
        BracketRegion(id=region["id"], name=region["labelPrimary"], slug=region.get("slug"))
        for region in bracket_state["regions"]
    ]
    rounds = [
        BracketRound(
            id=round_data["id"],
            name=round_data["labelPrimary"],
            num_games=round_data["numMatchups"],
            start_at=_parse_datetime(round_data.get("start")),
            end_at=_parse_datetime(round_data.get("end")),
        )
        for round_data in bracket_state["rounds"]
    ]
    round_names = {round_data.id: round_data.name for round_data in rounds}

    matchups = sorted(
        bracket_state["matchups"],
        key=lambda matchup: (matchup["roundId"], matchup["bracketLocation"]),
    )
    by_round_and_location: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    for matchup in matchups:
        by_round_and_location[matchup["roundId"]][matchup["bracketLocation"]] = matchup

    # ESPN does not explicitly link First Four games to their first-round targets,
    # so we infer that edge from the winning team that appears in round 1.
    play_in_targets: dict[str, str] = {}
    for matchup in by_round_and_location.get(0, {}).values():
        winner_team_id = _winner_team_id(matchup)
        if not winner_team_id:
            continue
        for round_one_matchup in by_round_and_location.get(1, {}).values():
            team_ids = {
                (round_one_matchup.get("competitorOne") or {}).get("id"),
                (round_one_matchup.get("competitorTwo") or {}).get("id"),
            }
            if winner_team_id in team_ids:
                play_in_targets[matchup["id"]] = round_one_matchup["id"]
                break

    games_by_id: dict[str, Game] = {}
    for matchup in matchups:
        round_id = matchup["roundId"]
        region_id, region_name = _region_for_game(matchup, round_id, regions)
        team_1 = _slot_from_competitor(matchup["competitorOne"])
        team_2 = _slot_from_competitor(matchup["competitorTwo"])
        status = GameStatus.FINISHED if round_id == 0 else GameStatus.FUTURE
        winner_team_id = _winner_team_id(matchup) if round_id == 0 else None

        # Later-round participants are not known yet at the "before round 1" snapshot,
        # so they are represented only through feeder game references.
        if round_id >= 2:
            team_1 = GameSlot()
            team_2 = GameSlot()

        game = Game(
            id=matchup["id"],
            round_id=round_id,
            round_name=round_names[round_id],
            bracket_location=matchup["bracketLocation"],
            region_id=region_id,
            region_name=region_name,
            scheduled_at=_parse_datetime(matchup.get("date")),
            location=matchup.get("location"),
            status=status,
            team_1=team_1,
            team_2=team_2,
            winner_team_id=winner_team_id,
        )
        games_by_id[game.id] = game

    # From round 2 onward, bracketLocation pairs define the graph shape.
    for round_id in range(2, 7):
        next_round_games = by_round_and_location.get(round_id, {})
        previous_round_games = by_round_and_location.get(round_id - 1, {})
        for location, matchup in next_round_games.items():
            feed_locations = (location * 2 - 1, location * 2)
            feed_games = [
                games_by_id[previous_round_games[feed_location]["id"]]
                for feed_location in feed_locations
            ]
            current_game = games_by_id[matchup["id"]]
            current_game.feed_in_game_ids = [feed_game.id for feed_game in feed_games]
            current_game.team_1 = GameSlot(from_game_id=feed_games[0].id)
            current_game.team_2 = GameSlot(from_game_id=feed_games[1].id)
            for feed_game in feed_games:
                feed_game.feeds_to_game_id = current_game.id

    # First Four winners are known in this snapshot and should remain attached to
    # their first-round slots while still preserving the feed-in relationship.
    for play_in_matchup in by_round_and_location.get(0, {}).values():
        play_in_game = games_by_id[play_in_matchup["id"]]
        target_game_id = play_in_targets.get(play_in_game.id)
        if not target_game_id:
            continue
        target_game = games_by_id[target_game_id]
        target_game.feed_in_game_ids = [play_in_game.id]
        play_in_game.feeds_to_game_id = target_game_id

        winner_team_id = play_in_game.winner_team_id
        play_in_seed = play_in_matchup["competitorOne"].get("seed") or play_in_matchup["competitorTwo"].get("seed")
        if winner_team_id == target_game.team_1.team_id:
            target_game.team_1.from_game_id = play_in_game.id
            target_game.team_1.seed = play_in_seed
        elif winner_team_id == target_game.team_2.team_id:
            target_game.team_2.from_game_id = play_in_game.id
            target_game.team_2.seed = play_in_seed

    return Bracket(
        id=str(bracket_state["id"]),
        name=bracket_state["name"],
        season=bracket_state["season"],
        league=bracket_state["league"],
        active_round=1,
        regions=regions,
        rounds=rounds,
        games=sorted(
            games_by_id.values(),
            key=lambda game: (game.round_id, game.bracket_location),
        ),
    )


def _build_fuzzy_candidates(teams: list[Team]) -> dict[str, set[str]]:
    """Build a normalized-name index used for fuzzy fallback matching."""

    candidates: dict[str, set[str]] = defaultdict(set)
    for team in teams:
        for value in (
            team.display_name,
            team.short_display_name,
            team.school_name,
            team.location,
            team.abbreviation,
        ):
            if not value:
                continue
            candidates[_normalize_name(value)].add(team.id)
    return candidates


def _string_similarity(left: str, right: str) -> float:
    """Score the similarity of two normalized team names."""

    return SequenceMatcher(a=left, b=right).ratio()


def _match_kenpom_team(
    team_name: str,
    alias_map: dict[str, set[str]],
    fuzzy_candidates: dict[str, set[str]],
) -> str | None:
    """Match a KenPom team name back to one ESPN team ID."""

    variants = _name_variants(team_name)
    exact_matches: set[str] = set()
    for variant in variants:
        exact_matches.update(alias_map.get(variant, set()))
    if len(exact_matches) == 1:
        return next(iter(exact_matches))

    normalized = _normalize_name(team_name)
    if normalized in fuzzy_candidates and len(fuzzy_candidates[normalized]) == 1:
        return next(iter(fuzzy_candidates[normalized]))

    best_team_id: str | None = None
    best_score = 0.0
    second_best = 0.0
    for candidate_name, team_ids in fuzzy_candidates.items():
        score = _string_similarity(normalized, candidate_name)
        if score > best_score:
            second_best = best_score
            best_score = score
            best_team_id = next(iter(team_ids)) if len(team_ids) == 1 else None
        elif score > second_best:
            second_best = score

    # Keep fuzzy matching conservative. The local ESPN team universe is incomplete,
    # so a weak fuzzy threshold would silently map KenPom rows to the wrong schools.
    if best_team_id and best_score >= 0.94 and (best_score - second_best) >= 0.03:
        return best_team_id
    return None


def parse_kenpom_rows(path: Path) -> list[KenPomRow]:
    """Parse the saved KenPom index page into normalized rows."""

    parser = KenPomTableParser()
    parser.feed(path.read_text())
    return parser.rows


def build_kenpom_ratings(teams: list[Team], alias_map: dict[str, set[str]]) -> list[KenPomRating]:
    """Build the daily KenPom snapshot and annotate matched teams with KenPom names."""

    teams_by_id = {team.id: team for team in teams}
    fuzzy_candidates = _build_fuzzy_candidates(teams)
    ratings: list[KenPomRating] = []
    seen_team_ids: set[str] = set()
    for row in parse_kenpom_rows(KENPOM_INDEX_PATH):
        team_id = _match_kenpom_team(row.team_name, alias_map, fuzzy_candidates)
        if not team_id or team_id in seen_team_ids:
            continue

        teams_by_id[team_id].kenpom_name = row.team_name
        ratings.append(KenPomRating(team_id=team_id, net_rating=row.net_rating))
        seen_team_ids.add(team_id)

    ratings.sort(key=lambda rating: _sort_key(rating.team_id))
    return ratings


def _write_json(path: Path, payload: Any) -> None:
    """Write JSON with stable indentation and a trailing newline."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _remove_legacy_kenpom_output() -> None:
    """Remove the older flat KenPom output path if it still exists."""

    LEGACY_KENPOM_OUTPUT_PATH.unlink(missing_ok=True)


def build_structs() -> tuple[list[Team], Bracket, list[KenPomRating]]:
    """Build all normalized structs in memory."""

    teams, alias_map = _merge_team_data()
    bracket = build_starting_bracket()
    kenpom_ratings = build_kenpom_ratings(teams, alias_map)
    return teams, bracket, kenpom_ratings


def write_structs() -> tuple[list[Team], Bracket, list[KenPomRating]]:
    """Write all normalized structs to disk."""

    teams, bracket, kenpom_ratings = build_structs()
    _write_json(
        TEAMS_OUTPUT_PATH,
        [team.model_dump(mode="json", exclude_none=True) for team in teams],
    )
    _write_json(
        BRACKET_OUTPUT_PATH,
        bracket.model_dump(mode="json", exclude_none=True),
    )
    _write_json(
        KENPOM_OUTPUT_PATH,
        [rating.model_dump(mode="json", exclude_none=True) for rating in kenpom_ratings],
    )
    _remove_legacy_kenpom_output()
    return teams, bracket, kenpom_ratings


def main() -> None:
    """Generate all structs and print a compact summary."""

    teams, bracket, kenpom_ratings = write_structs()
    print(
        f"Wrote {len(teams)} teams, {len(bracket.games)} games, and "
        f"{len(kenpom_ratings)} KenPom ratings."
    )


if __name__ == "__main__":
    main()
