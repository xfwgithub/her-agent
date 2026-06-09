"""
Work Queue — SQLite-backed priority queue with threading event signaling.

Manages the task queue shared between Conversation Agent (CA) and Work Agent (WA).
Thread-safe: uses Python's threading primitives + SQLite in WAL mode.

Priority levels (lower number = higher priority):
    0 = critical (interrupt current work)
    1 = urgent
    2 = normal  (default)
    3 = low

Status values:
    queued    → waiting to be picked up
    running   → currently being executed by WA
    paused    → interrupted by CA, will resume later
    completed → finished successfully
    failed    → execution error
    cancelled → removed by CA before/during execution
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --- Constants ---

DB_FILENAME = "work_queue.db"

PRIORITY_CRITICAL = 0
PRIORITY_URGENT = 1
PRIORITY_NORMAL = 2
PRIORITY_LOW = 3

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

_VALID_STATUSES = {
    STATUS_QUEUED, STATUS_RUNNING, STATUS_PAUSED,
    STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED,
}


# --- Data ---

@dataclass
class WorkItem:
    """Work Item in the work queue."""
    id: str = ""
    goal: str = ""              # What to do (WA receives this as instruction)
    context: str = ""           # Background info / progress snapshot
    priority: int = PRIORITY_NORMAL
    status: str = STATUS_QUEUED
    task_type: str = "goal"     # "goal" (LLM-driven) or "script" (direct command)
    result: str = ""            # Output from WA (completion message / error)
    toolset: str = "execution"  # Toolset for WA to use
    created_at: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> WorkItem:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# --- Queue ---

class WorkQueue:
    """Thread-safe, SQLite-backed priority work queue.

    The WA thread blocks on ``wait_for_work()`` until a queued item is
    available. CA wakes it by calling ``add()`` which sets a threading.Event.
    """

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            from her_constants import get_her_home
            db_path = Path(get_her_home()) / DB_FILENAME
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()
        self._event = threading.Event()  # Signals WA when new work arrives
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ── Connection management ──────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        """Get or create a thread-safe SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self._db_path),
                timeout=10,
                check_same_thread=False,
            )
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _init_db(self) -> None:
        """Create the work_queue table if it doesn't exist."""
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS work_queue (
                id          TEXT PRIMARY KEY,
                goal        TEXT NOT NULL,
                context     TEXT DEFAULT '',
                priority    INTEGER NOT NULL DEFAULT 2,
                status      TEXT NOT NULL DEFAULT 'queued',
                task_type   TEXT NOT NULL DEFAULT 'goal',
                result      TEXT DEFAULT '',
                toolset     TEXT DEFAULT 'execution',
                created_at  REAL NOT NULL,
                started_at  REAL DEFAULT 0,
                completed_at REAL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_wq_status_priority
            ON work_queue(status, priority)
        """)
        # Migrate: add task_type column if it doesn't exist
        try:
            conn.execute("ALTER TABLE work_queue ADD COLUMN task_type TEXT NOT NULL DEFAULT 'goal'")
        except Exception:
            pass  # Column already exists
        conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ── CRUD ────────────────────────────────────────────────────────────

    def add(
        self,
        goal: str,
        context: str = "",
        priority: int = PRIORITY_NORMAL,
        task_type: str = "goal",
        toolset: str = "execution",
    ) -> str:
        """Add a work item. Returns its ID. Wakes the WA.

        Args:
            goal: What to do.
            context: Background info for the WA.
            priority: Priority level (0=critical, 1=urgent, 2=normal, 3=low).
            task_type: "goal" (LLM-driven) or "script" (direct command).
            toolset: Which tools the WA should have access to.
        """
        item_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                """INSERT INTO work_queue (id, goal, context, priority, status,
                   task_type, toolset, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (item_id, goal, context, priority, STATUS_QUEUED,
                 task_type, toolset, now),
            )
            conn.commit()
        logger.info("work_queue: added %s (priority=%d): %s", item_id, priority, goal[:80])
        self._event.set()  # Wake WA
        return item_id

    def update(
        self,
        item_id: str,
        *,
        status: str | None = None,
        result: str | None = None,
        context: str | None = None,
        priority: int | None = None,
    ) -> bool:
        """Update fields of an existing item. Returns True if found."""
        fields = {}
        if status is not None:
            if status not in _VALID_STATUSES:
                raise ValueError(f"Invalid status: {status!r}")
            fields["status"] = status
            if status == STATUS_RUNNING:
                fields["started_at"] = time.time()
            elif status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
                fields["completed_at"] = time.time()
        if result is not None:
            fields["result"] = result
        if context is not None:
            fields["context"] = context
        if priority is not None:
            fields["priority"] = priority

        if not fields:
            return False

        set_clause = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [item_id]

        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                f"UPDATE work_queue SET {set_clause} WHERE id=?",
                values,
            )
            conn.commit()
            updated = cursor.rowcount > 0
        if updated:
            logger.info("work_queue: updated %s → status=%s", item_id, status or "unchanged")
        return updated

    def get(self, item_id: str) -> WorkItem | None:
        """Get a single item by ID."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM work_queue WHERE id=?", (item_id,)
            ).fetchone()
        if row is None:
            return None
        return WorkItem.from_dict(dict(row))

    def list(
        self,
        status_filter: str | list[str] | None = None,
        limit: int = 50,
    ) -> list[WorkItem]:
        """List items, ordered by priority then creation time."""
        with self._lock:
            conn = self._get_conn()
            if status_filter:
                if isinstance(status_filter, str):
                    status_filter = [status_filter]
                placeholders = ",".join("?" for _ in status_filter)
                rows = conn.execute(
                    f"SELECT * FROM work_queue WHERE status IN ({placeholders}) "
                    "ORDER BY priority ASC, created_at ASC LIMIT ?",
                    status_filter + [limit],
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM work_queue ORDER BY priority ASC, created_at ASC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [WorkItem.from_dict(dict(r)) for r in rows]

    def cancel(self, item_id: str, reason: str = "") -> bool:
        """Cancel a queued or running item."""
        return self.update(item_id, status=STATUS_CANCELLED, result=reason or "cancelled by CA")

    def reorder(self, item_id: str, new_priority: int) -> bool:
        """Change an item's priority."""
        return self.update(item_id, priority=new_priority)

    def pause(self, item_id: str) -> bool:
        """Pause a running item (will be resumed later)."""
        return self.update(item_id, status=STATUS_PAUSED, result="")

    def resume(self, item_id: str) -> bool:
        """Resume a paused item back to queued."""
        return self.update(item_id, status=STATUS_QUEUED)

    # ── WA coordination ────────────────────────────────────────────────

    def next(self) -> WorkItem | None:
        """Atomically claim the highest-priority queued item.

        Returns the item with status set to 'running', or None if empty.
        Thread-safe: only one WA caller will get a result.
        """
        with self._lock:
            conn = self._get_conn()
            # Find the highest-priority queued/paused item
            row = conn.execute(
                "SELECT * FROM work_queue "
                "WHERE status IN (?, ?) "
                "ORDER BY priority ASC, created_at ASC LIMIT 1",
                (STATUS_QUEUED, STATUS_PAUSED),
            ).fetchone()
            if row is None:
                return None
            item = WorkItem.from_dict(dict(row))
            # Atomically claim it
            now = time.time()
            conn.execute(
                "UPDATE work_queue SET status=?, started_at=? WHERE id=?",
                (STATUS_RUNNING, now, item.id),
            )
            conn.commit()
        item.status = STATUS_RUNNING
        item.started_at = now
        logger.info("work_queue: claimed %s (priority=%d): %s", item.id, item.priority, item.goal[:80])
        return item

    def wait_for_work(
        self,
        stop_event: threading.Event | None = None,
        poll_interval: float = 1.0,
    ) -> WorkItem | None:
        """Block until work is available or stop_event is set.

        Returns the next item, or None if stopped.
        """
        while True:
            if stop_event and stop_event.is_set():
                return None
            # Try to claim work
            item = self.next()
            if item is not None:
                return item
            # Wait for signal or timeout (so we can check stop_event)
            self._event.wait(timeout=poll_interval)
            self._event.clear()

    def clear_completed(self, max_age_hours: float = 24) -> int:
        """Delete completed/failed/cancelled items older than max_age_hours."""
        cutoff = time.time() - max_age_hours * 3600
        with self._lock:
            conn = self._get_conn()
            cursor = conn.execute(
                "DELETE FROM work_queue WHERE status IN (?, ?, ?) AND completed_at < ?",
                (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, cutoff),
            )
            conn.commit()
        count = cursor.rowcount
        if count:
            logger.info("work_queue: cleared %d stale items", count)
        return count

    def running_item(self) -> WorkItem | None:
        """Get the currently running item, if any."""
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM work_queue WHERE status=? ORDER BY started_at DESC LIMIT 1",
                (STATUS_RUNNING,),
            ).fetchone()
        if row is None:
            return None
        return WorkItem.from_dict(dict(row))
