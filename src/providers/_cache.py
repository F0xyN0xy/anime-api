import time

SHOW_IDENTITY_TTL = 6 * 3600
MAPPING_TTL = 24 * 3600
EPISODES_TTL = 3600

_store: dict = {}


def get(key):
    return _store.get(key)


def set(key, data, ttl):
    _store[key] = {"data": data, "expires": time.time() + ttl}


def is_fresh(entry) -> bool:
    return bool(entry) and entry.get("expires", 0) > time.time()


def cached(key, ttl):
    entry = get(key)
    return entry["data"] if is_fresh(entry) else None
