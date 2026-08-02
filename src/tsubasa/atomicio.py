"""Crash-safe file writes.

`Path.write_text` truncates the target before writing: a crash, a full disk, or
a reader arriving mid-write sees a partial file. For a store whose event log is
the durable source of truth — `rebuild` replays it — a truncated write is data
loss, not a transient glitch.

`write_text_atomic` writes to a sibling temp file and renames it over the
target. POSIX rename is atomic within a filesystem, so a reader observes either
the old file or the new one, never a half-written one, and a crash leaves the
previous version intact.

The temp file is created in the destination's own directory because rename is
only atomic within a single filesystem; /tmp is frequently a different one.

This provides crash-safety and reader-safety, not write serialization:
concurrent writers still last-rename-wins.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` atomically, replacing it if it exists."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
        # mkstemp is deliberately 0600 for temp secrecy; the rename must not
        # export that onto a shared file. Preserve the target's existing
        # permission bits, or apply the process default (0666 & ~umask) when
        # creating a fresh file.
        try:
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except FileNotFoundError:
            umask = os.umask(0)
            os.umask(umask)
            mode = 0o666 & ~umask
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        # leave no debris behind on failure; the original file is untouched
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
