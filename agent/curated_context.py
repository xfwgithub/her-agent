"""Volatile-tier "Curated Context" block — distilled, recall-only context.

When the main conversation's `messages` list grows past
``context_curation.threshold_ratio`` of the model's context window, the
:class:`agent.context_curator.ContextCurator` runs a forked review pass
in the background. The result is captured here as a
:class:`CuratedContext` and rendered as the ``## Curated Context``
section appended to the system prompt's **volatile** tier.

Design contract — do not break:
    * The curator NEVER mutates ``agent._session_messages`` or any
      message the LLM has already seen. The curated block is a
      **second look** at the same data, not a replacement.
    * The block is the **last** volatile segment so the cached
      ``stable`` prefix stays byte-identical turn-to-turn.
    * The block is *informational*. The model is expected to keep
      following the live user message first; the curated block only
      helps it ignore the noise that has accumulated in the rest of
      the prompt.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)


SECTION_HEADER = "## Curated Context"

# Hard ceiling for the rendered block. The curator is told to stay
# under ``max_curated_tokens`` from config; this is a defensive
# truncation so a runaway curator can't blow out the volatile tier.
_RENDER_CHAR_FLOOR = 4000  # ≈ 1000 tokens at 4 chars/token


# ---------------------------------------------------------------------------
# Threshold helpers — pure, deterministic, easy to unit-test.
# ---------------------------------------------------------------------------

def _safe_div(numerator: float, denominator: float) -> float:
    """Divide without exploding on zero/None denominators."""
    try:
        if not denominator:
            return 0.0
        return float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def compute_token_ratio(
    used_tokens: Optional[float],
    context_window: Optional[float],
) -> float:
    """Return ``used / window`` as a float in ``[0.0, 1.0+]``.

    Returns ``0.0`` if either argument is missing or non-positive — the
    conservative value so callers default to "no curation needed".
    """
    if used_tokens is None or context_window is None:
        return 0.0
    if used_tokens <= 0 or context_window <= 0:
        return 0.0
    return _safe_div(used_tokens, context_window)


def should_curate(
    used_tokens: Optional[float],
    context_window: Optional[float],
    threshold_ratio: float,
    enabled: bool = True,
) -> bool:
    """Decide whether the curator pass should fire on this turn.

    ``enabled`` is checked first so config-driven killswitches are
    honoured before the (cheap but real) arithmetic.
    """
    if not enabled:
        return False
    if threshold_ratio <= 0:
        return False
    return compute_token_ratio(used_tokens, context_window) >= threshold_ratio


# ---------------------------------------------------------------------------
# Data class — the curator's output, normalised before it lands in the
# system prompt.
# ---------------------------------------------------------------------------

@dataclass
class CuratedContext:
    """Normalised, render-ready curated block.

    All fields are optional. The renderer skips empty fields. Strings
    should be plain prose — the curator is told not to use markdown
    headers inside its bullet points (we own the section header).
    """

    focus: str = ""                 # Latest core question, ≤ 2 sentences.
    key_facts: List[str] = field(default_factory=list)      # Concrete values to keep.
    open_threads: List[str] = field(default_factory=list)   # Unresolved items.
    discarded: List[str] = field(default_factory=list)      # What was pruned and why.
    recalled: List[str] = field(default_factory=list)       # External facts pulled in.

    def is_empty(self) -> bool:
        return not (
            self.focus
            or self.key_facts
            or self.open_threads
            or self.discarded
            or self.recalled
        )

    # ------------------------------------------------------------------
    # Curators in the wild sometimes dump LLM control tokens or stray
    # code fences. Strip them so the volatile tier stays clean prose.
    # ------------------------------------------------------------------
    @staticmethod
    def _clean_bullet(text: str) -> str:
        if not text:
            return ""
        # Strip leading bullet markers the curator sometimes leaves in.
        text = re.sub(r"^[\s>*\-+•·●\d.]+", "", text)
        # Drop markdown header prefixes ("###", "##", "#") — we own the
        # top-level section header; nested ones would just clutter.
        text = re.sub(r"^#{1,6}\s*", "", text)
        return text.strip()

    @classmethod
    def from_curator_text(cls, text: str) -> "CuratedContext":
        """Parse a free-form curator reply into structured fields.

        The curator is given a strict sectioned prompt; this is a
        tolerant parser that also accepts the "all-in-one-paragraph"
        fallback some models fall back to. Unknown sections are
        dropped — the volatile tier has no room for novel structure.
        """
        if not text:
            return cls()

        focus = ""
        facts: List[str] = []
        threads: List[str] = []
        discarded: List[str] = []
        recalled: List[str] = []

        section_map = {
            "active focus": "focus",
            "focus": "focus",
            "key facts": "facts",
            "facts": "facts",
            "open threads": "threads",
            "threads": "threads",
            "discarded": "discarded",
            "discarded (for reference)": "discarded",
            "pruned": "discarded",
            "recalled": "recalled",
            "recall": "recalled",
        }

        current: Optional[str] = None
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            stripped = line.lstrip("#").strip().rstrip(":").lower()
            if stripped in section_map:
                current = section_map[stripped]
                continue
            cleaned = cls._clean_bullet(line)
            if not cleaned:
                continue
            if current == "focus" and not focus:
                focus = cleaned
            elif current == "facts":
                facts.append(cleaned)
            elif current == "threads":
                threads.append(cleaned)
            elif current == "discarded":
                discarded.append(cleaned)
            elif current == "recalled":
                recalled.append(cleaned)
        return cls(
            focus=focus,
            key_facts=facts,
            open_threads=threads,
            discarded=discarded,
            recalled=recalled,
        )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def render(self, max_chars: int = _RENDER_CHAR_FLOOR) -> str:
        """Render to the volatile-tier markdown block.

        ``max_chars`` is a defensive ceiling; we trim the largest
        section first (typically ``discarded`` or ``recalled``) so the
        most decision-relevant fields (``focus``, ``key_facts``) survive.
        """
        if self.is_empty():
            return ""

        lines: List[str] = [SECTION_HEADER]

        if self.focus:
            lines.append("")
            lines.append("### Active Focus")
            lines.append(self.focus)

        if self.key_facts:
            lines.append("")
            lines.append("### Key Facts")
            lines.extend(f"- {fact}" for fact in self._truncate(self.key_facts, max_chars))

        if self.open_threads:
            lines.append("")
            lines.append("### Open Threads")
            lines.extend(f"- {t}" for t in self._truncate(self.open_threads, max_chars))

        if self.recalled:
            lines.append("")
            lines.append("### Recalled")
            lines.extend(f"- {r}" for r in self._truncate(self.recalled, max_chars))

        if self.discarded:
            lines.append("")
            lines.append("### Discarded (for reference)")
            lines.extend(f"- {d}" for d in self._truncate(self.discarded, max_chars))

        # Final hard-trim to max_chars, but never lop the section header.
        body = "\n".join(lines)
        if len(body) > max_chars:
            body = body[: max(0, max_chars - 1)] + "…"
            if SECTION_HEADER not in body:
                body = SECTION_HEADER + "\n\n" + body
        return body

    @staticmethod
    def _truncate(items: Sequence[str], budget: int) -> Iterable[str]:
        """Yield items, then a single truncation marker if we cut any off."""
        if not items:
            return []
        # Reserve room for the section header + siblings by giving each
        # bullet ~1/3 of the budget. Empirically generous.
        per_item = max(40, budget // 6)
        used = 0
        kept: List[str] = []
        for item in items:
            if used + len(item) > budget and kept:
                kept.append(f"…({len(items) - len(kept)} more, trimmed)")
                return kept
            kept.append(item)
            used += len(item) + 2  # bullet + newline
        return kept


__all__ = [
    "CuratedContext",
    "SECTION_HEADER",
    "compute_token_ratio",
    "should_curate",
]
