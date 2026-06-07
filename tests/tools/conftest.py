"""Shared fixtures for tests/tools/ web-provider tests.

Per-file subprocess isolation means each test file gets a fresh interpreter,
so module-level state (like the web-search-provider registry) is empty when
a file starts.  The ``web_registry_populated`` fixture registers all bundled
providers before each test and resets the registry afterwards — tests that
depend on the registry being populated should use it explicitly or via
``@pytest.mark.usefixtures("web_registry_populated")``.
"""

from unittest.mock import patch

import pytest


def register_all_web_providers():
    """Register all bundled web-search providers into the global registry.

    This is the single source of truth for the provider list used by
    test classes that need the registry populated for dispatch checks.
    """
    from agent.web_search_registry import register_provider, _reset_for_tests
    from plugins.web.brave_free.provider import BraveFreeWebSearchProvider
    from plugins.web.ddgs.provider import DDGSWebSearchProvider
    from plugins.web.exa.provider import ExaWebSearchProvider
    from plugins.web.firecrawl.provider import FirecrawlWebSearchProvider
    from plugins.web.parallel.provider import ParallelWebSearchProvider
    from plugins.web.searxng.provider import SearXNGWebSearchProvider
    from plugins.web.tavily.provider import TavilyWebSearchProvider
    from plugins.web.xai.provider import XAIWebSearchProvider

    _reset_for_tests()
    for cls in (
        BraveFreeWebSearchProvider,
        DDGSWebSearchProvider,
        ExaWebSearchProvider,
        FirecrawlWebSearchProvider,
        ParallelWebSearchProvider,
        SearXNGWebSearchProvider,
        TavilyWebSearchProvider,
        XAIWebSearchProvider,
    ):
        register_provider(cls())


@pytest.fixture
def web_registry_populated():
    """Populate the web-search-provider registry for one test, then reset."""
    register_all_web_providers()
    yield
    from agent.web_search_registry import _reset_for_tests
    _reset_for_tests()


@pytest.fixture
def disable_lazy_stt_install():
    """Disarm the runtime lazy-install probe so static ``_HAS_FASTER_WHISPER``
    patches accurately simulate 'faster-whisper not installed'.

    With this, ``_try_lazy_install_stt()`` calls
    ``importlib.util.find_spec("faster_whisper")``, which returns truthy
    whenever the package is installed in the dev / CI environment —
    defeating the test's ``_HAS_FASTER_WHISPER=False`` patch.

    Opt in at module scope with
    ``pytestmark = pytest.mark.usefixtures("disable_lazy_stt_install")``.
    """
    with patch("tools.transcription_tools._try_lazy_install_stt", return_value=False):
        yield


# ---------------------------------------------------------------------------
# 2026-06-07 platform slim: skip MCP tests when the mcp package is missing.
# The platform slim only removed messaging adapters; mcp itself is core.
# When mcp deps are absent in the venv, we still want the rest of the
# tools/ suite to run.  Use ``pytest_collection_modifyitems`` to mark
# only the mcp test modules as skipped, not the whole directory.
# ---------------------------------------------------------------------------
_MCP_SKIP_REASON = "mcp package not installed (dev venv without mcp deps)"

try:
    import mcp  # noqa: F401, E402
    _MCP_AVAILABLE = True
except ModuleNotFoundError:
    _MCP_AVAILABLE = False


def pytest_collection_modifyitems(config, items):
    if _MCP_AVAILABLE:
        return
    for item in items:
        # Only skip the mcp test modules -- everything else in tools/
        # should still run.
        if item.module.__name__.startswith("tests.tools.test_mcp"):
            item.add_marker(pytest.mark.skip(reason=_MCP_SKIP_REASON))
