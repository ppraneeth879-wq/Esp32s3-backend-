"""Audio receiver service: parses incoming packets and feeds the pipeline."""

from __future__ import annotations

import logging
from typing import Callable, Optional

from .protocol import parse_packet, ProtocolError

logger = logging.getLogger("lifelog.receiver")


class Receiver:
    def __init__(self, on_audio: Callable[[bytes, object], None], on_packet: Optional[Callable] = None):
        self.on_audio = on_audio
        self.on_packet = on_packet

    async def handle_bytes(self, data: bytes) -> Optional[object]:
        try:
            pkt = parse_packet(data)
        except ProtocolError as e:
            logger.warning("invalid packet: %s", e)
            return None
        if self.on_packet:
            try:
                self.on_packet(pkt)
            except Exception as e:
                logger.exception("on_packet error: %s", e)
        if pkt.packet_type.value == 0x01:  # AUDIO
            self.on_audio(pkt.payload, pkt)
        return pkt