import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.storage import Storage


def test_storage(tmp_path):
    st = Storage(tmp_path)
    sid = "sess-1"
    d = st.create_session_dir(sid)
    assert d.exists()
    st.write_transcript(sid, "hello world")
    assert st.transcript_path(sid).read_text() == "hello world"
    st.write_metadata(sid, {"a": 1})
    assert st.read_metadata(sid) == {"a": 1}