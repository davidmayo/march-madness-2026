"""FastAPI app for the static-first tournament website."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from march_madness.frontend.site import default_bracket_slug
from march_madness.frontend.site import render_bracket_page
from march_madness.frontend.site import render_historical_page
from march_madness.frontend.site import render_prediction_page
from march_madness.frontend.site import render_standings_page


FRONTEND_ROOT = Path(__file__).resolve().parent
ASSETS_DIR = FRONTEND_ROOT / "assets"

app = FastAPI(
    title="March Madness 2026 Frontend",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.mount("/static", StaticFiles(directory=ASSETS_DIR), name="static")


@app.get("/", include_in_schema=False)
def root_redirect() -> RedirectResponse:
    """Redirect the site root to the standings page."""

    return RedirectResponse(url="/standings", status_code=307)


@app.get("/brackets", include_in_schema=False)
def bracket_index_redirect(bracket_slug: str | None = None) -> RedirectResponse:
    """Redirect the generic bracket route to the preferred default bracket."""

    selected_bracket_slug = bracket_slug or default_bracket_slug()
    return RedirectResponse(url=f"/brackets/{selected_bracket_slug}", status_code=307)


@app.get("/standings", response_class=HTMLResponse, include_in_schema=False)
def standings_page(category: str = "all") -> HTMLResponse:
    """Serve the current standings page."""

    return HTMLResponse(render_standings_page(category))


@app.get("/brackets/{bracket_slug}", response_class=HTMLResponse, include_in_schema=False)
def bracket_page(bracket_slug: str) -> HTMLResponse:
    """Serve the selected user bracket page."""

    status_code, html = render_bracket_page(bracket_slug)
    return HTMLResponse(html, status_code=status_code)


@app.get("/prediction", response_class=HTMLResponse, include_in_schema=False)
def prediction_page() -> HTMLResponse:
    """Serve the placeholder prediction page."""

    return HTMLResponse(render_prediction_page())


@app.get("/historical", response_class=HTMLResponse, include_in_schema=False)
def historical_page() -> HTMLResponse:
    """Serve the placeholder historical page."""

    return HTMLResponse(render_historical_page())
