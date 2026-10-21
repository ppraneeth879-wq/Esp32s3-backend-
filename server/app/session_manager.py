"""Audio session lifecycle management with explicit state machine."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from .models import Session, SessionState


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._active: Optional[str] = None
        self._listeners: list[Callable[[Session, SessionState, SessionState], None]] = []

    def on_transition(self, cb: Callable[[Session, SessionState, SessionState], None]) -> None:
        self._listeners.append(cb)

    def create_session(self, device_id: Optional[uuid.UUID] = None) -> Session:
        s = Session(device_id=device_id)
        s.state = SessionState.IDLE
        self._sessions[s.session_id] = s
        return s

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def active_session(self) -> Optional[Session]:
        if self._active is None:
            return None
        return self._sessions.get(self._active)

    def transition(self, session: Session, new_state: SessionState) -> None:
        old = session.state
        session.transition(new_state)
        for cb in self._listeners:
            try:
                cb(session, old, new_state)
            except Exception:
                pass
        if new_state == SessionState.RECORDING and self._active is None:
            self._active = session.session_id
        if new_state == SessionState.CLOSED and self._active == session.session_id:
            self._active = None

    def close_session(self, session: Session) -> None:
        if session.state != SessionState.CLOSED:
            # Force through FINISHING if possible
            if session.state in (SessionState.RECORDING, SessionState.TRANSCRIBING):
                try:
                    self.transition(session, SessionState.FINISHING)
                except ValueError:
                    pass
            if session.state != SessionState.CLOSED:
                try:
                    self.transition(session, SessionState.CLOSED)
                except ValueError:
                    session.state = SessionState.CLOSED
                    session.ended_at = datetime.now(timezone.utc)

    def list_sessions(self) -> list[Session]:
        return list(self._sessions.values())