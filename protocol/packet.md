# Packet Wire Format

All multi-byte integers are little-endian.

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                        Magic (4 bytes) "LLV1"                 |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
| Ver   | Type  |            Reserved (2 bytes)                 |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|                     Device ID (16 bytes UUID)                 |
|                                                               |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Sequence Number (uint32)                   |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|                    Timestamp (uint64)                          |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                    Payload Length (uint32)                     |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|                       Payload (N bytes)                       |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

## Field details

- Magic: bytes `4C 4C 56 31`.
- Protocol Version: uint8.
- Packet Type: uint8.
- Reserved: uint16, must be 0 on send, ignored on receive (but validated == 0 by strict parsers).
- Device ID: 16 raw bytes. UUID is transmitted in its binary form.
- Sequence Number: uint32.
- Timestamp: uint64 milliseconds.
- Payload Length: uint32. Must equal actual trailing bytes; mismatch => invalid packet.

## Validation rules

1. Minimum frame size = 40 bytes.
2. Magic must equal `LLV1`.
3. Protocol version must be supported (currently {1}).
4. Packet type must be in {0x01..0x06}.
5. Payload length must match remaining bytes.
6. On any violation: reject frame, log, optionally close connection.