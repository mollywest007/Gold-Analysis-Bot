"""Local process lock for the single Telegram long-polling bot instance."""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from pathlib import Path
from typing import TextIO


def acquire_instance_lock(
    identity: str,
    *,
    lock_dir: str | os.PathLike[str] | None = None,
) -> TextIO | None:
    """Acquire a non-blocking lock shared by processes for the same entrypoint.

    Telegram permits only one getUpdates poller per bot token. This local lock
    also protects the file-backed trade and alert state from overlapping bot
    processes after a workflow restart.
    """
    canonical_identity = str(Path(identity).resolve())
    digest = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()[:20]
    directory = Path(lock_dir) if lock_dir is not None else Path(tempfile.gettempdir())
    lock_path = directory / f"gold-analysis-bot-{digest}.lock"
    directory.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle
