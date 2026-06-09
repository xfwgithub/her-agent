"""Pluggable file operation backends for her-agent.

The abstract interface lives in ``tools.file_operations.FileOperations``.
Each backend in this package inherits from it and provides a concrete
implementation.

To add a new backend:
  1. Create ``backends/<name>.py`` with a class inheriting ``FileOperations``.
  2. Add a detection function to ``resolver.py``.
  3. Wire it into ``resolve_file_operations()``.
"""

from tools.file_operations import (
    FileOperations,        # Abstract base class
    ReadResult,            # Return types
    WriteResult,
    PatchResult,
    SearchResult,
    SearchMatch,
)
