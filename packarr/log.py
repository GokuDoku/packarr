"""One-line-per-event log: stdout plus an append-only file when the config names one."""

from __future__ import annotations

import time

_file = None


def setup(path: str | None) -> None:
    global _file
    _file = path


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    if _file:
        with open(_file, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
