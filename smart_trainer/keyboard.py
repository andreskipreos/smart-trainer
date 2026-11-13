"""Non-blocking single-key input for the ride screen (Linux/macOS terminals and Windows)."""

from __future__ import annotations

import asyncio
import sys
import threading


class KeyReader:
    """Reads keys in a background thread and pushes them into an asyncio queue."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._old_attrs = None
        self.enabled = sys.stdin.isatty()

    def __enter__(self) -> "KeyReader":
        if not self.enabled:
            return self
        loop = asyncio.get_running_loop()
        if sys.platform == "win32":
            target = self._run_windows
        else:
            import termios
            import tty

            fd = sys.stdin.fileno()
            self._old_attrs = termios.tcgetattr(fd)
            tty.setcbreak(fd)
            target = self._run_posix
        self._thread = threading.Thread(target=target, args=(loop,), daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._old_attrs is not None:
            import termios

            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, self._old_attrs)
            self._old_attrs = None

    def _push(self, loop: asyncio.AbstractEventLoop, key: str) -> None:
        loop.call_soon_threadsafe(self.queue.put_nowait, key)

    def _run_posix(self, loop: asyncio.AbstractEventLoop) -> None:
        import os
        import select

        fd = sys.stdin.fileno()
        while not self._stop.is_set():
            r, _, _ = select.select([fd], [], [], 0.1)
            if not r:
                continue
            ch = os.read(fd, 1).decode(errors="ignore")
            if ch == "\x1b":  # escape sequence (arrow keys)
                r, _, _ = select.select([fd], [], [], 0.02)
                if r:
                    seq = os.read(fd, 2).decode(errors="ignore")
                    ch = {"[A": "UP", "[B": "DOWN", "[C": "RIGHT", "[D": "LEFT"}.get(seq, "")
                else:
                    ch = "ESC"
            if ch:
                self._push(loop, ch)

    def _run_windows(self, loop: asyncio.AbstractEventLoop) -> None:  # pragma: no cover
        import msvcrt
        import time

        while not self._stop.is_set():
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    code = msvcrt.getwch()
                    ch = {"H": "UP", "P": "DOWN", "M": "RIGHT", "K": "LEFT"}.get(code, "")
                if ch:
                    self._push(loop, ch)
            else:
                time.sleep(0.05)

    def drain(self) -> list[str]:
        keys = []
        while not self.queue.empty():
            keys.append(self.queue.get_nowait())
        return keys
