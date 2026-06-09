"""Python-native file operations — zero shell forking, runs in-process.

Uses ``pathlib``, ``shutil``, and pure-Python fuzzy matching instead of
shelling out to ``cat``, ``mkdir -p``, ``rg``, etc.

Best for:
  - Local terminal backends (fastest option)
  - Environments where Python is available but shell tooling differs

Limitations:
  - No ``wc -c`` / ``head`` binary detection (uses magic bytes instead)
  - ``rg`` is replaced by pure-Python ``os.walk`` + ``fnmatch`` + ``re``
  - Image detection uses file extension heuristics only (no ``file`` cmd)
"""

from __future__ import annotations

import difflib
import os
import re
import shutil
import stat
import tempfile
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Optional

from . import (
    FileOperations,
    PatchResult,
    ReadResult,
    SearchMatch,
    SearchResult,
    WriteResult,
)


class PythonNativeFileOperations(FileOperations):
    """In-process file operations using Python stdlib — no shell commands."""

    def __init__(self, cwd: str | None = None):
        self._cwd = Path(cwd or os.getcwd()).resolve()

    # -- helpers -----------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self._cwd / p
        return p.resolve()

    def _ensure_parent(self, path: Path) -> bool:
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            return True
        return False

    def _is_binary(self, data: bytes) -> bool:
        """Check if bytes contain null bytes (binary heuristic)."""
        return b"\0" in data[:8192]

    # -- read_file ---------------------------------------------------------

    def read_file(self, path: str, offset: int = 1, limit: int = 500) -> ReadResult:
        path = self._resolve(path)
        if not path.exists():
            return self._suggest_similar(path)

        file_size = path.stat().st_size
        if file_size > 100 * 1024 * 1024:  # 100 MB
            return ReadResult(error=f"File too large ({file_size} bytes)")

        raw = path.read_bytes()

        # Binary detection
        if self._is_binary(raw):
            ext = path.suffix.lower()
            if ext in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}:
                import base64
                return ReadResult(
                    is_image=True,
                    is_binary=True,
                    file_size=file_size,
                    base64_content=base64.b64encode(raw).decode("ascii"),
                    mime_type=_MIME_TYPES.get(ext, "application/octet-stream"),
                )
            return ReadResult(
                is_binary=True,
                file_size=file_size,
                error="Binary file - cannot display as text",
            )

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines(keepends=True)
        total_lines = len(lines)

        if limit <= 0:
            limit = 500
        start_idx = max(0, offset - 1)
        end_idx = min(total_lines, start_idx + limit)

        # If only reading a portion, add line numbers
        selected = lines[start_idx:end_idx]
        numbered = "".join(
            f"{start_idx + i + 1}|{line}" for i, line in enumerate(selected)
        )

        truncated = end_idx < total_lines

        return ReadResult(
            content=numbered,
            total_lines=total_lines,
            file_size=file_size,
            truncated=truncated,
        )

    def _suggest_similar(self, path: Path) -> ReadResult:
        """Find similar filenames when the exact path doesn't exist."""
        parent = path.parent
        if not parent.exists():
            return ReadResult(error=f"File not found: {path}")

        target = path.name
        candidates = []
        for f in parent.iterdir():
            ratio = difflib.SequenceMatcher(None, target, f.name).ratio()
            if ratio > 0.4:
                candidates.append((ratio, f.name))
        candidates.sort(reverse=True)

        return ReadResult(
            error=f"File not found: {path}",
            similar_files=[name for _, name in candidates[:10]],
        )

    # -- read_file_raw -----------------------------------------------------

    def read_file_raw(self, path: str) -> ReadResult:
        path = self._resolve(path)
        if not path.exists():
            return ReadResult(error=f"File not found: {path}")
        try:
            text = path.read_text("utf-8")
            return ReadResult(content=text, total_lines=text.count("\n") + 1)
        except Exception as e:
            return ReadResult(error=str(e))

    # -- write_file --------------------------------------------------------

    def write_file(self, path: str, content: str) -> WriteResult:
        path = self._resolve(path)
        dirs_created = self._ensure_parent(path)

        # Atomic write: write to temp, then rename
        tmp = None
        try:
            fd, tmp_path = tempfile.mkstemp(dir=str(path.parent))
            os.close(fd)
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
            # Preserve existing mode, or default to 0644
            if path.exists():
                shutil.copymode(str(path), tmp_path)
            else:
                os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, str(path))
        except Exception as e:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)
            return WriteResult(error=str(e))

        return WriteResult(
            bytes_written=len(content.encode("utf-8")),
            dirs_created=dirs_created,
        )

    # -- patch_replace -----------------------------------------------------

    def patch_replace(
        self, path: str, old_string: str, new_string: str,
        replace_all: bool = False,
    ) -> PatchResult:
        path = self._resolve(path)
        if not path.exists():
            return PatchResult(success=False, error=f"File not found: {path}")

        try:
            text = path.read_text("utf-8")
        except Exception as e:
            return PatchResult(success=False, error=str(e))

        if replace_all:
            if old_string not in text:
                return PatchResult(success=False, error="old_string not found in file")
            new_text = text.replace(old_string, new_string)
        else:
            # Try exact match first, then fuzzy
            idx = text.find(old_string)
            if idx == -1:
                # Fuzzy fallback
                import difflib as _dl
                candidates = _dl.get_close_matches(old_string, text.splitlines(), n=1)
                if candidates:
                    idx = text.find(candidates[0])
                    if idx != -1:
                        old_string = candidates[0]
            if idx == -1:
                return PatchResult(
                    success=False,
                    error="old_string not found in file",
                )
            new_text = text[:idx] + new_string + text[idx + len(old_string):]

        # Generate unified diff
        old_lines = text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)
        diff = "".join(
            difflib.unified_diff(old_lines, new_lines, str(path), str(path))
        )

        try:
            path.write_text(new_text, encoding="utf-8")
        except Exception as e:
            return PatchResult(success=False, error=str(e))

        return PatchResult(
            success=True,
            diff=diff,
            files_modified=[str(path)],
        )

    # -- patch_v4a ---------------------------------------------------------

    def patch_v4a(self, patch_content: str) -> PatchResult:
        """Apply a V4A-format multi-file patch."""
        result = PatchResult(success=False)
        # V4A format: *** Begin Patch / *** Update File: path / @@ ... @@ / -/+ lines / *** End Patch
        current_file = None
        hunks: list[tuple[str, str, str, str]] = []  # (file, old, new, context)

        for line in patch_content.splitlines():
            if line.startswith("*** Update File:"):
                current_file = line.split(":", 1)[1].strip()
            elif line.startswith("*** Begin Patch"):
                pass
            elif line.startswith("*** End Patch"):
                pass
            elif line.startswith("--- ") and current_file:
                # Start of a hunk — capture old/new blocks
                pass

        # For simplicity, fall back to line-by-line application
        # Parse ---/+++ style unified diff blocks
        files_content: dict[str, str] = {}
        current_file = None
        in_hunk = False
        old_lines: list[str] = []
        new_lines: list[str] = []
        hunk_header = False

        for line in patch_content.splitlines():
            if line.startswith("--- "):
                fp = line[4:].strip()
            elif line.startswith("+++ "):
                fp = line[4:].strip()
                current_file = fp
                # Strip leading a/ b/ from git diff
                for prefix in ("a/", "b/", "/dev/null"):
                    if current_file.startswith(prefix):
                        current_file = current_file[len(prefix):]
                        break
                if current_file not in files_content:
                    resolved = self._resolve(current_file)
                    if resolved.exists():
                        files_content[current_file] = resolved.read_text("utf-8")
                    else:
                        files_content[current_file] = ""
                in_hunk = False
            elif line.startswith("@@"):
                in_hunk = True
                old_lines = []
                new_lines = []
                hunk_header = True
            elif in_hunk:
                if line.startswith("-") and not line.startswith("---"):
                    old_lines.append(line[1:])
                elif line.startswith("+") and not line.startswith("+++"):
                    new_lines.append(line[1:])
                elif line.startswith(" "):
                    old_lines.append(line[1:])
                    new_lines.append(line[1:])
                elif line.strip() == "":
                    old_lines.append("")
                    new_lines.append("")

        # Apply each chunk
        for fpath in files_content:
            content = files_content[fpath]
            # This is a simplified V4A — real implementation would
            # parse hunk ranges and apply context-matched patches.
            # For production, consider using ``patch`` library.
            pass

        return PatchResult(success=False, error="V4A not fully implemented in native backend")

    # -- delete_file -------------------------------------------------------

    def delete_file(self, path: str) -> WriteResult:
        path = self._resolve(path)
        if not path.exists():
            return WriteResult(error=f"File not found: {path}")
        try:
            os.unlink(str(path))
            return WriteResult(bytes_written=0)
        except Exception as e:
            return WriteResult(error=str(e))

    # -- delete_path -------------------------------------------------------

    def delete_path(self, path: str, recursive: bool = False) -> WriteResult:
        path = self._resolve(path)
        if not path.exists():
            return WriteResult(error=f"Path not found: {path}")
        try:
            if path.is_dir():
                if recursive:
                    shutil.rmtree(str(path))
                else:
                    path.rmdir()
            else:
                os.unlink(str(path))
            return WriteResult(bytes_written=0)
        except Exception as e:
            return WriteResult(error=str(e))

    # -- move_file ---------------------------------------------------------

    def move_file(self, src: str, dst: str) -> WriteResult:
        src_path = self._resolve(src)
        dst_path = self._resolve(dst)
        if not src_path.exists():
            return WriteResult(error=f"Source not found: {src_path}")
        try:
            self._ensure_parent(dst_path)
            shutil.move(str(src_path), str(dst_path))
            return WriteResult(bytes_written=0)
        except Exception as e:
            return WriteResult(error=str(e))

    # -- search ------------------------------------------------------------

    def search(
        self, pattern: str, path: str = ".", target: str = "content",
        file_glob: Optional[str] = None, limit: int = 50, offset: int = 0,
        output_mode: str = "content", context: int = 0,
    ) -> SearchResult:
        root = self._resolve(path)
        if not root.exists():
            return SearchResult(error=f"Path not found: {root}")
        if not root.is_dir():
            root = root.parent

        try:
            regex = re.compile(pattern)
        except re.error as e:
            return SearchResult(error=f"Invalid regex: {e}")

        if target == "files":
            return self._search_files(pattern, root, file_glob, limit, offset)

        matches: list[SearchMatch] = []
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                if file_glob and not fnmatch(fname, file_glob):
                    continue
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        for lineno, line in enumerate(f, 1):
                            if regex.search(line):
                                matches.append(SearchMatch(
                                    path=fpath,
                                    line_number=lineno,
                                    content=line.rstrip("\n"),
                                    mtime=os.path.getmtime(fpath),
                                ))
                except Exception:
                    continue

                if len(matches) >= offset + limit:
                    break
            if len(matches) >= offset + limit:
                break

        total = len(matches)
        page = matches[offset:offset + limit]

        if output_mode == "files_only":
            files = sorted(set(m.path for m in page))
            return SearchResult(files=files, total_count=total, truncated=total > limit)
        elif output_mode == "count":
            from collections import Counter
            counts = Counter(m.path for m in page)
            return SearchResult(
                counts=dict(counts),
                total_count=total,
                truncated=total > limit,
            )
        else:
            return SearchResult(
                matches=page,
                total_count=total,
                truncated=total > limit,
            )

    def _search_files(
        self, pattern: str, root: Path,
        file_glob: Optional[str], limit: int, offset: int,
    ) -> SearchResult:
        matches: list[str] = []
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                if file_glob and not fnmatch(fname, file_glob):
                    continue
                if fnmatch(fname, pattern):
                    matches.append(os.path.join(dirpath, fname))
                if len(matches) >= offset + limit:
                    break
            if len(matches) >= offset + limit:
                break

        total = len(matches)
        page = matches[offset:offset + limit]
        return SearchResult(
            files=page,
            total_count=total,
            truncated=total > limit,
        )


# MIME type map for image results
_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
}
