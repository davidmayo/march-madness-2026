"""Generate a static-site build of the frontend for GitHub Pages."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from pathlib import PurePosixPath

from march_madness.frontend.site import USER_CATEGORY_FILTER_VALUES
from march_madness.frontend.site import SiteUrlBuilder
from march_madness.frontend.site import load_site_data
from march_madness.frontend.site import render_bracket_page_with_urls
from march_madness.frontend.site import render_not_found_page
from march_madness.frontend.site import render_prediction_page_with_urls
from march_madness.frontend.site import render_standings_page_with_urls


PACKAGE_ROOT = Path(__file__).resolve().parent
FRONTEND_ROOT = PACKAGE_ROOT / "frontend"
ASSETS_DIR = FRONTEND_ROOT / "assets"
DEFAULT_OUTPUT_DIR = PACKAGE_ROOT.parent.parent / "docs"


def build_static_site(output_dir: Path) -> None:
    """Write the frontend pages and assets into one static output directory."""

    site_data = load_site_data()

    # The generated directory is meant to be published directly, so keep the
    # well-known GitHub Pages files at the root alongside the rendered pages.
    output_dir.mkdir(parents=True, exist_ok=True)
    _copy_assets(output_dir / "static")
    _write_text(output_dir / ".nojekyll", "")

    _write_rendered_page(
        output_dir=output_dir,
        relative_output_path=PurePosixPath("index.html"),
        html=render_prediction_page_with_urls(
            url_builder=SiteUrlBuilder(PurePosixPath("index.html")),
        ),
    )
    _write_rendered_page(
        output_dir=output_dir,
        relative_output_path=PurePosixPath("404.html"),
        html=render_not_found_page(
            title="Page Not Found",
            message="The requested page does not exist in this published build.",
            url_builder=SiteUrlBuilder(PurePosixPath("404.html")),
        ),
    )

    for user_category_filter in USER_CATEGORY_FILTER_VALUES:
        relative_output_path = _standings_output_path(user_category_filter)
        _write_rendered_page(
            output_dir=output_dir,
            relative_output_path=relative_output_path,
            html=render_standings_page_with_urls(
                user_category_filter,
                url_builder=SiteUrlBuilder(relative_output_path),
            ),
        )

    prediction_output_path = PurePosixPath("prediction") / "index.html"
    _write_rendered_page(
        output_dir=output_dir,
        relative_output_path=prediction_output_path,
        html=render_prediction_page_with_urls(
            url_builder=SiteUrlBuilder(prediction_output_path),
        ),
    )

    for bracket_link in site_data.bracket_links:
        bracket_output_path = PurePosixPath("brackets") / bracket_link.slug / "index.html"
        _status_code, html = render_bracket_page_with_urls(
            bracket_link.slug,
            url_builder=SiteUrlBuilder(bracket_output_path),
        )
        _write_rendered_page(
            output_dir=output_dir,
            relative_output_path=bracket_output_path,
            html=html,
        )


def _standings_output_path(user_category_filter: str) -> PurePosixPath:
    """Return the output path for one standings category page."""

    if user_category_filter == "all":
        return PurePosixPath("standings") / "index.html"
    return PurePosixPath("standings") / user_category_filter / "index.html"


def _copy_assets(destination_dir: Path) -> None:
    """Copy the frontend asset directory into the static build."""

    shutil.copytree(ASSETS_DIR, destination_dir, dirs_exist_ok=True)


def _write_rendered_page(output_dir: Path, relative_output_path: PurePosixPath, html: str) -> None:
    """Write one rendered HTML page into the output directory."""

    output_path = output_dir / Path(relative_output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _write_text(output_path, html)


def _write_text(path: Path, contents: str) -> None:
    """Write UTF-8 text to disk."""

    path.write_text(contents, encoding="utf-8")


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the static-site deploy command."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where the generated static site should be written.",
    )
    return parser.parse_args()


def main() -> None:
    """Generate the static site from the current saved data."""

    args = _parse_args()
    build_static_site(Path(args.output_dir).resolve())


if __name__ == "__main__":
    main()
