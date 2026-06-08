"""Tests for the Nous-her-3/4 non-agentic warning detector.

Prior to this check, the warning fired on any model whose name contained
``"her"`` anywhere (case-insensitive). That false-positived on unrelated
local Modelfiles such as ``her-brain:qwen3-14b-ctx16k`` — a tool-capable
Qwen3 wrapper that happens to live under the "her" tag namespace.

``is_nous_her_non_agentic`` should only match the actual Nous Research
her-3 / her-4 chat family.
"""

from __future__ import annotations

import pytest

from her_cli.model_switch import (
    _HER_MODEL_WARNING,
    _check_her_model_warning,
    is_nous_her_non_agentic,
)


@pytest.mark.parametrize(
    "model_name",
    [
        "NousResearch/her-3-Llama-3.1-70B",
        "NousResearch/her-3-Llama-3.1-405B",
        "her-3",
        "her-3",
        "her-4",
        "her-4-405b",
        "her_4_70b",
        "openrouter/her3:70b",
        "openrouter/nousresearch/her-4-405b",
        "NousResearch/her3",
        "her-3.1",
    ],
)
def test_matches_real_nous_her_chat_models(model_name: str) -> None:
    assert is_nous_her_non_agentic(model_name), (
        f"expected {model_name!r} to be flagged as Nous her 3/4"
    )
    assert _check_her_model_warning(model_name) == _HER_MODEL_WARNING


@pytest.mark.parametrize(
    "model_name",
    [
        # Kyle's local Modelfile — qwen3:14b under a custom tag
        "her-brain:qwen3-14b-ctx16k",
        "her-brain:qwen3-14b-ctx32k",
        "her-honcho:qwen3-8b-ctx8k",
        # Plain unrelated models
        "qwen3:14b",
        "qwen3-coder:30b",
        "qwen2.5:14b",
        "claude-opus-4-6",
        "anthropic/claude-sonnet-4.5",
        "gpt-5",
        "openai/gpt-4o",
        "google/gemini-2.5-flash",
        "deepseek-chat",
        # Non-chat her models we don't warn about
        "her-llm-2",
        "her2-pro",
        "nous-her-2-mistral",
        # Edge cases
        "",
        "her",  # bare "her" isn't the 3/4 family
        "her-brain",
        "brain-her-3-impostor",  # "3" not preceded by /: boundary
    ],
)
def test_does_not_match_unrelated_models(model_name: str) -> None:
    assert not is_nous_her_non_agentic(model_name), (
        f"expected {model_name!r} NOT to be flagged as Nous her 3/4"
    )
    assert _check_her_model_warning(model_name) == ""


def test_none_like_inputs_are_safe() -> None:
    assert is_nous_her_non_agentic("") is False
    # Defensive: the helper shouldn't crash on None-ish falsy input either.
    assert _check_her_model_warning("") == ""
