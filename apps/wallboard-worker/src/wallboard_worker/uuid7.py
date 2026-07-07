from __future__ import annotations

import os
import time
from uuid import UUID


def uuid7() -> UUID:
    """Generate a UUIDv7 conforming to RFC 4122."""
    # 48-bit timestamp in milliseconds
    ms = int(time.time() * 1000)
    timestamp_hex = f"{ms:012x}"

    # 10 bytes of randomness (80 bits)
    rand_bytes = os.urandom(10)
    rand_hex = rand_bytes.hex()

    # Layout:
    # 8 chars - 4 chars - '7' + 3 chars - [89ab] + 3 chars - 12 chars
    # Variant character: first 2 bits of rand_hex[4] must be '10' (binary).
    # So the value must be between 8 and 11 (hex 8, 9, a, or b).
    val = int(rand_hex[0], 16)
    var_char = hex(8 + (val % 4))[2:]

    uuid_str = (
        f"{timestamp_hex[:8]}-"
        f"{timestamp_hex[8:]}-"
        f"7{rand_hex[1:4]}-"
        f"{var_char}{rand_hex[4:7]}-"
        f"{rand_hex[7:19]}"
    )
    return UUID(uuid_str)
