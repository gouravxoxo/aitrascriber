import itertools
import os
import re
import threading
from typing import Iterable


_key_cycle = itertools.count()
_key_lock = threading.Lock()


def get_mistral_api_keys() -> list[str]:
    keys: list[str] = []

    multi = os.getenv("MISTRAL_API_KEYS", "")
    if multi:
        for raw in re.split(r"[\s,]+", multi.strip()):
            key = raw.strip()
            if key and key not in keys:
                keys.append(key)

    single = os.getenv("MISTRAL_API_KEY", "").strip()
    if single and single not in keys:
        keys.append(single)

    return keys


def get_rotated_mistral_api_keys() -> list[str]:
    keys = get_mistral_api_keys()
    if not keys:
        return []

    with _key_lock:
        start = next(_key_cycle) % len(keys)
    return keys[start:] + keys[:start]


def key_label(api_key: str) -> str:
    if len(api_key) <= 10:
        return "***"
    return f"{api_key[:6]}...{api_key[-4:]}"


def is_retryable_mistral_error(message: str) -> bool:
    text = (message or "").lower()
    retryable_markers: Iterable[str] = (
        "status 429",
        "status 500",
        "status 502",
        "status 503",
        "status 504",
        "status 520",
        "capacity exceeded",
        "service unavailable",
        "gateway time-out",
        "bad gateway",
        "overflow",
        "timed out",
        "timeout",
        "connection reset",
        "temporarily unavailable",
    )
    return any(marker in text for marker in retryable_markers)
