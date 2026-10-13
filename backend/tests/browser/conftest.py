"""Fixtures for the ``browser`` test mark: real Playwright, real Chromium,
against local, sanitized HTML loaded with ``page.set_content()`` — never a
network request, never a live employer site.

Mirrors ``tests/conftest.py``'s discipline for ``integration``/Postgres: a
missing dependency skips with an actionable message rather than failing in a
way that looks like broken code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

_FIXTURES_DIR = Path(__file__).parent / "fixtures"

_SKIP_REASON = (
    "Playwright/Chromium is not available for browser tests.\n"
    "Install with:\n"
    "    pip install -e \".[browser]\"\n"
    "    python -m playwright install chromium"
)


@pytest.fixture
async def browser() -> AsyncIterator[object]:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        pytest.skip(_SKIP_REASON)

    async with async_playwright() as pw:
        try:
            instance = await pw.chromium.launch(headless=True)
        except Exception as exc:
            pytest.skip(f"{_SKIP_REASON}\nLaunch error: {exc}")
        try:
            yield instance
        finally:
            await instance.close()


@pytest.fixture
async def page(browser: object) -> AsyncIterator[object]:
    new_page = await browser.new_page()  # type: ignore[attr-defined]
    try:
        yield new_page
    finally:
        await new_page.close()


def fixture_html(name: str) -> str:
    """Read one sanitized local fixture by filename."""
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


async def load_fixture(page: object, name: str) -> None:
    """Load a sanitized local fixture directly into the page — no server, no
    network, no ``file://`` navigation. ``page.url`` stays ``about:blank``,
    which is why origin-policy tests live separately in
    ``tests/unit/test_browser_policy.py`` rather than here."""
    await page.set_content(fixture_html(name))  # type: ignore[attr-defined]
