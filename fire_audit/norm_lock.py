from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class NormativeUpdateBusy(RuntimeError):
    """Another application window or MCP process is updating this cache."""


@contextmanager
def normative_update_lock(path: Path) -> Iterator[None]:
    """OS-backed lock; released on close/crash, shared by threads and processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the lock file: unlinking it could allow two different lock owners.
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            raise NormativeUpdateBusy(
                "Нормативная база уже обновляется в другом окне программы или через подключение к чату. "
                "Дождитесь завершения текущего обновления."
            ) from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
