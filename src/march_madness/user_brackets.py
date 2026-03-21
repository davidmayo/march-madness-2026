"""Parse saved NCAA user bracket pages into typed JSON exports.

The browser saves in ``data/brackets`` are split into two layers:
- ``<name>.html`` is mostly a shell page.
- ``<name>_files/a.html`` is the saved iframe that contains the bracket itself.

This module extracts only the user's bracket information:
- entry metadata such as the entry name and NCAA print/PDF URL
- the picked teams shown in every matchup slot
- the inferred picked winner for every game
- the champion pick and tiebreaker

The saved NCAA markup does not directly label the picked winner inside each
matchup card. Instead, each card shows the two teams occupying that game's
slots, and the winner is implied by which team appears in the parent game in
the next round. The parser below reconstructs that bracket tree.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ROOT = Path(__file__).resolve().parents[2]
BRACKETS_DIR = ROOT / "data" / "brackets"
HOMEPAGE_IFRAME_PATH = BRACKETS_DIR / "bracket-homepage_files" / "a.html"
USER_BRACKETS_DIR = ROOT / "data" / "user-brackets"

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

ENTRY_URL_PATTERN = re.compile(
    r"https://play-prod\.ncaa\.com/mens-bracket-challenge/api/v2/"
    r"ncaabracketchallenge/pdf/entry/(?P<entry_id>\d+)\?name=[^\"']+"
)
ENTRY_NAME_PATTERN = re.compile(r"<header[^>]*><h2>(?P<entry_name>.*?)</h2></header>")
CHAMPION_SLUG_PATTERN = re.compile(
    r'class="team-champ-picked"[^>]*style="[^"]*?/([^/\"?]+)\.(?:png|svg|webp)'
)
WINNING_SCORE_PATTERN = re.compile(
    r'<input[^>]*name="winning-team-score"[^>]*value="(?P<score>\d+)"'
    r'|<input[^>]*value="(?P<score_alt>\d+)"[^>]*name="winning-team-score"'
)
LOSING_SCORE_PATTERN = re.compile(
    r'<input[^>]*name="losing-team-score"[^>]*value="(?P<score>\d+)"'
    r'|<input[^>]*value="(?P<score_alt>\d+)"[^>]*name="losing-team-score"'
)
HOMEPAGE_ROW_PATTERN = re.compile(
    r"<button>(?P<entry_name>.*?)</button><button class=\"realName\">"
    r"(?P<real_name>.*?)</button>"
)

UserCategory = Literal["student", "staff"]
BracketRegion = Literal["EAST", "SOUTH", "WEST", "MIDWEST"]


class BracketMetadata(BaseModel):
    """Store identifying information for one saved NCAA bracket."""

    model_config = ConfigDict(extra="forbid")

    user_name: str
    entry_name: str
    user_categories: list[UserCategory] = Field(default_factory=list)
    ncaa_com_id: int | None = None
    ncaa_com_url: str | None = None


class PickedTeam(BaseModel):
    """Represent one team inside a user's bracket pick."""

    model_config = ConfigDict(extra="forbid")

    seed: str
    team_name: str


class BracketGamePick(BaseModel):
    """Represent the user's pick for one game in the bracket tree."""

    model_config = ConfigDict(extra="forbid")

    game_key: str
    round_id: int
    round_name: str
    region: BracketRegion | None = None
    matchup_index: int
    picked_team_1: PickedTeam
    picked_team_2: PickedTeam
    picked_winner: PickedTeam


class BracketTiebreaker(BaseModel):
    """Represent the user's title-game tiebreaker guess."""

    model_config = ConfigDict(extra="forbid")

    winner_score: int
    loser_score: int


class BracketPicks(BaseModel):
    """Store the parsed picks for one user bracket."""

    model_config = ConfigDict(extra="forbid")

    games: list[BracketGamePick]
    champion: PickedTeam
    tiebreaker: BracketTiebreaker


class UserBracket(BaseModel):
    """Store the full parsed output for one saved user bracket."""

    model_config = ConfigDict(extra="forbid")

    bracket_metadata: BracketMetadata
    bracket_picks: BracketPicks


@dataclass(frozen=True)
class ParsedTeam:
    """Represent one team block extracted from the saved bracket HTML."""

    seed: str
    name: str


@dataclass
class ParsedSlot:
    """Represent one matchup slot and both NCAA displays that may appear there."""

    actual: ParsedTeam | None = None
    picked: ParsedTeam | None = None
    logo_slug: str | None = None

    def chosen_team(self) -> ParsedTeam:
        """Return the team that belongs in this slot for the user's bracket."""

        if self.picked is not None:
            return self.picked
        if self.actual is not None:
            return self.actual
        msg = "Expected either a picked or actual team in every matchup slot."
        raise ValueError(msg)


@dataclass
class ParsedMatchup:
    """Represent one saved matchup card from the NCAA bracket UI."""

    round_id: int
    region: str | None
    slot_1: ParsedSlot = field(default_factory=ParsedSlot)
    slot_2: ParsedSlot = field(default_factory=ParsedSlot)


@dataclass
class ParsedPage:
    """Represent the raw information extracted from one saved iframe page."""

    entry_name: str
    ncaa_com_id: int | None
    ncaa_com_url: str | None
    champion_slug: str
    tiebreaker_winner_score: int
    tiebreaker_loser_score: int
    matchups: list[ParsedMatchup]


@dataclass
class _CapturedTeam:
    """Track an in-progress team block while the parser is inside the HTML node."""

    kind: Literal["actual", "picked"]
    seed: str | None = None
    name: str | None = None


class SavedBracketHTMLParser(HTMLParser):
    """Parse matchup cards from the saved NCAA bracket iframe HTML."""

    def __init__(self) -> None:
        """Initialize parser state."""

        super().__init__()
        self.depth = 0
        self.current_region: str | None = None
        self.current_round_id: int | None = None
        self.round_depth: int | None = None
        self.region_header_depth: int | None = None
        self.region_header_parts: list[str] = []
        self.current_matchup: ParsedMatchup | None = None
        self.matchup_depth: int | None = None
        self.current_slot_index: int | None = None
        self.current_slot_depth: int | None = None
        self.current_team: _CapturedTeam | None = None
        self.current_team_depth: int | None = None
        self.capture_seed = False
        self.capture_name = False
        self.text_buffer: list[str] = []
        self.matchups: list[ParsedMatchup] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track entry into regions, rounds, matchup slots, and team blocks."""

        self.depth += 1
        attrs_map = dict(attrs)
        classes = set((attrs_map.get("class") or "").split())

        if tag == "h3":
            self.region_header_depth = self.depth
            self.region_header_parts = []

        if "bracket-round" in classes:
            round_class = next((value for value in classes if value.startswith("round-")), None)
            if round_class is not None:
                self.current_round_id = int(round_class.split("-", maxsplit=1)[1])
                self.round_depth = self.depth

        if "bracket-matchup" in classes and self.current_round_id is not None:
            self.current_matchup = ParsedMatchup(
                round_id=self.current_round_id,
                region=self.current_region,
            )
            self.matchup_depth = self.depth

        if self.current_matchup is not None and "matchup-team" in classes:
            if "team1" in classes:
                self.current_slot_index = 1
                self.current_slot_depth = self.depth
            elif "team2" in classes:
                self.current_slot_index = 2
                self.current_slot_depth = self.depth

        # NCAA renders the slot logo on the same container that holds the team blocks.
        # For the first round, that gives us a clean team-name -> slug mapping for every
        # tournament team, which we later reuse to resolve the champion logo.
        if self.current_matchup is not None and self.current_slot_index is not None and "team-logo" in classes:
            style = attrs_map.get("style") or ""
            slug = _extract_asset_slug(style)
            if slug is not None:
                self._current_slot().logo_slug = slug

        if self.current_matchup is not None and self.current_slot_index is not None:
            if "actual-team" in classes:
                self.current_team = _CapturedTeam(kind="actual")
                self.current_team_depth = self.depth
            elif "picked-team" in classes:
                self.current_team = _CapturedTeam(kind="picked")
                self.current_team_depth = self.depth

        if self.current_team is not None and tag == "span" and "seed" in classes:
            self.capture_seed = True
            self.text_buffer = []
        if self.current_team is not None and tag == "span" and "team-name" in classes:
            self.capture_name = True
            self.text_buffer = []

    def handle_endtag(self, tag: str) -> None:
        """Close parser scopes and emit completed matchup objects."""

        if self.capture_seed and tag == "span":
            self.current_team.seed = _collapse_whitespace("".join(self.text_buffer))
            self.capture_seed = False
        if self.capture_name and tag == "span":
            self.current_team.name = _collapse_whitespace("".join(self.text_buffer))
            self.capture_name = False

        if self.current_team is not None and self.current_team_depth == self.depth:
            parsed_team = ParsedTeam(
                seed=self.current_team.seed or "",
                name=self.current_team.name or "",
            )
            current_slot = self._current_slot()
            if self.current_team.kind == "actual":
                current_slot.actual = parsed_team
            else:
                current_slot.picked = parsed_team
            self.current_team = None
            self.current_team_depth = None

        if self.current_slot_depth == self.depth:
            self.current_slot_index = None
            self.current_slot_depth = None

        if self.current_matchup is not None and self.matchup_depth == self.depth:
            self.matchups.append(self.current_matchup)
            self.current_matchup = None
            self.matchup_depth = None

        if self.current_round_id is not None and self.round_depth == self.depth:
            self.current_round_id = None
            self.round_depth = None

        if self.region_header_depth == self.depth and tag == "h3":
            region_name = _collapse_whitespace("".join(self.region_header_parts))
            if region_name in REGION_ORDER:
                self.current_region = region_name
            self.region_header_depth = None
            self.region_header_parts = []

        self.depth -= 1

    def handle_data(self, data: str) -> None:
        """Collect text content for region headers and team labels."""

        if self.region_header_depth is not None:
            self.region_header_parts.append(data)
        if self.capture_seed or self.capture_name:
            self.text_buffer.append(data)

    def _current_slot(self) -> ParsedSlot:
        """Return the currently active matchup slot."""

        if self.current_matchup is None or self.current_slot_index is None:
            msg = "Tried to access a matchup slot outside of a matchup-team container."
            raise ValueError(msg)
        return self.current_matchup.slot_1 if self.current_slot_index == 1 else self.current_matchup.slot_2


def parse_homepage_real_names(homepage_iframe_path: Path = HOMEPAGE_IFRAME_PATH) -> dict[str, str]:
    """Parse a best-effort mapping of entry names to displayed real names."""

    if not homepage_iframe_path.exists():
        return {}

    text = homepage_iframe_path.read_text()
    real_names: dict[str, str] = {}
    for match in HOMEPAGE_ROW_PATTERN.finditer(text):
        entry_name = _collapse_whitespace(unescape(match.group("entry_name")))
        real_name = _collapse_whitespace(unescape(match.group("real_name")))
        if not entry_name:
            continue
        # The saved page contains duplicate leaderboard tables. Keep the longest
        # non-empty real name we see for the entry.
        if real_name and len(real_name) > len(real_names.get(entry_name, "")):
            real_names[entry_name] = real_name
        else:
            real_names.setdefault(entry_name, "")
    return real_names


def parse_saved_user_bracket(
    page_path: Path,
    homepage_real_names: dict[str, str] | None = None,
) -> UserBracket:
    """Parse one saved top-level NCAA bracket page into a typed user bracket."""

    iframe_path = page_path.with_name(f"{page_path.stem}_files") / "a.html"
    if not iframe_path.exists():
        msg = f"Could not find saved iframe content for {page_path.name}: {iframe_path}"
        raise FileNotFoundError(msg)

    homepage_real_names = homepage_real_names or {}
    parsed_page = _parse_saved_iframe_page(iframe_path)
    picks = _build_bracket_picks(parsed_page)
    user_name = _choose_user_name(
        file_stem=page_path.stem,
        entry_name=parsed_page.entry_name,
        homepage_real_name=homepage_real_names.get(parsed_page.entry_name),
    )
    metadata = BracketMetadata(
        user_name=user_name,
        entry_name=parsed_page.entry_name,
        user_categories=[],
        ncaa_com_id=parsed_page.ncaa_com_id,
        ncaa_com_url=parsed_page.ncaa_com_url,
    )
    return UserBracket(bracket_metadata=metadata, bracket_picks=picks)


def export_saved_user_brackets(output_dir: Path = USER_BRACKETS_DIR) -> list[Path]:
    """Parse every saved user bracket and write one JSON file per person."""

    homepage_real_names = parse_homepage_real_names()
    output_dir.mkdir(parents=True, exist_ok=True)
    written_paths: list[Path] = []

    for page_path in sorted(BRACKETS_DIR.glob("*.html")):
        if page_path.name == "bracket-homepage.html":
            continue
        user_bracket = parse_saved_user_bracket(page_path, homepage_real_names=homepage_real_names)
        output_path = output_dir / f"{page_path.stem}.json"
        output_path.write_text(user_bracket.model_dump_json(indent=4) + "\n")
        written_paths.append(output_path)

    return written_paths


def _parse_saved_iframe_page(iframe_path: Path) -> ParsedPage:
    """Parse one saved NCAA bracket iframe page into raw intermediate objects."""

    text = iframe_path.read_text()
    parser = SavedBracketHTMLParser()
    parser.feed(text)

    entry_name_match = ENTRY_NAME_PATTERN.search(text)
    if entry_name_match is None:
        msg = f"Could not find the entry name in {iframe_path}"
        raise ValueError(msg)
    entry_name = _collapse_whitespace(unescape(entry_name_match.group("entry_name")))

    entry_url_match = ENTRY_URL_PATTERN.search(text)
    ncaa_com_url = entry_url_match.group(0) if entry_url_match is not None else None
    ncaa_com_id = int(entry_url_match.group("entry_id")) if entry_url_match is not None else None

    champion_slug_match = CHAMPION_SLUG_PATTERN.search(text)
    if champion_slug_match is None:
        msg = f"Could not find the picked champion logo in {iframe_path}"
        raise ValueError(msg)

    winning_score_match = WINNING_SCORE_PATTERN.search(text)
    losing_score_match = LOSING_SCORE_PATTERN.search(text)
    if winning_score_match is None or losing_score_match is None:
        msg = f"Could not find the tiebreaker scores in {iframe_path}"
        raise ValueError(msg)

    return ParsedPage(
        entry_name=entry_name,
        ncaa_com_id=ncaa_com_id,
        ncaa_com_url=ncaa_com_url,
        champion_slug=champion_slug_match.group(1),
        tiebreaker_winner_score=int(winning_score_match.group("score") or winning_score_match.group("score_alt")),
        tiebreaker_loser_score=int(losing_score_match.group("score") or losing_score_match.group("score_alt")),
        matchups=parser.matchups,
    )


def _build_bracket_picks(parsed_page: ParsedPage) -> BracketPicks:
    """Convert raw parsed matchup cards into normalized user picks."""

    round_matchups = _group_matchups_by_round(parsed_page.matchups)
    region_round_matchups = _group_regional_matchups(round_matchups)
    semifinal_matchups = round_matchups[5]
    championship_matchup = round_matchups[6][0]

    logo_slug_to_name = _build_logo_slug_lookup(round_matchups[1])
    champion = _resolve_champion(
        championship_matchup=championship_matchup,
        champion_slug=parsed_page.champion_slug,
        logo_slug_to_name=logo_slug_to_name,
    )

    games: list[BracketGamePick] = []

    for region in REGION_ORDER:
        for round_id in range(1, 5):
            matchups = region_round_matchups[region][round_id]
            for matchup_index, matchup in enumerate(matchups, start=1):
                picked_team_1 = _to_picked_team(matchup.slot_1.chosen_team())
                picked_team_2 = _to_picked_team(matchup.slot_2.chosen_team())
                picked_winner = _regional_parent_winner(
                    region_round_matchups=region_round_matchups,
                    semifinal_matchups=semifinal_matchups,
                    region=region,
                    round_id=round_id,
                    matchup_index=matchup_index,
                )
                games.append(
                    BracketGamePick(
                        game_key=_build_game_key(region=region, round_id=round_id, matchup_index=matchup_index),
                        round_id=round_id,
                        round_name=ROUND_NAMES[round_id],
                        region=region,
                        matchup_index=matchup_index,
                        picked_team_1=picked_team_1,
                        picked_team_2=picked_team_2,
                        picked_winner=picked_winner,
                    )
                )

    for matchup_index, matchup in enumerate(semifinal_matchups, start=1):
        picked_team_1 = _to_picked_team(matchup.slot_1.chosen_team())
        picked_team_2 = _to_picked_team(matchup.slot_2.chosen_team())
        picked_winner = _championship_slot_pick(championship_matchup, matchup_index)
        games.append(
            BracketGamePick(
                game_key=f"final-four-{matchup_index}",
                round_id=5,
                round_name=ROUND_NAMES[5],
                region=None,
                matchup_index=matchup_index,
                picked_team_1=picked_team_1,
                picked_team_2=picked_team_2,
                picked_winner=picked_winner,
            )
        )

    games.append(
        BracketGamePick(
            game_key="championship-1",
            round_id=6,
            round_name=ROUND_NAMES[6],
            region=None,
            matchup_index=1,
            picked_team_1=_to_picked_team(championship_matchup.slot_1.chosen_team()),
            picked_team_2=_to_picked_team(championship_matchup.slot_2.chosen_team()),
            picked_winner=champion,
        )
    )

    return BracketPicks(
        games=games,
        champion=champion,
        tiebreaker=BracketTiebreaker(
            winner_score=parsed_page.tiebreaker_winner_score,
            loser_score=parsed_page.tiebreaker_loser_score,
        ),
    )


def _group_matchups_by_round(matchups: list[ParsedMatchup]) -> dict[int, list[ParsedMatchup]]:
    """Group parsed matchup cards by round and validate the expected counts."""

    grouped: dict[int, list[ParsedMatchup]] = {round_id: [] for round_id in ROUND_NAMES}
    for matchup in matchups:
        grouped[matchup.round_id].append(matchup)

    expected_counts = {1: 32, 2: 16, 3: 8, 4: 4, 5: 2, 6: 1}
    for round_id, expected_count in expected_counts.items():
        actual_count = len(grouped[round_id])
        if actual_count != expected_count:
            msg = f"Expected {expected_count} round-{round_id} matchups, found {actual_count}"
            raise ValueError(msg)

    return grouped


def _group_regional_matchups(
    round_matchups: dict[int, list[ParsedMatchup]],
) -> dict[str, dict[int, list[ParsedMatchup]]]:
    """Group the regional rounds by region name and validate regional counts."""

    grouped: dict[str, dict[int, list[ParsedMatchup]]] = {
        region: {round_id: [] for round_id in range(1, 5)} for region in REGION_ORDER
    }

    for round_id in range(1, 5):
        for matchup in round_matchups[round_id]:
            if matchup.region not in REGION_ORDER:
                msg = f"Unexpected region {matchup.region!r} in round {round_id}"
                raise ValueError(msg)
            grouped[matchup.region][round_id].append(matchup)

    expected_per_region = {1: 8, 2: 4, 3: 2, 4: 1}
    for region in REGION_ORDER:
        for round_id, expected_count in expected_per_region.items():
            actual_count = len(grouped[region][round_id])
            if actual_count != expected_count:
                msg = (
                    f"Expected {expected_count} {region} matchups in round {round_id}, "
                    f"found {actual_count}"
                )
                raise ValueError(msg)

    return grouped


def _build_logo_slug_lookup(first_round_matchups: list[ParsedMatchup]) -> dict[str, str]:
    """Build a slug-to-team-name lookup from the first-round slot logos."""

    slug_to_name: dict[str, str] = {}
    for matchup in first_round_matchups:
        for slot in (matchup.slot_1, matchup.slot_2):
            if slot.logo_slug is None or slot.actual is None:
                continue
            slug_to_name.setdefault(slot.logo_slug, slot.actual.name)
    return slug_to_name


def _resolve_champion(
    championship_matchup: ParsedMatchup,
    champion_slug: str,
    logo_slug_to_name: dict[str, str],
) -> PickedTeam:
    """Resolve the picked champion from the championship matchup and logo slug."""

    championship_teams = [
        championship_matchup.slot_1.chosen_team(),
        championship_matchup.slot_2.chosen_team(),
    ]
    champion_name = logo_slug_to_name.get(champion_slug)

    for team in championship_teams:
        if champion_name is not None and team.name == champion_name:
            return _to_picked_team(team)

    # The explicit slug lookup is the preferred path. This fallback handles cases
    # where the saved asset slug and displayed team name do not line up exactly in
    # the first-round lookup, but the championship participants are still enough to
    # identify the chosen winner.
    for team in championship_teams:
        if champion_slug in _name_to_slug_candidates(team.name):
            return _to_picked_team(team)

    msg = f"Could not resolve the champion slug {champion_slug!r} from the championship matchup."
    raise ValueError(msg)


def _regional_parent_winner(
    region_round_matchups: dict[str, dict[int, list[ParsedMatchup]]],
    semifinal_matchups: list[ParsedMatchup],
    region: str,
    round_id: int,
    matchup_index: int,
) -> PickedTeam:
    """Return the team that the user advanced from the given regional game."""

    if round_id < 4:
        parent_matchup = region_round_matchups[region][round_id + 1][(matchup_index - 1) // 2]
        parent_slot = 1 if matchup_index % 2 == 1 else 2
        return _slot_pick(parent_matchup, parent_slot)

    semifinal_index, semifinal_slot = REGION_TO_FINAL_FOUR_SLOT[region]
    return _slot_pick(semifinal_matchups[semifinal_index - 1], semifinal_slot)


def _championship_slot_pick(championship_matchup: ParsedMatchup, semifinal_index: int) -> PickedTeam:
    """Return the team occupying the requested championship slot."""

    return _slot_pick(championship_matchup, semifinal_index)


def _slot_pick(matchup: ParsedMatchup, slot_index: int) -> PickedTeam:
    """Return the user's picked team for one specific matchup slot."""

    slot = matchup.slot_1 if slot_index == 1 else matchup.slot_2
    return _to_picked_team(slot.chosen_team())


def _to_picked_team(team: ParsedTeam) -> PickedTeam:
    """Convert an internal parsed team into the external Pydantic model."""

    return PickedTeam(seed=team.seed, team_name=team.name)


def _build_game_key(region: str, round_id: int, matchup_index: int) -> str:
    """Build a stable key for one regional game."""

    return f"{region.lower()}-round-{round_id}-game-{matchup_index}"


def _choose_user_name(file_stem: str, entry_name: str, homepage_real_name: str | None) -> str:
    """Choose the best available human-facing name for the bracket owner."""

    fallback_name = _humanize_file_stem(file_stem)
    if homepage_real_name:
        tokens = [token for token in homepage_real_name.strip().split() if token]
        if len(tokens) >= 2 and all(len(token) > 1 for token in tokens):
            return homepage_real_name.strip()

    # If the entry name already looks like a person's full name, it is usually
    # more faithful than the title-cased file stem.
    if " " in entry_name and not any(char.isdigit() for char in entry_name):
        return entry_name

    return fallback_name


def _humanize_file_stem(file_stem: str) -> str:
    """Convert a dash-separated file stem into a readable fallback name."""

    return " ".join(part.capitalize() for part in file_stem.split("-"))


def _extract_asset_slug(style_value: str) -> str | None:
    """Extract the final filename stem from a background-image style attribute."""

    match = re.search(r"/([^/\"?]+)\.(?:png|svg|webp)", style_value)
    if match is None:
        return None
    return match.group(1)


def _name_to_slug_candidates(team_name: str) -> set[str]:
    """Generate a few slug candidates for a displayed NCAA team name."""

    normalized = team_name.lower()
    replacements = {
        "&": "and",
        ".": "",
        "'": "",
        "(": "",
        ")": "",
        ",": "",
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    normalized = re.sub(r"\s+", "-", normalized.strip())

    candidates = {normalized}
    # NCAA sometimes uses "st" instead of "saint" in asset names.
    candidates.add(normalized.replace("saint-", "st-"))
    # The saved assets occasionally keep common university abbreviations as-is.
    candidates.add(normalized.replace("state", "st"))
    return {candidate for candidate in candidates if candidate}


def _collapse_whitespace(value: str) -> str:
    """Collapse all internal whitespace to single spaces."""

    return " ".join(value.split())


if __name__ == "__main__":
    export_saved_user_brackets()
