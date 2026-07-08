"""Unit tests for pure helpers in services.orchestrator.router."""

from __future__ import annotations

from services.orchestrator.router import _extract_highlight, _stamp_today


def test_extract_highlight_finds_line_containing_signature() -> None:
    log_text = "\n".join(
        [
            "# Log bundle: scn-example — comment header, never real evidence",
            "2024-01-15T09:20:00Z INFO  dcgm[1]: Health monitoring started.",
            "2024-01-15T09:20:01Z INFO  dcgm[1]: Driver version 535.129.03",
            "2024-01-15T09:23:40Z CRIT  kernel[1]: NVRM: Xid (PCI:0000:3b:00): 79",
            "2024-01-15T09:23:41Z ERROR iDRAC[1]: GPU 0 critical Xid 79 fault detected",
            "2024-01-15T09:23:43Z INFO  dcgm[1]: Node flagged for remediation.",
        ]
    )
    highlight = _extract_highlight(log_text, ["Xid 79", "GPU has fallen off the bus"])
    assert highlight is not None
    assert "Xid 79" in highlight
    assert "# Log bundle" not in highlight


def test_extract_highlight_excludes_comment_lines_from_context_window() -> None:
    log_text = "\n".join(
        [
            "# comment line right before the match — must not appear in output",
            "2024-01-15T09:23:40Z CRIT  kernel[1]: fault signature ABC123 detected",
        ]
    )
    highlight = _extract_highlight(log_text, ["ABC123"])
    assert highlight is not None
    assert "#" not in highlight


def test_extract_highlight_returns_window_around_match_not_whole_file() -> None:
    lines = [f"2024-01-15T09:2{i}:00Z INFO  noise line {i}" for i in range(20)]
    lines[10] = "2024-01-15T09:30:00Z CRIT  the actual signature XYZ appears here"
    log_text = "\n".join(lines)

    highlight = _extract_highlight(log_text, ["XYZ"])
    assert highlight is not None
    highlight_lines = highlight.splitlines()
    assert len(highlight_lines) <= 5  # match + 2 lines of context each side
    assert any("XYZ" in ln for ln in highlight_lines)


def test_extract_highlight_falls_back_when_no_signature_matches() -> None:
    log_text = "2024-01-15T09:20:00Z INFO  dcgm[1]: nothing interesting here\nmore text"
    highlight = _extract_highlight(log_text, ["some-signature-not-present"])
    assert highlight is not None  # still returns something rather than None


def test_extract_highlight_returns_none_for_empty_log() -> None:
    assert _extract_highlight("", ["Xid 79"]) is None
    assert _extract_highlight("# only a comment\n\n", ["Xid 79"]) is None


def test_stamp_today_replaces_date_but_keeps_time() -> None:
    import datetime as dt

    stamped = _stamp_today("2024-01-15T09:23:40Z CRIT something happened")
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    assert stamped.startswith(today)
    assert stamped.endswith("T09:23:40Z CRIT something happened")
