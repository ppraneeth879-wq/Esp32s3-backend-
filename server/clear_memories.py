"""Delete every memory row. The tasks in `actions` are left alone.

    cd server
    python clear_memories.py            # only the memories
    python clear_memories.py --all      # memories, actions and the vector store
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import get_conn
from app.dotenv import load_dotenv
from app.vector_store import vector_store

load_dotenv(Path(__file__).resolve().parent / ".env")


def main() -> int:
    clear_actions = "--all" in sys.argv

    conn = get_conn()
    memories = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
    actions = conn.execute("SELECT COUNT(*) c FROM actions").fetchone()["c"]
    print(f"before: {memories} memories, {actions} actions")

    conn.execute("DELETE FROM memories")
    if clear_actions:
        conn.execute("DELETE FROM actions")
    conn.commit()
    conn.close()
    print("cleared the memories" + (" and the actions" if clear_actions else ""))

    try:
        vector_store.reset()
        print(f"cleared the ChromaDB collection ({vector_store.count()} vectors left)")
    except Exception as e:
        print(f"could not clear the vector store: {type(e).__name__}: {e}")

    conn = get_conn()
    memories = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
    actions = conn.execute("SELECT COUNT(*) c FROM actions").fetchone()["c"]
    conn.close()
    print(f"after:  {memories} memories, {actions} actions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
