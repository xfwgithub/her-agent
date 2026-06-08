"""Tests for the volatile context-curator worker.

The curator is mostly a stateful wrapper around a single LLM call. We
mock ``agent.auxiliary_client.call_llm`` and verify (a) the trigger
threshold logic, (b) the curator doesn't block the main turn, (c) the
rendered block lands on the agent and is picked up by the volatile-tier
builder.
"""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.context_curator import ContextCurator
from agent.curated_context import SECTION_HEADER
from agent.system_prompt import build_system_prompt_parts


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_agent(**overrides):
    """A minimal agent stand-in exposing the surface the curator reads."""
    base = dict(
        _config={"context_curation": {"enabled": True, "threshold_ratio": 0.10}},
        _curated_block=None,
        _curated_block_at=0.0,
        _skip_context_curation=False,
        model="test-model",
        provider="test-provider",
    )
    base.update(overrides)
    agent = SimpleNamespace(**base)
    return agent


# A realistic-looking curator reply (the kind the LLM would produce).
_CURATOR_REPLY = (
    "### Active Focus\n"
    "Get the new context-curator pass landed.\n"
    "\n"
    "### Key Facts\n"
    "- File: `agent/context_curator.py`\n"
    "- Trigger: `threshold_ratio` default 0.10\n"
    "\n"
    "### Open Threads\n"
    "- Decide whether to also surface `Recalled` facts in tests\n"
    "\n"
    "### Recalled\n"
    "None.\n"
    "\n"
    "### Discarded (for reference)\n"
    "- Earlier `kanban` grooming discussion (off-focus for the curator task)\n"
)


def _mock_response(text: str):
    """Build an OpenAI ChatCompletion-ish SimpleNamespace."""
    msg = SimpleNamespace(content=text, role="assistant")
    choice = SimpleNamespace(message=msg, index=0, finish_reason="stop")
    return SimpleNamespace(choices=[choice], model="test", usage=None)


# ---------------------------------------------------------------------------
# Trigger logic
# ---------------------------------------------------------------------------

class TestMaybeKickThreshold:
    def test_below_threshold_does_not_kick(self):
        agent = _make_agent()
        curator = ContextCurator(agent)
        kicked = curator.maybe_kick(
            messages=[{"role": "user", "content": "hi"}],
            latest_user_message="hi",
            used_tokens=500,       # 5% of window
            context_window=10_000,
        )
        assert kicked is False
        assert curator.status == "skipped"

    def test_at_threshold_kicks(self):
        agent = _make_agent()
        curator = ContextCurator(agent)
        # Patch the worker so we don't actually call the LLM.
        with patch.object(curator, "_do_run", return_value=None):
            kicked = curator.maybe_kick(
                messages=[{"role": "user", "content": "hi"}] * 50,
                latest_user_message="hi",
                used_tokens=1000,    # 10% of window
                context_window=10_000,
            )
        assert kicked is True
        curator.join(timeout=1.0)
        assert curator.status in {"ok", "running"}

    def test_above_threshold_kicks(self):
        agent = _make_agent()
        curator = ContextCurator(agent)
        with patch.object(curator, "_do_run", return_value=None):
            kicked = curator.maybe_kick(
                messages=[{"role": "user", "content": "x"}] * 100,
                latest_user_message="hi",
                used_tokens=5000,    # 50% of window
                context_window=10_000,
            )
        assert kicked is True
        curator.join(timeout=1.0)

    def test_disabled_in_config_does_not_kick(self):
        agent = _make_agent(_config={"context_curation": {"enabled": False, "threshold_ratio": 0.10}})
        curator = ContextCurator(agent)
        kicked = curator.maybe_kick(
            messages=[],
            latest_user_message="hi",
            used_tokens=9000,
            context_window=10_000,
        )
        assert kicked is False
        assert curator.status == "skipped"

    def test_agent_opt_out_does_not_kick(self):
        agent = _make_agent(_skip_context_curation=True)
        curator = ContextCurator(agent)
        kicked = curator.maybe_kick(
            messages=[],
            latest_user_message="hi",
            used_tokens=9000,
            context_window=10_000,
        )
        assert kicked is False

    def test_no_window_does_not_kick(self):
        agent = _make_agent()
        curator = ContextCurator(agent)
        kicked = curator.maybe_kick(
            messages=[],
            latest_user_message="hi",
            used_tokens=1000,
            context_window=None,
        )
        assert kicked is False


class TestMaybeKickConcurrency:
    def test_skip_when_curator_already_running(self):
        agent = _make_agent()
        curator = ContextCurator(agent)
        # Fake an in-flight thread.
        fake_thread = MagicMock()
        fake_thread.is_alive.return_value = True
        curator._inflight = fake_thread
        kicked = curator.maybe_kick(
            messages=[],
            latest_user_message="hi",
            used_tokens=9000,
            context_window=10_000,
        )
        assert kicked is False
        assert curator.status == "skipped"


# ---------------------------------------------------------------------------
# End-to-end (mocked LLM)
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_block_lands_on_agent(self):
        agent = _make_agent()
        curator = ContextCurator(agent)
        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=_mock_response(_CURATOR_REPLY),
        ) as _call:
            kicked = curator.maybe_kick(
                messages=[{"role": "user", "content": "build it"}] * 200,
                latest_user_message="build the curator",
                used_tokens=2000,
                context_window=10_000,
            )
        assert kicked is True
        curator.join(timeout=2.0)
        assert agent._curated_block is not None
        assert SECTION_HEADER in agent._curated_block
        assert "context-curator pass landed" in agent._curated_block

    def test_empty_response_leaves_block_unset(self):
        agent = _make_agent()
        curator = ContextCurator(agent)
        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=_mock_response(""),
        ):
            curator.maybe_kick(
                messages=[{"role": "user", "content": "x"}] * 200,
                latest_user_message="hi",
                used_tokens=2000,
                context_window=10_000,
            )
        curator.join(timeout=2.0)
        # Block was never assigned; either None or a previously-set value.
        assert agent._curated_block is None

    def test_curator_does_not_crash_on_llm_error(self):
        agent = _make_agent()
        curator = ContextCurator(agent)
        with patch(
            "agent.auxiliary_client.call_llm",
            side_effect=RuntimeError("provider down"),
        ):
            curator.maybe_kick(
                messages=[{"role": "user", "content": "x"}] * 200,
                latest_user_message="hi",
                used_tokens=2000,
                context_window=10_000,
            )
        curator.join(timeout=2.0)
        # Error path: status moves to "error", agent still has no block.
        assert curator.status == "error"
        assert agent._curated_block is None

    def test_routes_to_main_runtime_when_no_explicit_provider(self):
        agent = _make_agent()
        # Simulate ``_current_main_runtime`` on the agent.
        agent._current_main_runtime = lambda: {
            "model": "parent-model",
            "provider": "parent-provider",
            "base_url": "https://parent.example/v1",
            "api_key": "sk-parent",
            "api_mode": "chat_completions",
        }
        curator = ContextCurator(agent)
        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=_mock_response(_CURATOR_REPLY),
        ) as call_mock:
            curator.maybe_kick(
                messages=[{"role": "user", "content": "x"}] * 200,
                latest_user_message="hi",
                used_tokens=2000,
                context_window=10_000,
            )
        curator.join(timeout=2.0)
        # Verify the curator asked the auxiliary client to use the
        # parent runtime as fallback (i.e. ``main_runtime`` was set).
        kwargs = call_mock.call_args.kwargs
        assert kwargs["main_runtime"]["model"] == "parent-model"
        assert kwargs["main_runtime"]["provider"] == "parent-provider"


# ---------------------------------------------------------------------------
# Integration with the system-prompt builder
# ---------------------------------------------------------------------------

class TestSystemPromptIntegration:
    def test_curated_block_appears_in_volatile_tier(self):
        agent = SimpleNamespace(
            load_soul_identity=False,
            skip_context_files=False,
            valid_tool_names=[],
            _task_completion_guidance=False,
            _tool_use_enforcement=False,
            _environment_probe=False,
            _kanban_worker_guidance="",
            _memory_store=None,
            _memory_manager=None,
            model="",
            provider="",
            platform="",
            pass_session_id=False,
            session_id="",
            _curated_block=(
                f"{SECTION_HEADER}\n\n"
                "### Active Focus\n"
                "Refactor the auth module."
            ),
        )
        parts = build_system_prompt_parts(agent)
        assert SECTION_HEADER in parts["volatile"]
        assert "Refactor the auth module" in parts["volatile"]

    def test_no_curated_block_omits_section(self):
        agent = SimpleNamespace(
            load_soul_identity=False,
            skip_context_files=False,
            valid_tool_names=[],
            _task_completion_guidance=False,
            _tool_use_enforcement=False,
            _environment_probe=False,
            _kanban_worker_guidance="",
            _memory_store=None,
            _memory_manager=None,
            model="",
            provider="",
            platform="",
            pass_session_id=False,
            session_id="",
            _curated_block=None,
        )
        parts = build_system_prompt_parts(agent)
        assert SECTION_HEADER not in parts["volatile"]

    def test_empty_curated_block_omits_section(self):
        agent = SimpleNamespace(
            load_soul_identity=False,
            skip_context_files=False,
            valid_tool_names=[],
            _task_completion_guidance=False,
            _tool_use_enforcement=False,
            _environment_probe=False,
            _kanban_worker_guidance="",
            _memory_store=None,
            _memory_manager=None,
            model="",
            provider="",
            platform="",
            pass_session_id=False,
            session_id="",
            _curated_block="   \n\n  ",
        )
        parts = build_system_prompt_parts(agent)
        assert SECTION_HEADER not in parts["volatile"]


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def test_initial_state(self):
        agent = _make_agent()
        curator = ContextCurator(agent)
        d = curator.diagnostics()
        assert d["status"] == "idle"
        assert d["inflight"] is False
        assert d["has_block"] is False

    def test_state_after_successful_pass(self):
        agent = _make_agent()
        curator = ContextCurator(agent)
        with patch(
            "agent.auxiliary_client.call_llm",
            return_value=_mock_response(_CURATOR_REPLY),
        ):
            curator.maybe_kick(
                messages=[{"role": "user", "content": "x"}] * 200,
                latest_user_message="hi",
                used_tokens=2000,
                context_window=10_000,
            )
        curator.join(timeout=2.0)
        d = curator.diagnostics()
        assert d["status"] == "ok"
        assert d["has_block"] is True
        assert d["last_finished_at"] > 0
