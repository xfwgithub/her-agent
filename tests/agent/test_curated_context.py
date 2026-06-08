"""Tests for the volatile context-curator data class & helpers.

Pure-Python tests, no AIAgent fixture required.
"""

from agent.curated_context import (
    CuratedContext,
    compute_token_ratio,
    should_curate,
    SECTION_HEADER,
)


# ---------------------------------------------------------------------------
# Threshold helpers
# ---------------------------------------------------------------------------

class TestComputeTokenRatio:
    def test_normal_case(self):
        assert compute_token_ratio(1000, 10000) == 0.1

    def test_zero_used(self):
        assert compute_token_ratio(0, 10000) == 0.0

    def test_zero_window(self):
        assert compute_token_ratio(1000, 0) == 0.0

    def test_none_used(self):
        assert compute_token_ratio(None, 10000) == 0.0

    def test_none_window(self):
        assert compute_token_ratio(1000, None) == 0.0

    def test_negative_inputs(self):
        assert compute_token_ratio(-1, 10000) == 0.0
        assert compute_token_ratio(1000, -1) == 0.0

    def test_above_one_when_used_exceeds_window(self):
        # Conservatively allow values > 1 (over-budget state).
        assert compute_token_ratio(15000, 10000) == 1.5


class TestShouldCurate:
    def test_below_threshold_no_fire(self):
        assert should_curate(500, 10000, 0.10) is False

    def test_at_threshold_fires(self):
        assert should_curate(1000, 10000, 0.10) is True

    def test_above_threshold_fires(self):
        assert should_curate(5000, 10000, 0.10) is True

    def test_disabled_never_fires(self):
        assert should_curate(99999, 10000, 0.10, enabled=False) is False

    def test_zero_threshold_never_fires(self):
        # Threshold == 0 is a "feature off" sentinel.
        assert should_curate(99999, 10000, 0.0) is False

    def test_missing_window_no_fire(self):
        assert should_curate(1000, None, 0.10) is False


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class TestParseCuratorText:
    def test_parses_all_sections(self):
        text = (
            "### Active Focus\n"
            "Refactor the auth module to use JWT.\n"
            "\n"
            "### Key Facts\n"
            "- `auth/jwt.py:42` raises on missing claim\n"
            "- current `SECRET_KEY` is in `~/.her/.env`\n"
            "\n"
            "### Open Threads\n"
            "- Refresh-token rotation strategy undecided\n"
            "\n"
            "### Recalled\n"
            "- User mentioned cookies are first-party only\n"
            "\n"
            "### Discarded (for reference)\n"
            "- Discussion of the unrelated `dashboard` PR\n"
        )
        block = CuratedContext.from_curator_text(text)
        assert block.focus.startswith("Refactor the auth module")
        assert len(block.key_facts) == 2
        assert len(block.open_threads) == 1
        assert len(block.recalled) == 1
        assert len(block.discarded) == 1

    def test_accepts_h2_headers(self):
        text = (
            "## Active Focus\n"
            "Move the kanban board to SQLite WAL.\n"
            "\n"
            "## Key Facts\n"
            "- `kanban.db` currently uses `journal_mode=DELETE`\n"
        )
        block = CuratedContext.from_curator_text(text)
        assert "kanban" in block.focus.lower()
        assert len(block.key_facts) == 1

    def test_skips_empty_lines(self):
        text = (
            "### Active Focus\n"
            "\n"
            "Ship the v0.3 release.\n"
        )
        block = CuratedContext.from_curator_text(text)
        assert block.focus == "Ship the v0.3 release."

    def test_strips_bullet_markers(self):
        text = (
            "### Key Facts\n"
            "- file: `foo.py`\n"
            "* line 42\n"
            "+ branch `main`\n"
        )
        block = CuratedContext.from_curator_text(text)
        assert all(not fact.startswith(("-", "*", "+")) for fact in block.key_facts)
        assert block.key_facts[0].startswith("file:")

    def test_empty_input(self):
        assert CuratedContext.from_curator_text("").is_empty()

    def test_unrecognised_sections_ignored(self):
        text = (
            "### Active Focus\n"
            "Investigate the cron drift.\n"
            "\n"
            "### Weather\n"
            "sunny\n"  # Unknown section, should be dropped.
        )
        block = CuratedContext.from_curator_text(text)
        assert block.focus.startswith("Investigate")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

class TestRender:
    def test_renders_section_header(self):
        block = CuratedContext(focus="Refactor auth")
        rendered = block.render()
        assert rendered.startswith(SECTION_HEADER)
        assert "Refactor auth" in rendered

    def test_renders_subsections_in_order(self):
        block = CuratedContext(
            focus="Refactor auth",
            key_facts=["fact-1"],
            open_threads=["thread-1"],
            recalled=["recall-1"],
            discarded=["drop-1"],
        )
        rendered = block.render()
        # Subsections should appear in the documented order
        focus_idx = rendered.index("### Active Focus")
        facts_idx = rendered.index("### Key Facts")
        threads_idx = rendered.index("### Open Threads")
        recalled_idx = rendered.index("### Recalled")
        discarded_idx = rendered.index("### Discarded (for reference)")
        assert focus_idx < facts_idx < threads_idx < recalled_idx < discarded_idx

    def test_empty_renders_to_empty_string(self):
        assert CuratedContext().render() == ""

    def test_truncates_when_over_max_chars(self):
        huge = "x" * 5000
        block = CuratedContext(
            focus="",
            key_facts=[huge, huge, huge, huge, huge],
        )
        rendered = block.render(max_chars=2000)
        assert len(rendered) <= 2000
        # The "more trimmed" marker should be present since we cut entries.
        assert "(more, trimmed)" in rendered or "…" in rendered

    def test_drops_discarded_first_on_truncation(self):
        # The decision-relevant fields (focus, key_facts) should always survive.
        block = CuratedContext(
            focus="Keep me",
            key_facts=["keep-me-too"],
            discarded=["x" * 5000],
        )
        rendered = block.render(max_chars=2000)
        assert "Keep me" in rendered
        assert "keep-me-too" in rendered
