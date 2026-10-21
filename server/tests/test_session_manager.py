import sys
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import SessionState
from app.session_manager import SessionManager


def test_transitions():
    sm = SessionManager()
    s = sm.create_session(uuid.uuid4())
    sm.transition(s, SessionState.CONNECTING)
    sm.transition(s, SessionState.CONNECTED)
    sm.transition(s, SessionState.RECORDING)
    assert s.state == SessionState.RECORDING
    sm.close_session(s)
    assert s.state == SessionState.CLOSED


def test_illegal_transition():
    sm = SessionManager()
    s = sm.create_session()
    try:
        sm.transition(s, SessionState.RECORDING)
        assert False
    except ValueError:
        pass