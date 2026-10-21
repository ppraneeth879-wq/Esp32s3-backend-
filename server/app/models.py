"""Shared data models for LifeLog backend."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class PacketType(int, Enum):
    AUDIO = 0x01
    HEARTBEAT = 0x02
    DEVICE_INFO = 0x03
    START_SESSION = 0x04
    END_SESSION = 0x05
    ERROR = 0x06


MAGIC = b"LLV1"
PROTOCOL_VERSION = 1
HEADER_SIZE = 40


class SessionState(str, Enum):
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECORDING = "RECORDING"
    TRANSCRIBING = "TRANSCRIBING"
    FINISHING = "FINISHING"
    CLOSED = "CLOSED"


# Allowed transitions for the session state machine.
ALLOWED_TRANSITIONS: dict[SessionState, set[SessionState]] = {
    SessionState.IDLE: {SessionState.CONNECTING},
    SessionState.CONNECTING: {SessionState.CONNECTED, SessionState.CLOSED},
    SessionState.CONNECTED: {SessionState.RECORDING, SessionState.CLOSED},
    SessionState.RECORDING: {SessionState.TRANSCRIBING, SessionState.FINISHING, SessionState.CLOSED},
    SessionState.TRANSCRIBING: {SessionState.RECORDING, SessionState.FINISHING, SessionState.CLOSED},
    SessionState.FINISHING: {SessionState.CLOSED},
    SessionState.CLOSED: {SessionState.IDLE},
}


@dataclass
class DeviceInfo:
    device_id: uuid.UUID
    device_name: str = "unknown"
    firmware_version: str = "0.0.0"
    hardware_revision: str = "rev0"
    protocol_version: int = PROTOCOL_VERSION


@dataclass
class Packet:
    magic: bytes
    version: int
    packet_type: PacketType
    reserved: int
    device_id: uuid.UUID
    sequence: int
    timestamp: int
    payload_length: int
    payload: bytes


@dataclass
class Session:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    device_id: Optional[uuid.UUID] = None
    state: SessionState = SessionState.IDLE
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: Optional[datetime] = None
    bytes_received: int = 0
    packets_received: int = 0
    last_sequence: int = 0
    transcript_segments: list[str] = field(default_factory=list)
    full_transcript: str = ""

    def transition(self, new_state: SessionState) -> None:
        if new_state not in ALLOWED_TRANSITIONS.get(self.state, set()):
            raise ValueError(f"Illegal transition {self.state} -> {new_state}")
        self.state = new_state
        if new_state == SessionState.CLOSED:
            self.ended_at = datetime.now(timezone.utc)