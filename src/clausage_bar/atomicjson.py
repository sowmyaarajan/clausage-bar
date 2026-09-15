"""Atomic JSON writes and torn-read-tolerant reads.

os.replace maps to MoveFileEx(MOVEFILE_REPLACE_EXISTING) on Windows, so a
reader never observes a partially written file. The temp name embeds the PID so
two writers cannot collide.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import assert_readable


def write_atomic(path: Path, data: Any, *, attempts: int = 3) -> bool:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, default=str)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    for _ in range(attempts):
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
            return True
        except OSError:
            # A reader may briefly hold the target open; dropping one sample is
            # harmless, so retry and then give up quietly.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    return False


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON, returning ``default`` on absence or a torn/invalid read."""
    path = Path(path)
    assert_readable(path)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def stat_key(path: Path) -> tuple[int, int] | None:
    """Cheap change token, so we only parse a file that actually changed."""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)
