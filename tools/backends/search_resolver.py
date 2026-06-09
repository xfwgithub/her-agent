"""Search backend resolver.

Extensible search/discovery backends following the pluggable pattern.

Current backends:
  - ``searxng`` (default) — local SearXNG instance
  - ``direct`` — direct API calls (Google/Bing/DuckDuckGo)
  - ``meilisearch`` — local Meilisearch (for project/document search)
  - ``mcp`` — MCP-based search server

Config key: ``search_backend`` (in ``~/.her/config.yaml``)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _config_backend() -> str:
    try:
        from her_cli.config import load_config
        cfg = load_config()
        strategy = cfg.get("search_backend", "auto")
        if isinstance(strategy, str):
            return strategy
    except Exception:
        pass
    return "auto"


def resolve_search_url() -> tuple[str | None, str | None]:
    """Resolve search backend URLs.

    Returns (search_url, extract_url) where each can be None.
    """
    import os
    backend = _config_backend()

    if backend == "meilisearch":
        # Local Meilisearch
        url = os.environ.get("MEILISEARCH_URL", "http://127.0.0.1:7700")
        return url, None

    if backend == "mcp":
        # MCP-based search — returns None URLs, caller uses MCP tools
        return None, None

    # Default: SearXNG
    search_url = os.environ.get("SEARXNG_URL", "").strip() or None
    extract_fallback = os.environ.get("EXTRACT_FALLBACK", "").strip() or None
    return search_url, extract_fallback
