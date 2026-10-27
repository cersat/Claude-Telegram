"""Метки времени в логе: каждая строка stdout/stderr получает префикс [YYYY-MM-DD HH:MM:SS]."""

import sys
import threading
from bridge import core

STAMP_LEN = len("[2026-01-01 00:00:00] ")


class _StampedStream:
    def __init__(self, stream):
        self._stream = stream
        self._at_line_start = True
        self._lock = threading.Lock()

    def write(self, data):
        if not data:
            return 0
        with self._lock:
            out = []
            for part in data.splitlines(keepends=True):
                if self._at_line_start:
                    out.append(core.now_local().strftime("[%Y-%m-%d %H:%M:%S] "))
                out.append(part)
                self._at_line_start = part.endswith("\n")
            self._stream.write("".join(out))
        return len(data)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install():
    """Оборачивает sys.stdout и sys.stderr (идемпотентно)."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if not isinstance(stream, _StampedStream):
            setattr(sys, name, _StampedStream(stream))
