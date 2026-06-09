"""
Work Agent Manager — daemon thread lifecycle for the second agent.

Follows the same pattern as ``agent/background_review.py``: spawns a
second ``AIAgent`` in a daemon thread, restricted to execution-only tools.

The Work Agent (WA):
- Runs in a daemon thread, started when the main agent starts
- Blocks on ``WorkQueue.wait_for_work()`` until a task is available
- Executes tasks via ``AIAgent.chat(task.goal)`` with execution tools only
- Writes results back to the work queue
- Checks for cancellation/pause between tool calls
- Loops back to wait_for_work() after each task

Module-level singleton: one work queue + one WA thread per process.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

from agent.work_queue import WorkQueue, WorkItem, STATUS_RUNNING

logger = logging.getLogger(__name__)

# ── Module-level singleton state ─────────────────────────────────────

_work_queue: WorkQueue | None = None
_wa_thread: threading.Thread | None = None
_stop_event = threading.Event()


# ── Public API ───────────────────────────────────────────────────────

def get_queue() -> WorkQueue:
    """Get the singleton work queue (creates on first call)."""
    global _work_queue
    if _work_queue is None:
        _work_queue = WorkQueue()
    return _work_queue


def spawn_work_agent(parent_agent: Any) -> bool:
    """Start the Work Agent daemon thread.

    Must be called from the main agent's thread after the parent
    ``AIAgent`` is fully initialized.

    Args:
        parent_agent: The main ``AIAgent`` instance (CA). Used to inherit
                      credentials, provider, model, and toolset config.

    Returns:
        True if the WA was started, False if already running.
    """
    global _wa_thread

    if _wa_thread is not None and _wa_thread.is_alive():
        logger.info("work_agent: already running")
        return False

    _stop_event.clear()
    queue = get_queue()

    _wa_thread = threading.Thread(
        target=_work_agent_loop,
        args=(parent_agent, queue, _stop_event),
        name="work-agent",
        daemon=True,
    )
    _wa_thread.start()
    logger.info("work_agent: spawned daemon thread (WA)")
    return True


def stop_work_agent(timeout: float = 5.0) -> bool:
    """Signal the WA to stop and wait for it to finish.

    Args:
        timeout: Max seconds to wait for graceful shutdown.

    Returns:
        True if WA stopped, False if it's still running (timed out).
    """
    global _wa_thread

    if _wa_thread is None or not _wa_thread.is_alive():
        logger.info("work_agent: not running")
        return True

    _stop_event.set()

    # Also signal the work queue so wait_for_work() unblocks
    queue = _work_queue
    if queue is not None:
        from agent.work_queue import STATUS_CANCELLED
        running = queue.running_item()
        if running:
            queue.update(running.id, status=STATUS_CANCELLED,
                         result="WA shutting down")

    _wa_thread.join(timeout=timeout)
    if _wa_thread.is_alive():
        logger.warning("work_agent: did not stop within %ss", timeout)
        return False

    _wa_thread = None
    logger.info("work_agent: stopped")
    return True


def work_agent_status() -> dict:
    """Get current WA status for display."""
    running = _wa_thread is not None and _wa_thread.is_alive()
    queue = get_queue()
    current = queue.running_item()
    queued = queue.list(status_filter=["queued", "paused"])
    return {
        "running": running,
        "current_task": current.to_dict() if current else None,
        "queued": len(queued),
        "active": len([i for i in queued if i.status == "queued"]),
        "paused": len([i for i in queued if i.status == "paused"]),
    }


# ── WA Loop ──────────────────────────────────────────────────────────

def _work_agent_loop(
    parent_agent: Any,
    queue: WorkQueue,
    stop_event: threading.Event,
) -> None:
    """Main loop of the Work Agent daemon thread.

    Runs in a daemon thread. Creates its own ``AIAgent`` instance with
    execution-only tools, then blocks on the work queue.
    """
    from run_agent import AIAgent

    # Build the WA's AIAgent, inheriting credentials from the parent
    # (same pattern as background_review.py)
    wa_agent = AIAgent(
        model=parent_agent.model,
        provider=parent_agent.provider,
        base_url=getattr(parent_agent, "base_url", None),
        api_key=getattr(parent_agent, "api_key", None),
        api_mode=getattr(parent_agent, "api_mode", None),
        credential_pool=getattr(parent_agent, "_credential_pool", None),
        parent_session_id=parent_agent.session_id,
        max_iterations=90,
        quiet_mode=True,
        # WA gets execution-only tools (terminal, file, web, browser, code)
        # The "execution" toolset includes all of these via toolset composition.
        enabled_toolsets=["execution"],
        disabled_toolsets=getattr(parent_agent, "disabled_toolsets", None),
        skip_memory=True,          # WA doesn't touch memory
        skip_context_files=True,   # WA doesn't reload project context
        platform=getattr(parent_agent, "platform", "cli"),
    )
    wa_agent._memory_enabled = False
    wa_agent._user_profile_enabled = False

    logger.info("work_agent: WA agent created, waiting for work...")

    while not stop_event.is_set():
        # Block until a task is available (checks stop_event internally)
        task = queue.wait_for_work(stop_event=stop_event)
        if task is None:
            # stop_event was set during wait
            break

        logger.info("work_agent: starting task %s: %s", task.id, task.goal[:80])

        try:
            # Build a self-contained prompt for the WA
            wa_prompt = _build_wa_prompt(task)

            # Execute via the WA's AIAgent
            # ``chat()`` returns the final response string
            result = wa_agent.chat(wa_prompt)

            # Check if this task was cancelled/paused during execution
            current = queue.get(task.id)
            if current and current.status in ("cancelled", "paused"):
                logger.info("work_agent: task %s was %s mid-execution", task.id, current.status)
                continue

            # Report success
            queue.update(
                task.id,
                status="completed",
                result=result,
                context=f"completed by WA in {time.time() - task.started_at:.1f}s",
            )
            logger.info("work_agent: task %s completed", task.id)

        except Exception as e:
            logger.exception("work_agent: task %s failed: %s", task.id, e)
            queue.update(
                task.id,
                status="failed",
                result=f"WA error: {e}",
            )

    logger.info("work_agent: loop ended (stop_event)")


def _build_wa_prompt(task: WorkItem) -> str:
    """Build the prompt the WA receives to execute a task.

    The WA gets the goal + context as its instruction. It has full
    execution tools available (terminal, file, search, browser, codex).
    """
    parts = [
        "You are the Work Agent. Execute the following task using the tools available to you.",
        "",
        f"## Goal\n{task.goal}",
    ]
    if task.context:
        parts.extend([
            "",
            "## Context\n" + task.context,
        ])
    parts.extend([
        "",
        "When you're done, write a concise summary of what you did and the results.",
        "If the task cannot be completed, explain why.",
        "",
        "IMPORTANT: Do NOT use work_queue or delegate_task tools. Focus on execution.",
    ])
    return "\n".join(parts)
