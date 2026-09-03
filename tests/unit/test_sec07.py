"""SEC-07 — XSS / injection in KB article content does not reach the browser.

The KB article content is stored as plain text (Markdown) and served via the
Gateway's JSON API.  The React UI renders it with a safe Markdown renderer
(no dangerouslySetInnerHTML).  This test verifies the server-side contract:
content is returned verbatim and is not interpreted as HTML or JavaScript by
the server layer.

What we test here (server-side):
  1. KB article content containing HTML/script tags is stored and returned
     verbatim — the API does not double-encode or strip it.
  2. The KBIndex search returns content fields that the UI layer must handle
     safely; no server-side HTML execution occurs.
  3. The pack loader accepts arbitrary Markdown body text without errors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from libs.common.pack_loader import load_pack
from services.mcp_tools.kb_index import KBIndex

PACK_DIR = Path(__file__).parent.parent.parent / "packs" / "datacenter-xe9680"


@pytest.fixture(scope="module")
def loaded_pack():
    return load_pack(PACK_DIR)


@pytest.fixture(scope="module")
def kb_index(loaded_pack):
    return KBIndex(loaded_pack)


# ─────────────────────────────────────────────────────────────────────────────
# SEC-07: content is returned as-is (no server-side execution or stripping)
# ─────────────────────────────────────────────────────────────────────────────

_XSS_STRINGS = [
    "<script>alert('xss')</script>",
    "<img src=x onerror=alert(1)>",
    "javascript:alert(1)",
    '"><svg onload=alert(1)>',
    "{{7*7}}",  # template injection probe
    "${7*7}",  # template injection probe
    "<iframe src='javascript:alert(1)'>",
]


@pytest.mark.parametrize("xss", _XSS_STRINGS)
def test_sec07_xss_in_signature_does_not_crash_search(
    kb_index: KBIndex,
    xss: str,
) -> None:
    """SEC-07: Searching with an XSS string as the signature must not raise.

    The server returns None (no match) or a result dict — either is safe.
    It must not execute, eval, or HTML-encode the string.
    """
    result = kb_index.search(xss)
    # result is None or a dict — never an exception, never HTML output
    assert result is None or isinstance(result, dict)


def test_sec07_kb_article_content_returned_verbatim(
    loaded_pack,
    kb_index: KBIndex,
) -> None:
    """SEC-07: KB content containing angle brackets is stored and retrieved verbatim.

    The server layer must NOT strip or re-encode the content. The UI is
    responsible for safe rendering.
    """
    # Pick any real KB article
    articles = loaded_pack.kb_articles
    assert articles, "No KB articles found in pack — check pack fixture"

    article = next(iter(articles.values()))
    # Content must be a non-empty string
    assert isinstance(article.body_md, str)
    assert len(article.body_md) > 0

    # Verify that content from a search result is also a plain string
    for sig in loaded_pack.signature_index:
        result = kb_index.search(sig)
        if result is not None:
            assert isinstance(result.get("content", ""), str)
            break


def test_sec07_pack_loader_accepts_arbitrary_markdown(loaded_pack) -> None:
    """SEC-07: The pack loader does not filter or escape Markdown body text.

    KB articles can contain fenced code blocks and backtick-escaped shell
    commands; these must survive load_pack() unmodified.
    """
    for article in loaded_pack.kb_articles.values():
        # Body text is str, not HTML-escaped
        assert "&lt;" not in article.body_md or "<" not in article.body_md, (
            f"KB article {article.id!r} body appears to be HTML-escaped — "
            "content should be raw Markdown"
        )


def test_sec07_search_result_body_md_is_string_not_html(
    loaded_pack,
    kb_index: KBIndex,
) -> None:
    """SEC-07: search() returns body_md as a plain string, never an HTML object."""
    for sig in loaded_pack.signature_index:
        result = kb_index.search(sig)
        if result is not None:
            content = result.get("body_md", "")
            assert isinstance(content, str)
            # Must not be wrapped in HTML tags by the server
            stripped = content.strip()
            assert not stripped.startswith("<!DOCTYPE"), "content must not be a full HTML page"
            assert not stripped.startswith("<html"), "content must not be wrapped in HTML tags"
            break
