"""Process-wide file-path lock registry.

Prevents two worker threads from operating on the same file path concurrently.
try_acquire / release are called by Worker._run; individual execute_fn
implementations do not need to interact with this module directly.
"""

import threading

_lock = threading.Lock()
_active: set[str] = set()


def try_acquire(path: str) -> bool:
    """Atomically acquire path. Returns False if already held."""
    with _lock:
        if path in _active:
            return False
        _active.add(path)
        return True


def release(path: str):
    with _lock:
        _active.discard(path)
