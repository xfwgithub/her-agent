"""Search backend resolver.

The web_search tool already uses a plugin-based provider registry
(``agent.web_search_registry``) with 7+ providers. This resolver
is a thin config layer that selects the right provider based on
``web.search_backend`` / ``web.backend`` config keys.

The real pluggable architecture lives in:
  - ``tools/web_tools.py`` — ``_get_search_backend()``, ``_get_extract_backend()``
  - ``agent.web_search_registry`` — provider registration + dispatch
  - ``plugins/web/`` — individual provider plugins

Config::

    web:
      backend: searxng            # shared default
      search_backend: searxng      # override for search only
      extract_backend: firecrawl   # override for extract only
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def resolve_search_provider() -> str | None:
    """Return the active search provider name, or None."""
    try:
        from tools.web_tools import _get_search_backend
        return _get_search_backend()
    except Exception:
        return None


def resolve_extract_provider() -> str | None:
    """Return the active extract provider name, or None."""
    try:
        from tools.web_tools import _get_extract_backend
        return _get_extract_backend()
    except Exception:
        return None
