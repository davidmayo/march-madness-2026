"""Render the mostly static frontend pages from the saved tournament data.

This module keeps the web layer intentionally simple:
- page content is built from the existing JSON exports and scoring logic
- HTML is rendered with small Python helpers instead of a template dependency
- the result stays easy to serve from FastAPI today and easy to pre-render later
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from pathlib import Path

from march_madness.canonical_bracket import REGION_ORDER
from march_madness.canonical_bracket import ROUND_NAMES
from march_madness.canonical_bracket import canonical_team_name_by_id
from march_madness.scoring import get_bracket_from_scoreboard_data
from march_madness.scoring import load_saved_user_brackets
from march_madness.scoring import score_saved_user_brackets
from march_madness.scrape import Bracket
from march_madness.user_brackets import BracketGamePick
from march_madness.user_brackets import UserBracket


ROOT = Path(__file__).resolve().parents[3]
SCOREBOARD_PATH = ROOT / "data" / "espn" / "api" / "scoreboard.json"


@dataclass(frozen=True)
class BracketLink:
    """Represent one selectable saved user bracket."""

    slug: str
    user_name: str
    entry_name: str


@dataclass(frozen=True)
class StandingsRow:
    """Represent one row in the rendered standings table."""

    rank: int
    slug: str
    user_name: str
    current_score: float
    max_possible_score: float | None
    correctly_picked_games: int | None
    incorrectly_picked_games: int | None
    champion_pick: str


@dataclass(frozen=True)
class SiteData:
    """Collect the loaded data needed by the frontend pages."""

    bracket_links: tuple[BracketLink, ...]
    bracket_lookup: dict[str, UserBracket]
    standings: tuple[StandingsRow, ...]
    standings_by_slug: dict[str, StandingsRow]
    reference_bracket: Bracket
    completed_game_count: int


@lru_cache(maxsize=1)
def load_site_data() -> SiteData:
    """Load the current standings and bracket data for the frontend."""

    scoreboard_blob = json.loads(SCOREBOARD_PATH.read_text())
    reference_bracket = get_bracket_from_scoreboard_data(scoreboard_blob)
    loaded_user_brackets = load_saved_user_brackets()

    bracket_links = tuple(
        sorted(
            (
                BracketLink(
                    slug=path.stem,
                    user_name=user_bracket.bracket_metadata.user_name,
                    entry_name=user_bracket.bracket_metadata.entry_name,
                )
                for path, user_bracket in loaded_user_brackets
            ),
            key=lambda link: (link.user_name, link.slug),
        )
    )
    bracket_lookup = {path.stem: user_bracket for path, user_bracket in loaded_user_brackets}

    standings_rows = tuple(
        StandingsRow(
            rank=rank,
            slug=path.stem,
            user_name=user_bracket.bracket_metadata.user_name,
            current_score=result.current_score,
            max_possible_score=result.max_possible_score,
            correctly_picked_games=result.correctly_picked_games,
            incorrectly_picked_games=result.incorrectly_picked_games,
            champion_pick=user_bracket.bracket_picks.champion.team_name,
        )
        for rank, (path, user_bracket, result) in enumerate(
            score_saved_user_brackets(reference_bracket, calculate_details=True),
            start=1,
        )
    )
    standings_by_slug = {row.slug: row for row in standings_rows}
    completed_game_count = sum(1 for game in reference_bracket.games if game.winner_team_id is not None)

    return SiteData(
        bracket_links=bracket_links,
        bracket_lookup=bracket_lookup,
        standings=standings_rows,
        standings_by_slug=standings_by_slug,
        reference_bracket=reference_bracket,
        completed_game_count=completed_game_count,
    )


def default_bracket_slug() -> str:
    """Return the preferred default bracket slug for the viewer."""

    site_data = load_site_data()
    if "david-mayo" in site_data.bracket_lookup:
        return "david-mayo"
    return site_data.bracket_links[0].slug


def render_standings_page() -> str:
    """Render the current standings page."""

    site_data = load_site_data()
    leader = site_data.standings[0]
    highest_ceiling = max((row.max_possible_score or 0.0) for row in site_data.standings)

    summary_cards = "".join(
        (
            _render_stat_card("Completed Games", f"{site_data.completed_game_count} of 63"),
            _render_stat_card("Current Leader", leader.user_name),
            _render_stat_card("Top Score", _format_score(leader.current_score)),
            _render_stat_card("Highest Ceiling", _format_score(highest_ceiling)),
        )
    )

    rows_html = "".join(_render_standings_row(row) for row in site_data.standings)
    body = f"""
    <section class="hero">
        <p class="eyebrow">Traditional scoring</p>
        <h1>Current Standings</h1>
        <p class="lede">
            Live standings computed from the local ESPN scoreboard snapshot and the saved NCAA bracket picks.
        </p>
        <div class="stat-grid">{summary_cards}</div>
    </section>
    <section class="panel">
        <div class="section-heading">
            <div>
                <p class="section-kicker">Group table</p>
                <h2>Standings Board</h2>
            </div>
            <p class="panel-note">Current score, remaining ceiling, and pick accuracy.</p>
        </div>
        <div class="table-wrap">
            <table class="standings-table">
                <thead>
                    <tr>
                        <th>Rank</th>
                        <th>Name</th>
                        <th>Score</th>
                        <th>Max Possible</th>
                        <th>Correct</th>
                        <th>Incorrect</th>
                        <th>Champion Pick</th>
                    </tr>
                </thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
    </section>
    """
    return _render_page_shell(
        title="Standings",
        active_path="/standings",
        page_body=body,
    )


def render_bracket_page(bracket_slug: str) -> tuple[int, str]:
    """Render one user's bracket page and return ``(status_code, html)``."""

    site_data = load_site_data()
    user_bracket = site_data.bracket_lookup.get(bracket_slug)
    if user_bracket is None:
        html = render_not_found_page(
            title="Bracket Not Found",
            message=f"No saved bracket exists for {bracket_slug!r}.",
        )
        return 404, html

    standings_row = site_data.standings_by_slug.get(bracket_slug)
    reference_lookup = {game.id: game for game in site_data.reference_bracket.games}
    regional_sections = "".join(
        _render_region_section(
            region=region,
            user_bracket=user_bracket,
            reference_lookup=reference_lookup,
        )
        for region in REGION_ORDER
    )
    final_rounds_section = _render_final_rounds_section(
        user_bracket=user_bracket,
        reference_lookup=reference_lookup,
    )

    score_card = "Not yet scored"
    max_card = "Pending"
    if standings_row is not None:
        score_card = _format_score(standings_row.current_score)
        max_card = _format_score(standings_row.max_possible_score)

    champion = user_bracket.bracket_picks.champion
    tiebreaker = user_bracket.bracket_picks.tiebreaker
    body = f"""
    <section class="hero">
        <p class="eyebrow">Bracket viewer</p>
        <h1>{escape(user_bracket.bracket_metadata.user_name)}</h1>
        <p class="lede">Entry name: {escape(user_bracket.bracket_metadata.entry_name)}</p>
        <div class="stat-grid">
            {_render_stat_card("Current Score", score_card)}
            {_render_stat_card("Max Possible", max_card)}
            {_render_stat_card("Champion Pick", _format_team_label(champion.seed, champion.team_name))}
            {_render_stat_card("Tiebreaker", f"{tiebreaker.winner_score} to {tiebreaker.loser_score}")}
        </div>
    </section>
    <section class="panel">
        <div class="section-heading">
            <div>
                <p class="section-kicker">Regional picks</p>
                <h2>Road To Indianapolis</h2>
            </div>
            <p class="panel-note">Picked matchups and winners, with completed-game status when available.</p>
        </div>
        {regional_sections}
    </section>
    <section class="panel">
        <div class="section-heading">
            <div>
                <p class="section-kicker">Final rounds</p>
                <h2>Final Four And Title Game</h2>
            </div>
        </div>
        {final_rounds_section}
    </section>
    """
    html = _render_page_shell(
        title=f"{user_bracket.bracket_metadata.user_name} Bracket",
        active_path=f"/brackets/{bracket_slug}",
        page_body=body,
        current_bracket_slug=bracket_slug,
    )
    return 200, html


def render_prediction_page() -> str:
    """Render the placeholder prediction page."""

    body = """
    <section class="hero">
        <p class="eyebrow">Coming next</p>
        <h1>Prediction Engine</h1>
        <p class="lede">
            This page will eventually surface model-based tournament predictions, scenario comparisons,
            and bracket win odds.
        </p>
    </section>
    <section class="panel placeholder-panel">
        <h2>Planned Content</h2>
        <p>
            The data pipeline is already in place for bracket state and historical scores. The next step is to
            layer in simulation outputs and render them here.
        </p>
    </section>
    """
    return _render_page_shell(
        title="Prediction",
        active_path="/prediction",
        page_body=body,
    )


def render_historical_page() -> str:
    """Render the placeholder historical page."""

    body = """
    <section class="hero">
        <p class="eyebrow">Archive</p>
        <h1>Historical Results</h1>
        <p class="lede">
            This page will eventually collect prior scoreboard snapshots, leaderboard movement,
            and bracket-elimination history over time.
        </p>
    </section>
    <section class="panel placeholder-panel">
        <h2>Planned Content</h2>
        <p>
            The intended direction is a static-friendly archive of daily standings, completed games,
            and notable swings in each user bracket's maximum possible score.
        </p>
    </section>
    """
    return _render_page_shell(
        title="Historical",
        active_path="/historical",
        page_body=body,
    )


def render_not_found_page(title: str, message: str) -> str:
    """Render a simple site-styled not-found page."""

    body = f"""
    <section class="hero">
        <p class="eyebrow">Not found</p>
        <h1>{escape(title)}</h1>
        <p class="lede">{escape(message)}</p>
    </section>
    """
    return _render_page_shell(
        title=title,
        active_path="",
        page_body=body,
    )


def _render_page_shell(
    *,
    title: str,
    active_path: str,
    page_body: str,
    current_bracket_slug: str | None = None,
) -> str:
    """Wrap page-specific HTML with the shared site shell."""

    site_data = load_site_data()
    menu_html = _render_menu(site_data, active_path=active_path, current_bracket_slug=current_bracket_slug)
    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{escape(title)} | March Madness 2026</title>
    <link rel="stylesheet" href="/static/style.css">
</head>
<body>
    <div class="page-noise"></div>
    <header class="site-header">
        <a class="brand" href="/standings">
            <span class="brand-mark">MM</span>
            <span class="brand-copy">
                <span class="brand-title">March Madness 2026</span>
                <span class="brand-subtitle">Saved brackets, live standings, static-first frontend</span>
            </span>
        </a>
        <nav class="desktop-nav">
            {_render_main_nav_links(active_path)}
        </nav>
        {menu_html}
    </header>
    <main class="site-main">
        {page_body}
    </main>
</body>
</html>
"""


def _render_main_nav_links(active_path: str) -> str:
    """Render the compact desktop nav."""

    links = [
        ("Brackets", f"/brackets/{default_bracket_slug()}", active_path.startswith("/brackets")),
        ("Standings", "/standings", active_path == "/standings"),
        ("Prediction", "/prediction", active_path == "/prediction"),
        ("Historical", "/historical", active_path == "/historical"),
    ]
    return "".join(
        f'<a class="nav-link{" is-active" if is_active else ""}" href="{escape(href)}">{escape(label)}</a>'
        for label, href, is_active in links
    )


def _render_menu(site_data: SiteData, *, active_path: str, current_bracket_slug: str | None) -> str:
    """Render the no-JavaScript drawer used as the site menu."""

    page_links = [
        ("Brackets", f"/brackets/{default_bracket_slug()}", active_path.startswith("/brackets")),
        ("Standings", "/standings", active_path == "/standings"),
        ("Prediction", "/prediction", active_path == "/prediction"),
        ("Historical", "/historical", active_path == "/historical"),
    ]
    page_links_html = "".join(
        f'<a class="drawer-link{" is-active" if is_active else ""}" href="{escape(href)}">{escape(label)}</a>'
        for label, href, is_active in page_links
    )
    bracket_links_html = "".join(
        (
            f'<a class="drawer-link bracket-link{" is-active" if current_bracket_slug == link.slug else ""}" '
            f'href="/brackets/{escape(link.slug)}">'
            f'<span>{escape(link.user_name)}</span>'
            f'<small>{escape(link.entry_name)}</small>'
            f"</a>"
        )
        for link in site_data.bracket_links
    )
    return f"""
    <details class="drawer">
        <summary class="drawer-toggle" aria-label="Open site navigation">
            <span class="drawer-line"></span>
            <span class="drawer-line"></span>
            <span class="drawer-line"></span>
        </summary>
        <div class="drawer-panel">
            <section class="drawer-section">
                <p class="drawer-heading">Pages</p>
                {page_links_html}
            </section>
            <section class="drawer-section">
                <p class="drawer-heading">Bracket Picks</p>
                <div class="drawer-bracket-list">
                    {bracket_links_html}
                </div>
            </section>
        </div>
    </details>
    """


def _render_region_section(region: str, user_bracket: UserBracket, reference_lookup: dict[str, object]) -> str:
    """Render one region's rounds in a four-column grid."""

    columns = "".join(
        _render_round_column(
            region=region,
            round_id=round_id,
            user_bracket=user_bracket,
            reference_lookup=reference_lookup,
        )
        for round_id in range(1, 5)
    )
    return f"""
    <section class="region-card">
        <div class="region-header">
            <p class="section-kicker">Region</p>
            <h3>{escape(region.title())}</h3>
        </div>
        <div class="round-grid">{columns}</div>
    </section>
    """


def _render_round_column(
    *,
    region: str,
    round_id: int,
    user_bracket: UserBracket,
    reference_lookup: dict[str, object],
) -> str:
    """Render all games for one region/round combination."""

    games = [
        game
        for game in user_bracket.bracket_picks.games
        if game.region == region and game.round_id == round_id
    ]
    games.sort(key=lambda game: game.matchup_index)
    cards_html = "".join(_render_game_card(game, reference_lookup) for game in games)
    return f"""
    <section class="round-column">
        <h4>{escape(ROUND_NAMES[round_id])}</h4>
        <div class="game-stack">{cards_html}</div>
    </section>
    """


def _render_final_rounds_section(user_bracket: UserBracket, reference_lookup: dict[str, object]) -> str:
    """Render the Final Four and championship picks."""

    final_four_games = [
        game for game in user_bracket.bracket_picks.games if game.round_id == 5
    ]
    championship_games = [
        game for game in user_bracket.bracket_picks.games if game.round_id == 6
    ]
    final_four_games.sort(key=lambda game: game.matchup_index)
    championship_games.sort(key=lambda game: game.matchup_index)

    final_four_cards = "".join(_render_game_card(game, reference_lookup) for game in final_four_games)
    championship_cards = "".join(_render_game_card(game, reference_lookup) for game in championship_games)
    return f"""
    <div class="round-grid final-round-grid">
        <section class="round-column">
            <h4>Final Four</h4>
            <div class="game-stack">{final_four_cards}</div>
        </section>
        <section class="round-column">
            <h4>Championship</h4>
            <div class="game-stack">{championship_cards}</div>
        </section>
    </div>
    """


def _render_game_card(game: BracketGamePick, reference_lookup: dict[str, object]) -> str:
    """Render a single picked game card with live-result status when available."""

    reference_game = reference_lookup.get(game.game_key)
    status_class = "pending"
    status_label = "Pending"
    result_html = '<p class="game-result muted">Actual winner: still pending</p>'

    if reference_game is not None and getattr(reference_game, "winner_team_id", None) is not None:
        actual_winner_id = getattr(reference_game, "winner_team_id")
        actual_winner_name = canonical_team_name_by_id().get(actual_winner_id, actual_winner_id)
        if game.picked_winner.team_id == actual_winner_id:
            status_class = "correct"
            status_label = "Correct"
        else:
            status_class = "wrong"
            status_label = "Miss"
        result_html = f'<p class="game-result">Actual winner: {escape(actual_winner_name)}</p>'

    return f"""
    <article class="game-card {status_class}">
        <div class="game-card-head">
            <span class="game-index">Game {game.matchup_index}</span>
            <span class="status-pill {status_class}">{escape(status_label)}</span>
        </div>
        <div class="team-row">
            <span class="seed-chip">{escape(game.picked_team_1.seed)}</span>
            <span>{escape(game.picked_team_1.team_name)}</span>
        </div>
        <div class="team-row">
            <span class="seed-chip">{escape(game.picked_team_2.seed)}</span>
            <span>{escape(game.picked_team_2.team_name)}</span>
        </div>
        <p class="picked-winner">Picked winner: {escape(game.picked_winner.team_name)}</p>
        {result_html}
    </article>
    """


def _render_standings_row(row: StandingsRow) -> str:
    """Render one row in the standings table."""

    return f"""
    <tr>
        <td>{row.rank}</td>
        <td>
            <a class="standings-link" href="/brackets/{escape(row.slug)}">{escape(row.user_name)}</a>
        </td>
        <td>{_format_score(row.current_score)}</td>
        <td>{_format_score(row.max_possible_score)}</td>
        <td>{row.correctly_picked_games if row.correctly_picked_games is not None else "—"}</td>
        <td>{row.incorrectly_picked_games if row.incorrectly_picked_games is not None else "—"}</td>
        <td>{escape(row.champion_pick)}</td>
    </tr>
    """


def _render_stat_card(label: str, value: str) -> str:
    """Render a compact summary card."""

    return f"""
    <div class="stat-card">
        <p>{escape(label)}</p>
        <strong>{escape(value)}</strong>
    </div>
    """


def _format_team_label(seed: str, team_name: str) -> str:
    """Format a seeded team label for display."""

    return f"({seed}) {team_name}"


def _format_score(value: float | None) -> str:
    """Render scores as clean whole numbers when possible."""

    if value is None:
        return "—"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.1f}"
