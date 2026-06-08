"""Volatile context curation — pre-turn distillation pass.

When the main conversation's prompt grows past
``context_curation.threshold_ratio`` of the model's context window, this
module fires a background pass that asks a lightweight LLM to summarise
the current conversation with respect to the *latest* user message.
The summary is captured in :class:`agent.curated_context.CuratedContext`
and surfaced as the ``## Curated Context`` block at the tail of the
system prompt's **volatile** tier.

Design contract:
    * The curator does NOT mutate ``agent._session_messages`` and does
      NOT short-circuit any turn. It only writes
      ``agent._curated_block`` (a string) which the volatile tier picks
      up.
    * The pass is asynchronous — the main turn never waits for the
      curator. If the curator is still running when the next trigger
      fires, we just skip the new trigger (one curator at a time).
    * The curator is a single LLM call with no tool loop. The work is
      summarisation, not exploration. ``max_curator_iterations`` from
      config is reserved for a future tool-using variant; today the
      single call is the loop.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from agent.curated_context import (
    CuratedContext,
    compute_token_ratio,
    should_curate,
)

logger = logging.getLogger(__name__)


# Curator user-prompt template. Kept here (not in prompt_builder) so the
# whole curator module can be moved/disabled in isolation if needed.
_CURATOR_USER_PROMPT = """You are a context-curation assistant. A conversation has grown past a soft threshold and the main model needs a compact, high-signal summary of what matters for the LATEST user message below.

LATEST USER MESSAGE:
{latest_user_message}

PRIOR CONVERSATION (the parts the main model can already see — do not paraphrase them, distill them):
{conversation_block}

Write a structured summary in EXACTLY this sectioned format (use the headers verbatim, no extra prose before or after):

### Active Focus
[1-2 sentences: the single core question or task the user is working on right now]

### Key Facts
[Bullet list of concrete values to keep — file paths, error strings, command outputs, decisions, names, IDs. One fact per line, no narrative.]

### Open Threads
[Bullet list of unresolved items the user still expects addressed. If none, write "None."]

### Recalled
[Bullet list of external facts you are sure about from the conversation. If you did not recall anything new, write "None."]

### Discarded (for reference)
[Bullet list of items you considered dropping because they are off-focus for the Active Focus, in case the model needs to know what was filtered out.]

Hard constraints:
- Total output MUST stay under {max_tokens} tokens.
- NEVER include API keys, tokens, passwords, or credentials — replace with [REDACTED].
- Do NOT answer the user. Do NOT take any action. The summary is the entire deliverable."""


class ContextCurator:
    """Background volatile-tier curator. One instance per AIAgent."""

    def __init__(self, agent: Any) -> None:
        self._agent = agent
        self._lock = threading.Lock()
        self._inflight: Optional[threading.Thread] = None
        self._last_finished_at: float = 0.0
        self._last_status: str = "idle"  # idle | running | ok | error | skipped

    # ------------------------------------------------------------------
    # Public surface used by the conversation loop
    # ------------------------------------------------------------------
    @property
    def status(self) -> str:
        return self._last_status

    def maybe_kick(
        self,
        *,
        messages: List[Dict[str, Any]],
        latest_user_message: str,
        used_tokens: Optional[float] = None,
        context_window: Optional[float] = None,
    ) -> bool:
        """Spawn a background curator pass if the trigger conditions hold.

        Returns True if a pass was actually launched. False means the
        trigger was either unmet, the curator was already running, or
        curation is disabled.

        Non-blocking. The result lands on ``agent._curated_block`` (and
        ``agent._curated_block_at``) when the thread finishes.
        """
        if not self._enabled():
            self._last_status = "skipped"
            return False
        if not self._slot_free():
            # Another curator pass is still running; skip rather than
            # race a second fork on top of it.
            self._last_status = "skipped"
            return False

        cfg = self._curation_config()
        threshold = float(cfg.get("threshold_ratio", 0.10) or 0.0)
        if not should_curate(used_tokens, context_window, threshold, enabled=True):
            self._last_status = "skipped"
            return False

        thread = threading.Thread(
            target=self._run_curator_pass,
            args=(messages, latest_user_message, cfg),
            name="her-context-curator",
            daemon=True,
        )
        with self._lock:
            self._inflight = thread
            self._last_status = "running"
        thread.start()
        return True

    def join(self, timeout: Optional[float] = None) -> None:
        """Block until any in-flight curator pass finishes.

        Used by tests; production code never waits for the curator
        (the main turn proceeds in parallel).
        """
        thread = None
        with self._lock:
            thread = self._inflight
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------
    def _curation_config(self) -> Dict[str, Any]:
        agent = self._agent
        # The config may live in different places depending on the
        # caller. AIAgent exposes ``_config`` in some paths; the
        # CLI/gateway load it on demand. Fall back gracefully.
        cfg = getattr(agent, "_config", None)
        if not isinstance(cfg, dict):
            try:
                from her_cli.config import load_config
                cfg = load_config()
            except Exception:
                cfg = {}
        curation = cfg.get("context_curation", {}) if isinstance(cfg, dict) else {}
        return curation if isinstance(curation, dict) else {}

    def _enabled(self) -> bool:
        if not bool(self._curation_config().get("enabled", True)):
            return False
        # Subagents / fork contexts can opt out by setting this attr.
        if getattr(self._agent, "_skip_context_curation", False):
            return False
        return True

    def _slot_free(self) -> bool:
        with self._lock:
            thread = self._inflight
        return thread is None or not thread.is_alive()

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------
    def _run_curator_pass(
        self,
        messages: List[Dict[str, Any]],
        latest_user_message: str,
        cfg: Dict[str, Any],
    ) -> None:
        try:
            self._do_run(messages, latest_user_message, cfg)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("context_curator pass crashed: %s", exc, exc_info=True)
            with self._lock:
                self._last_status = "error"
                self._inflight = None
        else:
            with self._lock:
                self._last_status = "ok"
                self._inflight = None
                self._last_finished_at = time.time()

    def _do_run(
        self,
        messages: List[Dict[str, Any]],
        latest_user_message: str,
        cfg: Dict[str, Any],
    ) -> None:
        # Defensive snapshot — never read the live mutable list off-thread
        # without copying, otherwise a concurrent push from the main
        # turn could race our serialisation.
        snapshot = copy.deepcopy(messages or [])

        max_tokens = int(cfg.get("max_curated_tokens", 1200) or 1200)
        conversation_block = _serialise_messages_for_curator(snapshot)
        user_prompt = _CURATOR_USER_PROMPT.format(
            latest_user_message=(latest_user_message or "").strip() or "(no user message)",
            conversation_block=conversation_block or "(empty)",
            max_tokens=max_tokens,
        )

        # Resolve routing. "auto" + empty fields means "use whatever
        # the main turn is using" via the runtime main snapshot.
        provider = (cfg.get("provider") or "").strip() or None
        model = (cfg.get("model") or "").strip() or None
        base_url = (cfg.get("base_url") or "").strip() or None
        api_key = (cfg.get("api_key") or "").strip() or None
        timeout = float(cfg.get("timeout", 120) or 120)

        runtime_main: Optional[Dict[str, Any]] = None
        if not (provider and model):
            # Inherit the parent's main runtime so the curator hits the
            # same provider/auth as the main turn (and ideally the same
            # prefix cache for the system prompt).
            runtime_main = _safe_main_runtime(self._agent)

        # Local import — auxiliary_client is heavy.
        from agent.auxiliary_client import call_llm

        response = call_llm(
            task="context_curation",
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            main_runtime=runtime_main,
            messages=[
                {"role": "system", "content": _system_prompt_text()},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.0,
            timeout=timeout,
        )
        text = _extract_response_text(response)
        if not text:
            logger.debug("context_curator: empty response, leaving block unset")
            return

        curated = CuratedContext.from_curator_text(text)
        if curated.is_empty():
            logger.debug("context_curator: parsed to empty block, skipping")
            return

        rendered = curated.render(max_chars=max(400, max_tokens * 4))
        # Land on the agent so the system-prompt builder can pick it up.
        # Use a simple attribute set; concurrent readers see the last
        # complete value (Python attribute assignment is atomic for
        # strings on the GIL).
        try:
            self._agent._curated_block = rendered
            self._agent._curated_block_at = time.time()
        except Exception:
            # If the agent refuses the attribute (some test doubles),
            # don't crash the thread.
            pass

    # ------------------------------------------------------------------
    # Diagnostics — used by tests and `her diagnostics`.
    # ------------------------------------------------------------------
    def diagnostics(self) -> Dict[str, Any]:
        with self._lock:
            inflight = self._inflight is not None and self._inflight.is_alive()
        return {
            "status": self._last_status,
            "inflight": inflight,
            "last_finished_at": self._last_finished_at,
            "has_block": bool(getattr(self._agent, "_curated_block", "")),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _system_prompt_text() -> str:
    """Read the curator's system prompt, lazy-imported to avoid a load cycle."""
    try:
        from agent.prompt_builder import CURATOR_AGENT_SYSTEM_PROMPT
        return CURATOR_AGENT_SYSTEM_PROMPT
    except Exception:
        # Fallback so the curator never crashes the main turn on import hiccups.
        return (
            "You are the Hermes context curator. Your only job is to write a "
            "compact, structured summary of the conversation that helps the "
            "main model focus on the user's latest message. Do not answer the "
            "user. Do not call tools. Produce the sectioned summary as the "
            "entire response."
        )


def _safe_main_runtime(agent: Any) -> Optional[Dict[str, Any]]:
    """Return the parent agent's main runtime dict, defensively."""
    getter = getattr(agent, "_current_main_runtime", None)
    if not callable(getter):
        return None
    try:
        runtime = getter()
    except Exception:
        return None
    if not isinstance(runtime, dict):
        return None
    # Trim to the keys the auxiliary client actually consumes.
    return {
        "model": runtime.get("model", ""),
        "provider": runtime.get("provider", ""),
        "base_url": runtime.get("base_url", ""),
        "api_key": runtime.get("api_key", ""),
        "api_mode": runtime.get("api_mode", ""),
    }


def _serialise_messages_for_curator(messages: List[Dict[str, Any]]) -> str:
    """Render the conversation to a compact text block for the curator.

    Skips internal roles (``tool``) whose payloads the curator doesn't
    need — only the high-level user/assistant narrative. Truncates
    individual entries so the curator prompt itself stays small.
    """
    lines: List[str] = []
    char_budget = 12_000  # ~3k tokens for the curator input
    used = 0
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role not in {"user", "assistant", "system"}:
            continue
        content = msg.get("content")
        text = content if isinstance(content, str) else _flatten_content(content)
        if not text:
            continue
        # Keep tool-call summaries compact
        if role == "assistant" and msg.get("tool_calls"):
            names = [
                (tc.get("function") or {}).get("name", "?")
                for tc in (msg.get("tool_calls") or [])
            ]
            text = (text + f"\n[tool_calls: {', '.join(names)}]").strip()
        snippet = text.strip()
        if len(snippet) > 1200:
            snippet = snippet[:1100] + " …[truncated]"
        line = f"[{role}] {snippet}"
        if used + len(line) > char_budget:
            lines.append("…[earlier turns omitted]")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _flatten_content(content: Any) -> str:
    """OpenAI sometimes returns content as a list of typed blocks."""
    if not isinstance(content, list):
        return str(content or "")
    parts: List[str] = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") in {"text", "output_text"}:
                parts.append(str(block.get("text") or ""))
            elif block.get("type") == "image_url":
                parts.append("[image]")
            else:
                parts.append(f"[{block.get('type', 'block')}]")
    return " ".join(p for p in parts if p)


def _extract_response_text(response: Any) -> str:
    """Pull the text out of an OpenAI/Anthropic-shaped response, defensively."""
    if response is None:
        return ""
    # OpenAI ChatCompletion: response.choices[0].message.content
    choices = getattr(response, "choices", None)
    if choices:
        try:
            message = choices[0].message
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return _flatten_content(content)
        except (AttributeError, IndexError, TypeError):
            pass
    # Anthropic / dict shape
    if isinstance(response, dict):
        for key in ("content", "text", "completion"):
            if key in response and isinstance(response[key], str):
                return response[key]
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text
    return ""


__all__ = ["ContextCurator", "compute_token_ratio", "should_curate"]
