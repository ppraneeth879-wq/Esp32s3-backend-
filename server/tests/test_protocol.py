import struct
import uuid

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import MAGIC, PROTOCOL_VERSION, PacketType
from app.protocol import parse_packet, build_packet, ProtocolError, HEADER_SIZE


def test_roundtrip():
    dev = uuid.uuid4()
    payload = b"\x01\x02" * 10
    data = build_packet(PacketType.AUDIO, dev, 42, 12345, payload)
    pkt = parse_packet(data)
    assert pkt.magic == MAGIC
    assert pkt.version == PROTOCOL_VERSION
    assert pkt.packet_type == PacketType.AUDIO
    assert pkt.device_id == dev
    assert pkt.sequence == 42
    assert pkt.timestamp == 12345
    assert pkt.payload == payload
    assert pkt.payload_length == len(payload)


def test_bad_magic():
    dev = uuid.uuid4()
    data = build_packet(PacketType.AUDIO, dev, 1, 1, b"")
    data = b"XXXX" + data[4:]
    try:
        parse_packet(data)
        assert False
    except ProtocolError:
        pass


def test_length_mismatch():
    dev = uuid.uuid4()
    data = build_packet(PacketType.AUDIO, dev, 1, 1, b"abcd")
    # truncate
    try:
        parse_packet(data[:-1])
        assert False
    except ProtocolError:
        pass


def test_too_short():
    try:
        parse_packet(b"\x00" * 10)
        assert False
    except ProtocolError:
        pass