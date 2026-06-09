"""Project identity tools — quick project lookup by name/query.

Registered in the agent tool schema when the active profile has ``project``
in its toolsets config. A normal ``her chat`` session sees zero project
tools unless configured.

Why tools instead of a skill?
  Tools are always in the agent's schema. When the user says "我的XXX项目",
  the model can call ``project_find`` immediately without remembering to
  load a skill first.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------

def _check_project_mode() -> bool:
    """Project tools are available when the active profile has ``project``
    in its toolsets config."""
    try:
        from her_cli.config import load_config
        cfg = load_config()
        toolsets = cfg.get("toolsets", [])
        return "project" in toolsets
    except Exception:
        return False


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_PROJECT_DB_PATH = None  # lazy-resolved


def _get_db():
    """Lazy import + connect to the shared projects.db."""
    from her_cli import project_db as pm
    # If the identity skill's script is available, prefer it (has find_project)
    try:
        import sys as _sys
        _skill_path = os.path.expanduser(
            "~/.agents/skills/project-identity/scripts"
        )
        if os.path.isdir(_skill_path) and _skill_path not in _sys.path:
            _sys.path.insert(0, _skill_path)
        from project_db import connect, init_db, find_project, get_project, list_projects
    except ImportError:
        # Fallback: use her_cli's project_db if available
        try:
            from her_cli.project_db import connect, init_db, find_project, get_project, list_projects
        except ImportError:
            # Minimal inline implementation
            pm = None  # will raise below
            raise ImportError("project_db module not found")
    
    conn = connect()
    init_db(conn)
    return conn, find_project, get_project, list_projects, None


def _connect():
    """Open projects.db connection."""
    import sqlite3
    her_home = os.environ.get("HER_HOME", os.path.expanduser("~/.her"))
    db_path = os.path.join(her_home, "projects.db")
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure_schema(conn):
    """Create tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            kind TEXT NOT NULL DEFAULT 'other',
            description TEXT NOT NULL DEFAULT '',
            vision TEXT NOT NULL DEFAULT '',
            directory TEXT NOT NULL DEFAULT '',
            directory_tree TEXT NOT NULL DEFAULT '',
            long_term TEXT NOT NULL DEFAULT '',
            short_term TEXT NOT NULL DEFAULT '',
            progress TEXT NOT NULL DEFAULT '',
            issues TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS project_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
    """)


def _find_project(conn, query: str) -> Optional[dict]:
    """Find project by id, name substring, or directory substring."""
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (query,)).fetchone()
    if row: return dict(row)
    row = conn.execute(
        "SELECT * FROM projects WHERE name LIKE ? LIMIT 1",
        (f"%{query}%",),
    ).fetchone()
    if row: return dict(row)
    row = conn.execute(
        "SELECT * FROM projects WHERE directory LIKE ? LIMIT 1",
        (f"%{query}%",),
    ).fetchone()
    if row: return dict(row)
    return None


def _get_project(conn, pid: str) -> Optional[dict]:
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    return dict(row) if row else None


def _list_projects(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT id, name, kind, status, description, directory, updated_at "
        "FROM projects ORDER BY updated_at DESC"
    ).fetchall()]


def _update_project(conn, pid: str, changes: dict) -> tuple[Optional[dict], Optional[str]]:
    allowed = {
        "name", "status", "kind", "description", "vision", "directory",
        "directory_tree", "long_term", "short_term", "progress", "issues", "notes",
    }
    bad = set(changes) - allowed
    if bad:
        return None, f"unknown fields: {bad}"
    sets = []
    params = []
    for key in allowed:
        if key in changes:
            sets.append(f"{key} = ?")
            params.append(changes[key])
    if not sets:
        return _get_project(conn, pid), None
    import time
    params.append(time.strftime("%Y-%m-%dT%H:%M:%S"))
    params.append(pid)
    conn.execute(f"UPDATE projects SET {', '.join(sets)}, updated_at = ? WHERE id = ?", params)
    return _get_project(conn, pid), None


def _fmt_project(proj: dict) -> dict:
    """Compact project summary for tool responses."""
    return {
        "id": proj["id"],
        "name": proj["name"],
        "kind": proj["kind"],
        "status": proj["status"],
        "description": proj.get("description", ""),
        "vision": proj.get("vision", ""),
        "directory": proj.get("directory", ""),
        "short_term": proj.get("short_term", ""),
        "progress": proj.get("progress", ""),
        "issues": proj.get("issues", ""),
        "updated_at": proj.get("updated_at", "")[:19],
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

PROJECT_FIND_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "项目名称或关键词。支持模糊匹配（slug/名称/路径）。例如：'大明'、'环球'、'novels'",
        },
    },
    "required": ["query"],
}

PROJECT_SHOW_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {
            "type": "string",
            "description": "项目 ID（slug），如 'huanqiu-daming'",
        },
    },
    "required": ["project_id"],
}

PROJECT_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "description": "Filter by kind: novel, code, research, other (optional)",
        },
    },
}

PROJECT_UPDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "project_id": {"type": "string", "description": "项目 ID（slug）"},
        "field": {
            "type": "string",
            "description": "要更新的字段名",
            "enum": [
                "name", "status", "kind", "description", "vision",
                "directory", "directory_tree", "long_term", "short_term",
                "progress", "issues", "notes",
            ],
        },
        "value": {"type": "string", "description": "字段新值"},
    },
    "required": ["project_id", "field", "value"],
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _handle_find(args: dict, **kw) -> str:
    query = args.get("query", "")
    if not query:
        return tool_error("query is required")
    try:
        conn = _connect()
        try:
            _ensure_schema(conn)
            proj = _find_project(conn, query)
            if not proj:
                return json.dumps({
                    "found": False,
                    "query": query,
                    "message": f"未找到匹配 '{query}' 的项目",
                }, ensure_ascii=False)
            return json.dumps({
                "found": True,
                "query": query,
                "project": _fmt_project(proj),
            }, ensure_ascii=False)
        finally:
            conn.close()
    except Exception as e:
        logger.exception("project_find failed")
        return tool_error(f"project_find: {e}")


def _handle_show(args: dict, **kw) -> str:
    pid = args.get("project_id", "")
    if not pid:
        return tool_error("project_id is required")
    try:
        conn = _connect()
        try:
            _ensure_schema(conn)
            proj = _get_project(conn, pid)
            if not proj:
                return tool_error(f"项目 '{pid}' 不存在")
            return json.dumps({"project": dict(proj)}, ensure_ascii=False, default=str)
        finally:
            conn.close()
    except Exception as e:
        logger.exception("project_show failed")
        return tool_error(f"project_show: {e}")


def _handle_list(args: dict, **kw) -> str:
    kind = args.get("kind")
    try:
        conn = _connect()
        try:
            _ensure_schema(conn)
            projects = _list_projects(conn)
            if kind:
                projects = [p for p in projects if p.get("kind") == kind]
            return json.dumps({
                "projects": [_fmt_project(p) for p in projects],
                "count": len(projects),
            }, ensure_ascii=False)
        finally:
            conn.close()
    except Exception as e:
        logger.exception("project_list failed")
        return tool_error(f"project_list: {e}")


def _handle_update(args: dict, **kw) -> str:
    pid = args.get("project_id", "")
    field = args.get("field", "")
    value = args.get("value", "")
    if not pid or not field:
        return tool_error("project_id and field are required")
    try:
        conn = _connect()
        try:
            _ensure_schema(conn)
            proj, err = _update_project(conn, pid, {field: value})
            if err:
                return tool_error(err)
            conn.commit()
            return json.dumps({
                "ok": True,
                "project_id": pid,
                "updated": field,
                "project": _fmt_project(proj),
            }, ensure_ascii=False)
        finally:
            conn.close()
    except Exception as e:
        logger.exception("project_update failed")
        return tool_error(f"project_update: {e}")


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------

registry.register(
    name="project_find",
    toolset="project",
    schema=PROJECT_FIND_SCHEMA,
    handler=_handle_find,
    check_fn=_check_project_mode,
    emoji="🔍",
)

registry.register(
    name="project_show",
    toolset="project",
    schema=PROJECT_SHOW_SCHEMA,
    handler=_handle_show,
    check_fn=_check_project_mode,
    emoji="📖",
)

registry.register(
    name="project_list",
    toolset="project",
    schema=PROJECT_LIST_SCHEMA,
    handler=_handle_list,
    check_fn=_check_project_mode,
    emoji="📋",
)

registry.register(
    name="project_update",
    toolset="project",
    schema=PROJECT_UPDATE_SCHEMA,
    handler=_handle_update,
    check_fn=_check_project_mode,
    emoji="✏",
)
