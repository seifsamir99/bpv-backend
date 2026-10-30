"""
Small in-process TTL cache for Google Sheets reads.

Sheets is the system's database, but every API request was re-reading whole
tabs (~2-3s per request). Reads are cached for TTL_SECONDS; any write through
a service invalidates every cached entry for that spreadsheet (coarse on
purpose — writes are rare and correctness beats cleverness here).

Keys are strings, conventionally "{spreadsheet_id}:{tab_or_range}".
Values are deep-copied on both put and get so callers can't mutate the cache.
"""
import copy
import threading
import time

TTL_SECONDS = 45

_lock = threading.Lock()
_store = {}  # key -> (expires_at, value)


def get(key):
    """Return (hit, value). value is a deep copy safe to mutate."""
    with _lock:
        entry = _store.get(key)
        if entry:
            if entry[0] > time.time():
                return True, copy.deepcopy(entry[1])
            del _store[key]
    return False, None


def put(key, value):
    with _lock:
        _store[key] = (time.time() + TTL_SECONDS, copy.deepcopy(value))


def invalidate(prefix):
    """Drop every cached entry whose key starts with prefix."""
    with _lock:
        for key in [k for k in _store if k.startswith(prefix)]:
            del _store[key]
