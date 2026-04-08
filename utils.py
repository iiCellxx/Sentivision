"""Shared utilities for SentiVision."""

_ID_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ'


def track_id_to_alias(tid: int) -> str:
    """Convert a numeric tracker ID to a stable 3-letter code, e.g. 1 -> 'AAB'."""
    base = len(_ID_LETTERS)
    tid = max(0, tid - 1)
    c0 = _ID_LETTERS[(tid // (base * base)) % base]
    c1 = _ID_LETTERS[(tid // base) % base]
    c2 = _ID_LETTERS[tid % base]
    return f"{c0}{c1}{c2}"