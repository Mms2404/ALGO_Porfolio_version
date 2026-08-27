"""Angel SmartStream binary tick parsing (LTP / mode 1)."""

from __future__ import annotations

import struct


def parse_ltp_tick(data: bytes) -> dict | None:
    """Parse a binary LTP packet (>=51 bytes) from the Angel websocket.

    Returns {'token': int, 'ltp': float} (LTP converted paise -> rupees),
    or None if the packet is too short. LTP-mode (mode 1) only.
    """
    if len(data) < 51:
        return None

    # token: 25-byte UTF-8 string at offset 2, null-padded
    token_str = data[2:27].split(b"\x00", 1)[0].decode("utf-8")
    token = int(token_str)

    # LTP: int32 little-endian at offset 43, in paise
    ltp_paise = struct.unpack("<i", data[43:47])[0]
    ltp = ltp_paise / 100.0

    return {"token": token, "ltp": ltp}