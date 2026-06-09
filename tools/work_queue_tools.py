"""
Work Queue Tools — for the Conversation Agent (CA) to manage the work queue.

These tools are registered in the ``work`` toolset and are available only
to the main agent (CA). The Work Agent (WA) does NOT have access to these.

Tools:
    work_assign    — Add a task to the work queue
    work_status    — Show current queue status
    work_cancel    — Cancel a queued/running task
    work_reorder   — Change a task's priority
    work_pause     — Pause a running task
    work_resume    — Resume a paused task
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.work_queue import (
    WorkItem,
    PRIORITY_CRITICAL, PRIORITY_URGENT, PRIORITY_NORMAL, PRIORITY_LOW,
)
from agent.work_manager import get_queue, work_agent_status, detect_task_type

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────

_PRIORITY_MAP = {
    "critical": PRIORITY_CRITICAL,
    "urgent": PRIORITY_URGENT,
    "normal": PRIORITY_NORMAL,
    "low": PRIORITY_LOW,
}

_PRIORITY_NAMES = {v: k for k, v in _PRIORITY_MAP.items()}


def _parse_priority(s: str) -> int:
    """Convert string priority to int. Defaults to normal."""
    return _PRIORITY_MAP.get(s.strip().lower(), PRIORITY_NORMAL)


def _item_summary(item: WorkItem) -> dict:
    """Compact dict for display."""
    return {
        "id": item.id,
        "goal": item.goal[:120],
        "priority": _PRIORITY_NAMES.get(item.priority, str(item.priority)),
        "status": item.status,
        "context": item.context[:200] if item.context else "",
        "progress": item.context[:200] if item.context and item.status == "running" else "",
        "created": f"{item.created_at:.0f}",
        "started": f"{item.started_at:.0f}" if item.started_at else "",
        "completed": f"{item.completed_at:.0f}" if item.completed_at else "",
        "result": item.result[:500] if item.result else "",
    }


def _json_dumps(obj: Any) -> str:
    """Safe JSON serialization."""
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


# ── Tool handlers ───────────────────────────────────────────────────


def work_assign_tool(
    goal: str,
    context: str = "",
    priority: str = "normal",
    task_type: str = "",
) -> str:
    """Assign a task to the Work Agent.

    Args:
        goal: What to do (free-form instruction for the WA).
        context: Background information or progress from previous work.
        priority: Priority level — critical (0), urgent (1), normal (2), low (3).
        task_type: "goal" (LLM-driven), "script" (direct command, bypasses LLM),
                   or "" to auto-detect based on goal content.

    Returns:
        The assigned task ID and current queue state.
    """
    queue = get_queue()
    prio = _parse_priority(priority)
    task_type_resolved = task_type if task_type else detect_task_type(goal)
    item_id = queue.add(goal=goal, context=context, priority=prio, task_type=task_type_resolved)
    queued = queue.list(status_filter=["queued", "paused", "running"])

    lines = [
        f"✅ Task assigned: `{item_id}`",
        f"  Goal: {goal[:200]}",
        f"  Type: {task_type_resolved}",
        f"  Priority: {priority}",
        f"  Queue: {len(queued)} item(s) — "
        f"{len([i for i in queued if i.status == 'queued'])} waiting, "
        f"{len([i for i in queued if i.status == 'running'])} running"
    ]
    item = queued[-1] if queued else None
    if item and item.status == "running":
        lines.append(f"  Currently working on: `{item.goal[:80]}`")
    return "\n".join(lines)


def work_status_tool() -> str:
    """Show the current state of the work queue and Work Agent.

    Returns:
        A structured overview of queued, running, and completed tasks.
    """
    status = work_agent_status()
    queue = get_queue()

    running = queue.running_item()
    queued = queue.list(status_filter=["queued", "paused"])
    recent = queue.list(
        status_filter=["completed", "failed", "cancelled"],
        limit=10,
    )

    lines = [
        "## Work Agent Status",
        f"  Running: {'✅ yes' if status['running'] else '❌ no'}",
        "",
    ]

    if running:
        lines.append("### Currently Running")
        s = _item_summary(running)
        lines.append(f"  ID: `{s['id']}`")
        lines.append(f"  Goal: {s['goal']}")
        lines.append(f"  Priority: {s['priority']}")
        if s.get('progress'):
            lines.append(f"  Progress: {s['progress']}")
        lines.append(f"  Started: {s['started']}")
        lines.append("")

    if queued:
        lines.append(f"### Queued ({len(queued)} items)")
        for i, item in enumerate(queued):
            s = _item_summary(item)
            progress = f" — {s['progress']}" if s.get('progress') else ""
            lines.append(f"  {i+1}. `{s['id']}` [{s['priority']}] {s['goal']}{progress}")
        lines.append("")

    if recent:
        lines.append(f"### Recent ({len(recent)} items)")
        for item in recent[:5]:
            s = _item_summary(item)
            status_icon = {
                "completed": "✅", "failed": "❌", "cancelled": "🚫",
            }.get(s['status'], "•")
            lines.append(f"  {status_icon} `{s['id']}` {s['goal'][:80]} → {s['status']}")
        lines.append("")

    return "\n".join(lines) if len(lines) > 2 else "Work queue is empty."


def work_cancel_tool(task_id: str, reason: str = "") -> str:
    """Cancel a queued or running task.

    Args:
        task_id: The ID of the task to cancel.
        reason: Optional explanation for cancellation.
    """
    queue = get_queue()
    item = queue.get(task_id)
    if item is None:
        return f"❌ Task `{task_id}` not found."

    # If running, we can only mark it — WA will notice on next tool call
    was_running = item.status == "running"
    ok = queue.cancel(task_id, reason=reason)
    if not ok:
        return f"❌ Failed to cancel `{task_id}`."

    if was_running:
        return f"🚫 Task `{task_id}` will be stopped. It was in progress."
    return f"✅ Task `{task_id}` cancelled."


def work_reorder_tool(task_id: str, priority: str) -> str:
    """Change a task's priority.

    Args:
        task_id: The ID of the task to reorder.
        priority: New priority — critical (0), urgent (1), normal (2), low (3).
    """
    queue = get_queue()
    item = queue.get(task_id)
    if item is None:
        return f"❌ Task `{task_id}` not found."

    prio = _parse_priority(priority)
    ok = queue.reorder(task_id, prio)
    if not ok:
        return f"❌ Failed to reorder `{task_id}`."
    return f"✅ Task `{task_id}` priority changed to **{priority}**."


def work_pause_tool(task_id: str) -> str:
    """Pause a running task. It will be resumed later.

    Args:
        task_id: The ID of the running task to pause.
    """
    queue = get_queue()
    item = queue.get(task_id)
    if item is None:
        return f"❌ Task `{task_id}` not found."
    if item.status != "running":
        return f"⚠️ Task `{task_id}` is not running (status: {item.status})."

    ok = queue.pause(task_id)
    if not ok:
        return f"❌ Failed to pause `{task_id}`."
    return f"⏸️ Task `{task_id}` paused. Use `work_resume` to continue."


def work_resume_tool(task_id: str) -> str:
    """Resume a paused task back to the queue.

    Args:
        task_id: The ID of the paused task to resume.
    """
    queue = get_queue()
    item = queue.get(task_id)
    if item is None:
        return f"❌ Task `{task_id}` not found."
    if item.status != "paused":
        return f"⚠️ Task `{task_id}` is not paused (status: {item.status})."

    ok = queue.resume(task_id)
    if not ok:
        return f"❌ Failed to resume `{task_id}`."
    return f"▶️ Task `{task_id}` resumed and queued for execution."


# ── Tool registration ───────────────────────────────────────────────

_TOOL_SCHEMA = {
    "work_assign": {
        "description": "Assign a task to the Work Agent. The WA will execute it using terminal, file, search, and browser tools.",
        "parameters": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "string",
                    "description": "What to do — free-form instruction for the Work Agent.",
                },
                "context": {
                    "type": "string",
                    "description": "Background information or progress from previous work. Include file paths, partial results, etc.",
                    "default": "",
                },
                "priority": {
                    "type": "string",
                    "enum": ["critical", "urgent", "normal", "low"],
                    "description": "Task priority. critical=0 (interrupts current work), urgent=1, normal=2 (default), low=3.",
                    "default": "normal",
                },
                "task_type": {
                    "type": "string",
                    "enum": ["goal", "script", ""],
                    "description": "'goal' (LLM-driven, default when empty), 'script' (direct command, bypasses LLM), or '' to auto-detect.",
                    "default": "",
                },
            },
            "required": ["goal"],
        },
    },
    "work_status": {
        "description": "Show the current state of the work queue and Work Agent: what's running, queued, paused, and recently completed.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    "work_cancel": {
        "description": "Cancel a queued or running task. If running, the WA will stop at the next tool call boundary.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The ID of the task to cancel.",
                },
                "reason": {
                    "type": "string",
                    "description": "Optional explanation for cancellation.",
                    "default": "",
                },
            },
            "required": ["task_id"],
        },
    },
    "work_reorder": {
        "description": "Change a task's priority. Higher-priority tasks are executed first.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The ID of the task to reorder.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["critical", "urgent", "normal", "low"],
                    "description": "New priority level.",
                },
            },
            "required": ["task_id", "priority"],
        },
    },
    "work_pause": {
        "description": "Pause a running task. It will be paused at the next tool call boundary, and can be resumed later with work_resume.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The ID of the running task to pause.",
                },
            },
            "required": ["task_id"],
        },
    },
    "work_resume": {
        "description": "Resume a paused task back to the queue for execution. The WA will pick it up on the next available slot.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "The ID of the paused task to resume.",
                },
            },
            "required": ["task_id"],
        },
    },
}

_HANDLERS = {
    "work_assign": work_assign_tool,
    "work_status": work_status_tool,
    "work_cancel": work_cancel_tool,
    "work_reorder": work_reorder_tool,
    "work_pause": work_pause_tool,
    "work_resume": work_resume_tool,
}


def _register_work_tools() -> None:
    """Register all work queue tools into the registry."""
    from tools.registry import registry

    for name, schema in _TOOL_SCHEMA.items():
        handler = _HANDLERS[name]
        registry.register(
            name=name,
            toolset="work",
            schema=schema,
            handler=handler,
            description=schema.get("description", ""),
        )


_register_work_tools()
