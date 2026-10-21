"""Binary protocol parser/serializer for LifeLog."""

from __future__ import annotations

import struct
import uuid
from typing import Optional

from .models import HEADER_SIZE, MAGIC, PROTOCOL_VERSION, Packet, PacketType

# Header layout:
# 4s magic, B version, B type, H reserved, 16s device_id, I seq, Q timestamp, I payload_len
_HEADER_FMT = "<4sBBH16sIQI"
assert struct.calcsize(_HEADER_FMT) == HEADER_SIZE


class ProtocolError(Exception):
    pass


def parse_packet(data: bytes) -> Packet:
    if len(data) < HEADER_SIZE:
        raise ProtocolError(f"frame too short: {len(data)} < {HEADER_SIZE}")
    magic, version, ptype, reserved, device_raw, seq, ts, plen = struct.unpack(
        _HEADER_FMT, data[:HEADER_SIZE]
    )
    if magic != MAGIC:
        raise ProtocolError(f"bad magic: {magic!r}")
    if version != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported version: {version}")
    try:
        packet_type = PacketType(ptype)
    except ValueError:
        raise ProtocolError(f"unknown packet type: {ptype}")
    if reserved != 0:
        raise ProtocolError(f"reserved must be 0, got {reserved}")
    if plen != len(data) - HEADER_SIZE:
        raise ProtocolError(
            f"payload length mismatch: declared {plen}, actual {len(data) - HEADER_SIZE}"
        )
    try:
        device_id = uuid.UUID(bytes=device_raw)
    except ValueError:
        raise ProtocolError("invalid device uuid")
    return Packet(
        magic=magic,
        version=version,
        packet_type=packet_type,
        reserved=reserved,
        device_id=device_id,
        sequence=seq,
        timestamp=ts,
        payload_length=plen,
        payload=data[HEADER_SIZE:],
    )


def build_packet(
    packet_type: PacketType,
    device_id: uuid.UUID,
    sequence: int,
    timestamp: int,
    payload: bytes = b"",
) -> bytes:
    header = struct.pack(
        _HEADER_FMT,
        MAGIC,
        PROTOCOL_VERSION,
        int(packet_type),
        0,
        device_id.bytes,
        sequence,
        timestamp,
        len(payload),
    )
    return header + payload