#!/usr/bin/env python3
"""
Memory Tool Module - Persistent Curated Memory

Provides file-backed memory that persists across sessions. Two stores:

  MEMORY.md — structured activity log (agent writes, YAML format)
    Tracks what the user has been doing in three time tiers:
      daily:     last 7 days (per-day, date-keyed)
      last_week: the week before the last 7 days (one summary)
      last_month: the month before that (one summary)
    The agent only sets the current day's activity; auto-consolidation
    on load ages older entries out of ``daily`` into ``last_week``.

  USER.md — structured user profile (user edits, YAML format)
    Read-only through the tool. The user edits this file directly.

Both are injected into the system prompt as a frozen snapshot at session start.
Mid-session writes to MEMORY.md are durable but do NOT change the system prompt
(preserves prefix cache). Snapshot refreshes on next session start.

Character limits (not tokens) because char counts are model-independent.
"""

import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from her_constants import get_her_home
from typing import Dict, Any, List, Optional

from utils import atomic_replace

import fcntl
import yaml

logger = logging.getLogger(__name__)

MEMORY_CHAR_LIMIT = 2200
USER_CHAR_LIMIT = 256

# Max daily entries kept before oldest ones roll up into last_week
MAX_DAILY_ENTRIES = 7


def get_memory_dir() -> Path:
    """Return the profile-scoped memories directory."""
    return get_her_home() / "memories"


# ---------------------------------------------------------------------------
# Threat scanning
# ---------------------------------------------------------------------------
from tools.threat_patterns import first_threat_message as _first_threat_message


def _scan_memory_content(content: str) -> Optional[str]:
    return _first_threat_message(content, scope="strict")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_MEMORY_YAML = """\
daily: {}
last_week: ''
last_month: ''
"""


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _week_range_str(dates: List[str]) -> str:
    """Return a human-friendly week range like '06-01 ~ 06-07'."""
    if not dates:
        return ""
    sorted_dates = sorted(dates)
    start = sorted_dates[0][5:]  # strip year
    end = sorted_dates[-1][5:]
    return f"{start} ~ {end}"


# ---------------------------------------------------------------------------
# YAML I/O for structured memory files
# ---------------------------------------------------------------------------

def _read_yaml_file(path: Path) -> Optional[Dict[str, Any]]:
    """Read a YAML file and return parsed dict. Returns None on failure/missing."""
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, IOError):
        return None
    if not raw.strip():
        return None
    try:
        data = yaml.safe_load(raw)
        if isinstance(data, dict):
            return data
    except yaml.YAMLError:
        pass
    return None


def _write_yaml_file(path: Path, data: Dict[str, Any]) -> None:
    """Write a dict as YAML to path, using atomic temp-file + rename."""
    content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False).strip()
    if content:
        content += "\n"
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), suffix=".tmp", prefix=".mem_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            atomic_replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except (OSError, IOError) as e:
        raise RuntimeError(f"Failed to write {path}: {e}")


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------

class MemoryStore:
    """
    Manages two independent, file-backed memory stores.

    - MEMORY.md (``target="memory"``): YAML activity log (daily / last_week / last_month).
      The agent sets the current day via ``action='add'``. Auto-consolidation on load.
    - USER.md  (``target="user"``):  YAML user profile. Read-only through tool.
    """

    def __init__(self, memory_char_limit: int = MEMORY_CHAR_LIMIT, user_char_limit: int = USER_CHAR_LIMIT):
        # MEMORY.md — stored as raw dict from YAML
        self._memory_data: Dict[str, Any] = {}
        # USER.md — stored as single-entry list (raw YAML text)
        self.user_entries: List[str] = []
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        # Frozen snapshot for system prompt -- set once at load_from_disk()
        self._system_prompt_snapshot: Dict[str, str] = {"memory": "", "user": ""}

    # -- Disk I/O ------------------------------------------------------------

    def load_from_disk(self):
        """Load MEMORY.md and USER.md from disk, capture frozen snapshot."""
        mem_dir = get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)

        # MEMORY.md
        mem_data = _read_yaml_file(mem_dir / "MEMORY.md")
        if mem_data is None:
            mem_data = yaml.safe_load(_DEFAULT_MEMORY_YAML)
        self._memory_data = self._consolidate(mem_data)
        self._write_memory_file()  # persist consolidation immediately
        sanitized_memory = self._sanitize_memory_for_snapshot(self._memory_data)

        # USER.md
        self.user_entries = self._read_user_file(mem_dir / "USER.md")
        sanitized_user = self._sanitize_entries_for_snapshot(self.user_entries, "USER.md")

        self._system_prompt_snapshot = {
            "memory": self._render_memory_block(sanitized_memory),
            "user": self._render_user_block(sanitized_user),
        }

    def save_to_disk(self, target: str):
        get_memory_dir().mkdir(parents=True, exist_ok=True)
        if target == "user":
            self._write_user_file(self._path_for(target), self.user_entries)
        else:
            self._write_memory_file()

    # -- Consolidation -------------------------------------------------------

    @staticmethod
    def _consolidate(data: Dict[str, Any]) -> Dict[str, Any]:
        """Auto-age daily entries: keep newest MAX_DAILY_ENTRIES, roll up older ones.

        Rules:
          1. Ensure ``daily`` key exists (default empty dict).
          2. Ensure ``last_week`` and ``last_month`` keys exist (default empty string).
          3. If daily has more than MAX_DAILY_ENTRIES entries, the oldest
             surplus entries are rolled into ``last_week``.
          4. Does NOT auto-consolidate last_week → last_month (the agent may
             update last_month explicitly via replace).
        """
        out = dict(data)
        out.setdefault("daily", {})
        out.setdefault("last_week", "")
        out.setdefault("last_month", "")

        daily = out["daily"]
        if not isinstance(daily, dict):
            logger.warning("MEMORY.md 'daily' is not a dict, resetting.")
            daily = {}
            out["daily"] = daily

        dates = sorted(daily.keys(), reverse=True)
        if len(dates) <= MAX_DAILY_ENTRIES:
            return out

        # Entries beyond the newest MAX_DAILY_ENTRIES are surplus
        keep = set(dates[:MAX_DAILY_ENTRIES])
        surplus_dates = [d for d in reversed(dates) if d not in keep]

        # Build a summary of surplus entries
        surplus_lines = []
        for d in surplus_dates:
            text = daily.get(d, "").strip()
            if text:
                surplus_lines.append(f"{d}: {text}")
            del daily[d]

        if surplus_lines:
            old_week = out.get("last_week", "").strip()
            new_text = "; ".join(surplus_lines)
            out["last_week"] = (new_text + " | " + old_week) if old_week else new_text

        return out

    # -- Sanitization for snapshot -------------------------------------------

    @staticmethod
    def _sanitize_memory_for_snapshot(data: Dict[str, Any]) -> Dict[str, Any]:
        """Scan memory YAML values for threats, replace flagged ones.

        Scans daily entry text, last_week, and last_month values.
        Returns a copy of the dict with blocked entries replaced by placeholders.
        """
        from tools.threat_patterns import scan_for_threats

        out = {}
        out["last_week"] = data.get("last_week", "")
        out["last_month"] = data.get("last_month", "")
        out["daily"] = {}

        for date_str, text in data.get("daily", {}).items():
            if not text or text.startswith("[BLOCKED:"):
                out["daily"][date_str] = text
                continue
            findings = scan_for_threats(text, scope="strict")
            if findings:
                logger.warning("MEMORY.md entry %s blocked: %s", date_str, ", ".join(findings))
                out["daily"][date_str] = (
                    f"[BLOCKED: daily entry {date_str} contained threat pattern(s): "
                    f"{', '.join(findings)}. Removed from system prompt.]"
                )
            else:
                out["daily"][date_str] = text

        for field in ("last_week", "last_month"):
            text = data.get(field, "")
            if text and not text.startswith("[BLOCKED:"):
                findings = scan_for_threats(text, scope="strict")
                if findings:
                    logger.warning("MEMORY.md %s blocked: %s", field, ", ".join(findings))
                    out[field] = (
                        f"[BLOCKED: {field} contained threat pattern(s): "
                        f"{', '.join(findings)}. Removed from system prompt.]"
                    )

        return out

    @staticmethod
    def _sanitize_entries_for_snapshot(entries: List[str], filename: str) -> List[str]:
        from tools.threat_patterns import scan_for_threats

        sanitized: List[str] = []
        for entry in entries:
            if not entry or entry.startswith("[BLOCKED:"):
                sanitized.append(entry)
                continue
            findings = scan_for_threats(entry, scope="strict")
            if findings:
                logger.warning(
                    "Memory entry from %s blocked at load time: %s",
                    filename, ", ".join(findings),
                )
                sanitized.append(
                    f"[BLOCKED: {filename} entry contained threat pattern(s): "
                    f"{', '.join(findings)}. Removed from system prompt; "
                    f"use memory(action=read) to inspect and memory(action=remove) "
                    f"to delete the original.]"
                )
            else:
                sanitized.append(entry)
        return sanitized

    # -- File helpers --------------------------------------------------------

    @staticmethod
    def _path_for(target: str) -> Path:
        mem_dir = get_memory_dir()
        if target == "user":
            return mem_dir / "USER.md"
        return mem_dir / "MEMORY.md"

    def _reload_target(self, target: str) -> None:
        """Re-read a target from disk into in-memory state."""
        path = self._path_for(target)
        if target == "user":
            self.user_entries = self._read_user_file(path)
        else:
            data = _read_yaml_file(path)
            if data is not None:
                self._memory_data = self._consolidate(data)
                self._write_memory_file()

    @staticmethod
    @contextmanager
    def _file_lock(path: Path):
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except (OSError, IOError):
                pass
            fd.close()

    def _write_memory_file(self) -> None:
        """Persist current _memory_data to disk as YAML."""
        path = self._path_for("memory")
        # Only keep the three known keys when writing
        out = {
            "daily": self._memory_data.get("daily", {}),
            "last_week": self._memory_data.get("last_week", ""),
            "last_month": self._memory_data.get("last_month", ""),
        }
        _write_yaml_file(path, out)

    # -- USER.md YAML I/O ----------------------------------------------------

    @staticmethod
    def _read_user_file(path: Path) -> List[str]:
        """Read USER.md, validate as YAML dict, return as single-entry list."""
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, IOError):
            return []
        if not raw.strip():
            return []
        try:
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                logger.warning("USER.md root is not a mapping. Ignoring.")
                return []
        except yaml.YAMLError as e:
            logger.warning("USER.md invalid YAML: %s. Ignoring.", e)
            return []
        return [raw.strip()]

    @staticmethod
    def _write_user_file(path: Path, entries: List[str]) -> None:
        content = entries[0].strip() + "\n" if entries else ""
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), suffix=".tmp", prefix=".mem_"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())
                atomic_replace(tmp_path, path)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (OSError, IOError) as e:
            raise RuntimeError(f"Failed to write user profile {path}: {e}")

    # -- Actions -------------------------------------------------------------

    def read(self, target: str) -> Dict[str, Any]:
        """Return structured content of the specified store."""
        if target == "user":
            raw = self.user_entries[0] if self.user_entries else ""
            current = len(raw)
            limit = self.user_char_limit
            pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
            try:
                data = yaml.safe_load(raw) if raw else {}
                if isinstance(data, dict):
                    return {
                        "success": True,
                        "target": target,
                        "profile": data,
                        "usage": f"{pct}% — {current:,}/{limit:,} chars",
                    }
            except Exception:
                pass
            return {
                "success": True,
                "target": target,
                "profile_raw": raw,
                "usage": f"{pct}% — {current:,}/{limit:,} chars",
            }

        # memory target
        daily = dict(self._memory_data.get("daily", {}))
        today = _today_str()
        if today in daily and daily[today]:
            has_today = daily[today]
        else:
            has_today = None

        current = self._memory_char_count()
        limit = self.memory_char_limit
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        return {
            "success": True,
            "target": target,
            "daily": daily,
            "today": today,
            "today_entry": has_today,
            "last_week": self._memory_data.get("last_week", ""),
            "last_month": self._memory_data.get("last_month", ""),
            "entry_count": len(daily),
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
        }

    def add(self, target: str, content: str, date: str = None) -> Dict[str, Any]:
        """Add an activity entry for a specific date or section. target='memory' only.

        Args:
            target: Must be "memory".
            content: Summary text.
            date: Date in YYYY-MM-DD format, or "last_week", or "last_month".
                   Defaults to today if omitted.
        """
        if target == "user":
            return {"success": False, "error": "User profile is read-only. Edit USER.md directly."}

        content = content.strip()
        if not content:
            return {"success": False, "error": "Content cannot be empty."}

        scan_error = _scan_memory_content(content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            # Determine where to write
            key = (date or _today_str()).strip()

            if key in ("last_week", "last_month"):
                self._memory_data[key] = content
                self._write_memory_file()
                return self._memory_success(f"{key} updated.")

            # It's a daily entry — validate YYYY-MM-DD format loosely
            if not self._valid_date_key(key):
                return {
                    "success": False,
                    "error": f"Invalid date '{key}'. Use YYYY-MM-DD format, 'last_week', or 'last_month'.",
                }

            daily = self._memory_data.setdefault("daily", {})

            # Check memory char limit
            test_daily = dict(daily)
            test_daily[key] = content
            test_data = {
                "daily": test_daily,
                "last_week": self._memory_data.get("last_week", ""),
                "last_month": self._memory_data.get("last_month", ""),
            }
            total = self._yaml_char_count(test_data)
            if total > self.memory_char_limit:
                return {
                    "success": False,
                    "error": f"Memory would exceed {self.memory_char_limit:,} char limit ({total:,} chars). Remove or consolidate older entries first.",
                    "usage": f"{total:,}/{self.memory_char_limit:,}",
                }

            is_update = key in daily and bool(daily[key])
            daily[key] = content
            self._memory_data["daily"] = daily
            self._write_memory_file()

            label = "today" if key == _today_str() else key
            msg = f"{label} activity updated." if is_update else f"{label} activity recorded."
            return self._memory_success(msg)

    @staticmethod
    def _valid_date_key(key: str) -> bool:
        """Basic YYYY-MM-DD format check."""
        parts = key.split("-")
        if len(parts) != 3:
            return False
        y, m, d = parts
        return len(y) == 4 and len(m) == 2 and len(d) == 2 and all(p.isdigit() for p in parts)

    def replace(self, target: str, old_text: str, new_content: str) -> Dict[str, Any]:
        """Replace content for a date or summary section. target='memory' only."""
        if target == "user":
            return {"success": False, "error": "User profile is read-only. Edit USER.md directly."}
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}
        if not new_content:
            return {"success": False, "error": "new_content is required."}

        scan_error = _scan_memory_content(new_content)
        if scan_error:
            return {"success": False, "error": scan_error}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            # Try matching as a date key in daily
            daily = self._memory_data.get("daily", {})
            if old_text in daily:
                # Check char limit
                test_daily = dict(daily)
                test_daily[old_text] = new_content
                test_data = {
                    "daily": test_daily,
                    "last_week": self._memory_data.get("last_week", ""),
                    "last_month": self._memory_data.get("last_month", ""),
                }
                total = self._yaml_char_count(test_data)
                if total > self.memory_char_limit:
                    return {
                        "success": False,
                        "error": f"Replacement would exceed {self.memory_char_limit:,} char limit.",
                    }
                daily[old_text] = new_content
                self._memory_data["daily"] = daily
                self._write_memory_file()
                return self._memory_success(f"Entry for {old_text} replaced.")

            # Try matching as a section name
            if old_text in ("last_week", "last_month"):
                if old_text in self._memory_data:
                    test_data = dict(self._memory_data)
                    test_data[old_text] = new_content
                    total = self._yaml_char_count(test_data)
                    if total > self.memory_char_limit:
                        return {
                            "success": False,
                            "error": f"Replacement would exceed {self.memory_char_limit:,} char limit.",
                        }
                    self._memory_data[old_text] = new_content
                    self._write_memory_file()
                    return self._memory_success(f"{old_text} updated.")

            # Try substring match in daily entry text
            matches = [(d, t) for d, t in daily.items() if old_text in t]
            if matches:
                if len(matches) > 1:
                    return {
                        "success": False,
                        "error": f"Multiple daily entries matched '{old_text}'. Use a date (YYYY-MM-DD) as old_text to target a specific day.",
                        "matches": [f"{d}: {t[:60]}..." for d, t in matches],
                    }
                date_key = matches[0][0]
                test_daily = dict(daily)
                test_daily[date_key] = new_content
                test_data = {
                    "daily": test_daily,
                    "last_week": self._memory_data.get("last_week", ""),
                    "last_month": self._memory_data.get("last_month", ""),
                }
                total = self._yaml_char_count(test_data)
                if total > self.memory_char_limit:
                    return {
                        "success": False,
                        "error": f"Replacement would exceed {self.memory_char_limit:,} char limit.",
                    }
                daily[date_key] = new_content
                self._memory_data["daily"] = daily
                self._write_memory_file()
                return self._memory_success(f"Entry for {date_key} replaced.")

            return {"success": False, "error": f"No entry or section matched '{old_text}'."}

    def remove(self, target: str, old_text: str) -> Dict[str, Any]:
        """Remove a daily entry or reset a summary section. target='memory' only."""
        if target == "user":
            return {"success": False, "error": "User profile is read-only. Edit USER.md directly."}
        old_text = old_text.strip()
        if not old_text:
            return {"success": False, "error": "old_text cannot be empty."}

        with self._file_lock(self._path_for(target)):
            self._reload_target(target)

            daily = self._memory_data.get("daily", {})

            # Try exact date match
            if old_text in daily:
                del daily[old_text]
                self._memory_data["daily"] = daily
                self._write_memory_file()
                return self._memory_success(f"Entry for {old_text} removed.")

            # Try section name
            if old_text in ("last_week", "last_month") and old_text in self._memory_data:
                self._memory_data[old_text] = ""
                self._write_memory_file()
                return self._memory_success(f"{old_text} cleared.")

            # Try substring in entry text
            matches = [(d, t) for d, t in daily.items() if old_text in t]
            if matches:
                if len(matches) > 1:
                    return {
                        "success": False,
                        "error": f"Multiple entries matched '{old_text}'. Use a date (YYYY-MM-DD) as old_text to target a specific day.",
                        "matches": [f"{d}: {t[:60]}..." for d, t in matches],
                    }
                date_key = matches[0][0]
                del daily[date_key]
                self._memory_data["daily"] = daily
                self._write_memory_file()
                return self._memory_success(f"Entry for {date_key} removed.")

            return {"success": False, "error": f"No entry matched '{old_text}'."}

    # -- System prompt -------------------------------------------------------

    def format_for_system_prompt(self, target: str) -> Optional[str]:
        block = self._system_prompt_snapshot.get(target, "")
        return block if block else None

    # -- Rendering -----------------------------------------------------------

    def _memory_success(self, message: str = None) -> Dict[str, Any]:
        current = self._memory_char_count()
        limit = self.memory_char_limit
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
        resp: Dict[str, Any] = {
            "success": True,
            "target": "memory",
            "usage": f"{pct}% — {current:,}/{limit:,} chars",
            "entry_count": len(self._memory_data.get("daily", {})),
        }
        if message:
            resp["message"] = message
        return resp

    def _memory_char_count(self) -> int:
        return self._yaml_char_count(self._memory_data)

    @staticmethod
    def _yaml_char_count(data: Dict[str, Any]) -> int:
        daily = data.get("daily", {})
        total = 0
        for d, t in daily.items():
            if t:
                total += len(d) + len(str(t)) + 2  # "date: text"
        lw = data.get("last_week", "")
        lm = data.get("last_month", "")
        if lw:
            total += len(str(lw)) + 10  # "last_week: "
        if lm:
            total += len(str(lm)) + 11  # "last_month: "
        return total

    def _render_memory_block(self, data: Dict[str, Any]) -> str:
        """Render the three-tier activity log for system prompt injection."""
        daily = data.get("daily", {}) or {}
        last_week = data.get("last_week", "") or ""
        last_month = data.get("last_month", "") or ""

        if not daily and not last_week and not last_month:
            return ""

        current = self._memory_char_count()
        limit = self.memory_char_limit
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        lines = []
        separator = "═" * 46
        header = f"RECENT ACTIVITY LOG [{pct}% — {current:,}/{limit:,} chars]"
        lines.append(separator)
        lines.append(header)
        lines.append(separator)

        # Last 7 days
        if daily:
            sorted_dates = sorted(daily.keys(), reverse=True)
            lines.append("")
            lines.append("📅 最近7天:")
            for d in sorted_dates:
                text = daily.get(d, "")
                if text:
                    lines.append(f"  {d}  {text}")
                else:
                    lines.append(f"  {d}  (no record)")

        # Previous week
        if last_week:
            lines.append("")
            lines.append("📆 一周前:")
            lines.append(f"  {last_week}")

        # Last month
        if last_month:
            lines.append("")
            lines.append("📚 一月前:")
            lines.append(f"  {last_month}")

        return "\n".join(lines)

    def _render_user_block(self, entries: List[str]) -> str:
        """Render USER.md profile for system prompt injection."""
        if not entries:
            return ""

        raw = entries[0] if entries else ""
        current = len(raw)
        limit = self.user_char_limit
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0

        header = f"USER PROFILE (who the user is) [{pct}% — {current:,}/{limit:,} chars]"

        try:
            data = yaml.safe_load(raw) if raw else {}
            if isinstance(data, dict) and data:
                lines = [header]
                lines.append("═" * 46)
                for key, value in data.items():
                    if isinstance(value, list):
                        items = "\n".join(f"  - {v}" for v in value)
                        lines.append(f"{key}:\n{items}")
                    elif isinstance(value, dict):
                        items = "\n".join(f"  {k}: {v}" for k, v in value.items())
                        lines.append(f"{key}:\n{items}")
                    else:
                        lines.append(f"{key}: {value}")
                return "\n".join(lines)
        except Exception:
            pass

        separator = "═" * 46
        return f"{separator}\n{header}\n{separator}\n{raw}"


# ---------------------------------------------------------------------------
# Tool entry point
# ---------------------------------------------------------------------------

def memory_tool(
    action: str,
    target: str = "memory",
    content: str = None,
    old_text: str = None,
    date: str = None,
    store: Optional[MemoryStore] = None,
) -> str:
    """Dispatch to MemoryStore methods. Returns JSON string."""
    if store is None:
        return tool_error("Memory is not available. It may be disabled in config or this environment.", success=False)

    if target not in {"memory", "user"}:
        return tool_error(f"Invalid target '{target}'. Use 'memory' or 'user'.", success=False)

    if action == "read":
        result = store.read(target)
    elif action == "add":
        if target == "user":
            return json.dumps({
                "success": False,
                "error": "User profile is read-only. Edit USER.md directly.",
            }, ensure_ascii=False)
        if not content:
            return tool_error("Content is required for 'add' action.", success=False)
        result = store.add(target, content, date=date)
    elif action == "replace":
        if target == "user":
            return json.dumps({
                "success": False,
                "error": "User profile is read-only. Edit USER.md directly.",
            }, ensure_ascii=False)
        if not old_text:
            return tool_error("old_text is required for 'replace' action.", success=False)
        if not content:
            return tool_error("content is required for 'replace' action.", success=False)
        result = store.replace(target, old_text, content)
    elif action == "remove":
        if target == "user":
            return json.dumps({
                "success": False,
                "error": "User profile is read-only. Edit USER.md directly.",
            }, ensure_ascii=False)
        if not old_text:
            return tool_error("old_text is required for 'remove' action.", success=False)
        result = store.remove(target, old_text)
    else:
        return tool_error(f"Unknown action '{action}'. Use: read, add, replace, remove", success=False)

    return json.dumps(result, ensure_ascii=False)


def check_memory_requirements() -> bool:
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================

MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "Persistent memory that survives across sessions. Two targets with different purposes:\n\n"
        "TARGET='memory' — Activity Log (STRUCTURED YAML, WRITABLE)\n"
        "  Records what the user has been doing in three time tiers:\n"
        "    • daily:     last 7 days (per-day summaries, date-keyed)\n"
        "    • last_week: the week before the last 7 days (one summary)\n"
        "    • last_month: the month before last_week (one summary)\n"
        "  ACTIONS:\n"
        "    add(content=..., date=...) — Record activity for a specific date.\n"
        "                          date='YYYY-MM-DD' for a past day,\n"
        "                          date='last_week' or date='last_month' for summaries,\n"
        "                          omit date for today.\n"
        "    read                — Return the full structured log.\n"
        "    replace(old_text, content) — Update a specific date's entry (use YYYY-MM-DD as old_text)\n"
        "                          or update last_week / last_month sections.\n"
        "    remove(old_text)    — Delete a daily entry by date or clear a summary section.\n\n"
        "TARGET='user' — User Profile (STRUCTURED YAML, READ-ONLY)\n"
        "  The user's identity, preferences, and fixed attributes. Only the user edits USER.md.\n"
        "  ACTIONS: read only. add/replace/remove are rejected.\n\n"
        "GENERAL GUIDANCE:\n"
        "  • For target='memory': save what the user worked on at the end of each session.\n"
        "    Keep summaries concise (1-2 lines per day). Do NOT save trivial details.\n"
        "  • For target='user': ONLY read — never attempt to write.\n"
        "  • Older daily entries (>7 days) are automatically consolidated into last_week.\n"
        "  • The agent should also update last_week and last_month occasionally via add(date=...) or replace()."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "add", "replace", "remove"],
                "description": "What to do. 'read' works on both targets. 'add'/'replace'/'remove' only work on target='memory'."
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "'memory' = activity log (writable YAML, 3-tier time structure). 'user' = user profile (read-only YAML)."
            },
            "content": {
                "type": "string",
                "description": "For 'add' (target='memory'): activity summary (1-2 lines). For 'replace': the new content."
            },
            "date": {
                "type": "string",
                "description": "For 'add' (target='memory'): target date (YYYY-MM-DD), 'last_week', or 'last_month'. Omit for today."
            },
            "old_text": {
                "type": "string",
                "description": "For 'replace'/'remove' (target='memory'): a date key (YYYY-MM-DD) to target a specific day, a section name (last_week/last_month), or a substring to match in daily entries."
            },
        },
        "required": ["action", "target"],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error

registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=lambda args, **kw: memory_tool(
        action=args.get("action", ""),
        target=args.get("target", "memory"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        date=args.get("date"),
        store=kw.get("store")),
    check_fn=check_memory_requirements,
    emoji="🧠",
)
