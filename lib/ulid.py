"""ULID generation — stdlib only.

A ULID is a 26-character Crockford-base32 string: 48 bits of millisecond
timestamp followed by 80 bits of randomness. Lexical order equals creation
order; within the same millisecond the random component is incremented
monotonically so back-to-back ids still sort in creation order.
"""

import secrets
import threading
import time

CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

TIMESTAMP_CHARS = 10  # 48 bits
RANDOMNESS_CHARS = 16  # 80 bits
RANDOMNESS_BYTES = 10
_RANDOMNESS_MASK = (1 << 80) - 1

_lock = threading.Lock()
_last_timestamp_ms = -1
_last_randomness = 0


def _encode_base32(value: int, length: int) -> str:
    """Encode an integer as fixed-length Crockford base32 (5 bits per char).

    Args:
        value: Non-negative integer to encode.
        length: Number of output characters (value must fit in length * 5 bits).

    Returns:
        Crockford-base32 string of exactly `length` characters.
    """
    chars = []
    for _ in range(length):
        chars.append(CROCKFORD_ALPHABET[value & 0b11111])
        value >>= 5
    return "".join(reversed(chars))


def new_ulid() -> str:
    """Generate a new ULID.

    Returns:
        A 26-character Crockford-base32 ULID, lexically sortable by
        creation time (monotonic within a single process).
    """
    global _last_timestamp_ms, _last_randomness
    with _lock:
        timestamp_ms = time.time_ns() // 1_000_000
        if timestamp_ms == _last_timestamp_ms:
            randomness = (_last_randomness + 1) & _RANDOMNESS_MASK
            if randomness == 0:
                # 80-bit increment wrapped (practically unreachable): move to
                # the next millisecond to preserve sort order.
                while timestamp_ms <= _last_timestamp_ms:
                    timestamp_ms = time.time_ns() // 1_000_000
                randomness = int.from_bytes(
                    secrets.token_bytes(RANDOMNESS_BYTES), "big"
                )
        else:
            randomness = int.from_bytes(secrets.token_bytes(RANDOMNESS_BYTES), "big")
        _last_timestamp_ms = timestamp_ms
        _last_randomness = randomness
        return _encode_base32(timestamp_ms, TIMESTAMP_CHARS) + _encode_base32(
            randomness, RANDOMNESS_CHARS
        )
