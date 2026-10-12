"""The HTML-to-text converter used on a connector's raw posting description.

Pure, stdlib-only, no network — exactly the kind of thing that belongs in
``tests/unit``.
"""

from __future__ import annotations

import pytest

from app.connectors.html_text import html_to_text

pytestmark = pytest.mark.unit


class TestHtmlToText:
    def test_none_in_none_out(self) -> None:
        assert html_to_text(None) is None

    def test_blank_in_none_out(self) -> None:
        assert html_to_text("   ") is None

    def test_plain_text_passes_through(self) -> None:
        assert html_to_text("Just text, no tags.") == "Just text, no tags."

    def test_paragraphs_become_separate_lines(self) -> None:
        html = "<p>First paragraph.</p><p>Second paragraph.</p>"
        result = html_to_text(html)
        assert result is not None
        assert "First paragraph." in result
        assert "Second paragraph." in result
        # The two paragraphs must not run together into one line.
        lines = [line for line in result.split("\n") if line.strip()]
        assert "First paragraph." in lines
        assert "Second paragraph." in lines

    def test_list_items_are_separated(self) -> None:
        html = "<ul><li>Python</li><li>PostgreSQL</li></ul>"
        result = html_to_text(html)
        assert result is not None
        lines = [line for line in result.split("\n") if line.strip()]
        assert "Python" in lines
        assert "PostgreSQL" in lines

    def test_entities_are_unescaped(self) -> None:
        html = "<p>Node.js &amp; TypeScript &mdash; 3+ years</p>"
        result = html_to_text(html)
        assert result is not None
        assert "&amp;" not in result
        assert "&" in result

    def test_inline_tags_do_not_fragment_a_sentence(self) -> None:
        html = "<p>We use <strong>Python</strong> and <em>PostgreSQL</em>.</p>"
        result = html_to_text(html)
        assert result == "We use Python and PostgreSQL."

    def test_internal_whitespace_is_collapsed(self) -> None:
        html = "<p>Too    many     spaces.</p>"
        assert html_to_text(html) == "Too many spaces."

    def test_malformed_markup_does_not_raise(self) -> None:
        """A posting with broken HTML must not take down the whole board's ingestion."""
        html = "<p>Unclosed paragraph <li>stray list item <div>nested"
        result = html_to_text(html)
        assert result is not None
        assert "Unclosed paragraph" in result

    def test_script_and_style_content_is_not_specially_handled_but_does_not_crash(
        self,
    ) -> None:
        """Not a security boundary (nothing here executes anything) — just must not crash."""
        html = "<p>Real content.</p><script>alert(1)</script>"
        result = html_to_text(html)
        assert result is not None
        assert "Real content." in result
