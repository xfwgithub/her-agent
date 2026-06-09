"""
Work Agent Manager — daemon thread lifecycle for the second agent.

v2: Script-only execution + true preemption via interrupt watcher.

Follows the same pattern as ``agent/background_review.py``: spawns a
second ``AIAgent`` in a daemon thread, restricted to execution-only tools.

v2 features:
- Script task type: goals detected as shell commands bypass LLM entirely
- Interrupt watcher: sub-thread monitors DB for pause/cancel signals,
  preempts the WA mid-execution
- Progress awareness: WA writes status updates to the work queue

Module-level singleton: one work queue + one WA thread per process.
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Optional

from agent.work_queue import (
    WorkQueue, WorkItem,
    STATUS_RUNNING, STATUS_CANCELLED, STATUS_PAUSED,
)

logger = logging.getLogger(__name__)

# ── Module-level singleton state ─────────────────────────────────────

_work_queue: WorkQueue | None = None
_wa_thread: threading.Thread | None = None
_stop_event = threading.Event()

# Shared reference for script task subprocess (so watcher can kill it)
_script_proc: subprocess.Popen | None = None
_script_proc_lock = threading.Lock()


# ── Script detection ─────────────────────────────────────────────────

_SCRIPT_PREFIXES = ("run:", "cmd:", ">", "!")

def detect_task_type(goal: str) -> str:
    """Classify a task goal as 'script' or 'goal'.

    Heuristics:
    - Starts with a known prefix (``run:``, ``cmd:``, ``>``, ``!``) → script
    - Single short line containing shell operators (``|``, ``&&``, ``||``) → script
    - Everything else → goal (LLM-driven)
    """
    goal_stripped = goal.strip()

    # Prefix match
    for prefix in _SCRIPT_PREFIXES:
        if goal_stripped.startswith(prefix):
            return "script"

    # Single line with shell operators
    if "\n" not in goal_stripped and len(goal_stripped) < 200:
        for op in (" | ", " && ", " || ", " ; ", " 2>", " > "):
            if op in goal_stripped:
                return "script"

    return "goal"


def _strip_prefix(goal: str) -> str:
    """Strip ''run:'', ''cmd:'', ''>'', ''!'' prefix from a script goal."""
    for prefix in _SCRIPT_PREFIXES:
        if goal.strip().startswith(prefix):
            return goal.strip()[len(prefix):].strip()
    return goal.strip()


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
        running = queue.running_item()
        if running:
            queue.update(running.id, status=STATUS_CANCELLED,
                         result="WA shutting down")

    _kill_script_proc()

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


# ── Script execution ─────────────────────────────────────────────────

def _kill_script_proc() -> None:
    """Kill the currently running script subprocess, if any."""
    global _script_proc
    with _script_proc_lock:
        if _script_proc is not None:
            try:
                _script_proc.kill()
                _script_proc.wait(timeout=3)
            except Exception:
                pass
            _script_proc = None


def _execute_script_task(
    task: WorkItem,
    queue: WorkQueue,
    stop_event: threading.Event,
) -> str:
    """Execute a script-type task directly via subprocess (no LLM).

    The command is run via ``subprocess.Popen`` with shell=True.
    Can be interrupted by killing the subprocess.

    Returns the stdout+stderr output as the result string.
    """
    global _script_proc

    command = _strip_prefix(task.goal)
    logger.info("work_agent: script execution: %s", command[:120])

    _kill_script_proc()  # Safety: ensure no previous proc is running

    with _script_proc_lock:
        _script_proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    # Wait for completion with periodic checks
    output_lines = []
    poll_interval = 0.2
    max_wait = 600  # 10 minutes max for a script
    waited = 0.0

    try:
        while waited < max_wait:
            if stop_event.is_set() or _should_abort(task.id, queue):
                _kill_script_proc()
                return "[interrupted]"

            ret = _script_proc.poll()
            if ret is not None:
                # Read remaining stdout
                try:
                    stdout, _ = _script_proc.communicate(timeout=5)
                    if stdout:
                        output_lines.append(stdout)
                except Exception:
                    pass
                break

            # Read available stdout incrementally
            try:
                line = _script_proc.stdout.readline() if _script_proc.stdout else ""
                if line:
                    output_lines.append(line)
            except Exception:
                pass

            time.sleep(poll_interval)
            waited += poll_interval

        else:
            # Timeout
            _kill_script_proc()
            return f"[timeout after {max_wait}s]"

    except Exception as e:
        _kill_script_proc()
        return f"[script error: {e}]"

    output = "".join(output_lines)
    if output:
        return output.strip()
    return f"Command completed (exit code: {ret})"


def _should_abort(task_id: str, queue: WorkQueue) -> bool:
    """Check if the currently executing task should abort.

    Checks the DB for pause/cancel signals.
    Called between execution steps so the WA responds promptly.
    """
    item = queue.get(task_id)
    if item is None:
        return True  # Task deleted
    return item.status in (STATUS_CANCELLED, STATUS_PAUSED)


# ── Interrupt watcher ────────────────────────────────────────────────

def _start_interrupt_watcher(
    task: WorkItem,
    queue: WorkQueue,
    wa_agent: Any,
    stop_event: threading.Event,
) -> threading.Thread:
    """Start a watcher thread that monitors the DB for interrupt signals.

    When a task is cancelled/paused, the watcher:
    1. Calls ``wa_agent.interrupt()`` to stop the current LLM loop
    2. Updates the task status in the queue

    The watcher runs for the duration of the task and exits when the
    task is no longer in ``running`` state or a stop is requested.
    """
    watcher_stop = threading.Event()

    def _watcher():
        while not watcher_stop.is_set() and not stop_event.is_set():
            item = queue.get(task.id)
            if item is None or item.status not in (STATUS_RUNNING,):
                break  # Task finished or no longer running

            if item.status in (STATUS_CANCELLED, STATUS_PAUSED):
                logger.info(
                    "work_agent: interrupt watcher firing for %s (status=%s)",
                    task.id, item.status,
                )
                # Interrupt the WA agent's LLM loop
                try:
                    wa_agent.interrupt(f"Task {item.status} by CA")
                except Exception:
                    logger.exception("work_agent: interrupt() failed")
                break

            watcher_stop.wait(timeout=0.5)

    t = threading.Thread(target=_watcher, name=f"wa-watcher-{task.id[:8]}", daemon=True)
    t.start()
    return t


# ── WA Loop ──────────────────────────────────────────────────────────

def _work_agent_loop(
    parent_agent: Any,
    queue: WorkQueue,
    stop_event: threading.Event,
) -> None:
    """Main loop of the Work Agent daemon thread.

    Creates its own ``AIAgent`` instance with execution-only tools, then
    blocks on the work queue. Each task is routed to either:
    - Script execution (direct subprocess, no LLM)
    - Goal execution (LLM-driven via AIAgent.chat())

    An interrupt watcher thread runs alongside each task to enable
    preemption from the CA side.
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
        enabled_toolsets=["execution"],
        disabled_toolsets=getattr(parent_agent, "disabled_toolsets", None),
        skip_memory=True,
        skip_context_files=True,
        platform=getattr(parent_agent, "platform", "cli"),
    )
    wa_agent._memory_enabled = False
    wa_agent._user_profile_enabled = False

    logger.info("work_agent: WA agent created, waiting for work...")

    while not stop_event.is_set():
        # Block until a task is available
        task = queue.wait_for_work(stop_event=stop_event)
        if task is None:
            break

        logger.info("work_agent: starting task %s (type=%s, pri=%d): %s",
                    task.id, task.task_type, task.priority, task.goal[:80])

        try:
            if task.task_type == "script":
                # ── Script path: direct subprocess, no LLM ──────────
                command_display = _strip_prefix(task.goal)[:80]
                queue.update(task.id, context=f"script: {command_display}")
                result = _execute_script_task(task, queue, stop_event)
                final_status = _final_status_for(task.id, queue)

                if final_status == STATUS_PAUSED:
                    resume_ctx = f"[paused] Script was interrupted: {command_display}"
                    queue.update(
                        task.id,
                        status=STATUS_PAUSED,
                        context=resume_ctx,
                        result=result[:500],
                    )
                else:
                    queue.update(
                        task.id,
                        status=final_status,
                        result=result,
                        context=f"script completed in {time.time() - task.started_at:.1f}s",
                    )
                logger.info("work_agent: script task %s → %s", task.id, final_status)

            else:
                # ── Goal path: LLM-driven via AIAgent ───────────────
                wa_prompt = _build_wa_prompt(task)
                queue.update(task.id, context="initializing...")

                # Start the interrupt watcher for preemption
                watcher = _start_interrupt_watcher(task, queue, wa_agent, stop_event)

                # Progress callback: writes per-tool-call progress to the queue
                def _make_progress_cb(task_id: str, q: WorkQueue):
                    last_progress = {}
                    def _cb(event: str, tool_name: str, preview: str, args: dict) -> None:
                        nonlocal last_progress
                        if event == "tool.started":
                            now = time.time()
                            # Throttle: update at most every 2s to avoid DB spam
                            if last_progress.get("time", 0) + 2.0 > now:
                                return
                            last_progress = {"time": now}
                            q.update(task_id, context=preview[:200])
                    return _cb

                wa_agent.tool_progress_callback = _make_progress_cb(task.id, queue)

                # Clear any stale interrupt before starting
                wa_agent.clear_interrupt()

                # Execute via the WA's AIAgent
                result = wa_agent.chat(wa_prompt)

                # Clear progress callback
                wa_agent.tool_progress_callback = None

                # Wait for watcher to finish (it exits when task is done)
                watcher.join(timeout=3)

                # Determine final status
                final_status = _final_status_for(task.id, queue)

                if final_status == STATUS_PAUSED:
                    # Save resume context: the last progress + partial result
                    last_progress = queue.get(task.id).context if queue.get(task.id) else ""
                    resume_ctx = f"[paused] Last: {last_progress[:100]}\nPartial: {result[:500]}"
                    queue.update(
                        task.id,
                        status=STATUS_PAUSED,
                        context=resume_ctx,
                        result="",
                    )
                    logger.info("work_agent: goal task %s paused — context saved", task.id)

                else:
                    queue.update(
                        task.id,
                        status=final_status,
                        result=result,
                        context=f"completed by WA in {time.time() - task.started_at:.1f}s",
                    )
                logger.info("work_agent: goal task %s → %s", task.id, final_status)

        except Exception as e:
            logger.exception("work_agent: task %s failed: %s", task.id, e)
            final_status = _final_status_for(task.id, queue)
            if final_status not in (STATUS_CANCELLED, STATUS_PAUSED):
                final_status = "failed"
            queue.update(
                task.id,
                status=final_status,
                result=f"WA error: {e}",
            )

    logger.info("work_agent: loop ended (stop_event)")


def _final_status_for(task_id: str, queue: WorkQueue) -> str:
    """Determine the final status after task execution.

    If CA paused/cancelled during execution, honour that.
    Otherwise mark as completed.
    """
    item = queue.get(task_id)
    if item is None:
        return STATUS_CANCELLED
    if item.status in (STATUS_CANCELLED, STATUS_PAUSED):
        return item.status
    return "completed"


# ── Prompt builder ──────────────────────────────────────────────────

def _build_wa_prompt(task: WorkItem) -> str:
    """Build the prompt the WA receives to execute a task.

    The WA gets the goal + context as its instruction. It has full
    execution tools available (terminal, file, search, browser, codex).

    If the task context starts with ``[paused]``, the prompt includes
    resume instructions so the WA can continue where it left off.
    """
    parts = [
        "You are the Work Agent. Execute the following task using the tools available to you.",
        "",
        f"## Goal\n{task.goal}",
    ]

    if task.context:
        if task.context.startswith("[paused]"):
            # Resume from a previously paused task
            resume_info = task.context[len("[paused]"):].strip()
            parts.extend([
                "",
                "## ⚠️ Resume — This task was previously paused",
                "You were working on this before. Here's what happened so far:",
                resume_info,
                "",
                "Continue from where you left off. Do NOT redo work that's already done.",
            ])
        else:
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
