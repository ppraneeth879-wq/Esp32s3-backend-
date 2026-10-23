"""Check that every config profile loads and that a typo is rejected."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import ConfigError, load_settings

for profile in ("development", "production", "test"):
    try:
        s = load_settings(profile)
        print(
            f"  {profile:12} OK   summary_model={s.summary_model} "
            f"window={s.summary_window_seconds} budget={s.summary_daily_request_budget} "
            f"create_actions={s.extraction_create_actions}"
        )
    except ConfigError as e:
        print(f"  {profile:12} REJECTED: {e}")
    except Exception as e:
        print(f"  {profile:12} ERROR: {type(e).__name__}: {e}")

print("\ntypo detection:")
tmp = Path(tempfile.mkdtemp())
(tmp / "config").mkdir()
(tmp / "config" / "typo.yaml").write_text(
    'host: "0.0.0.0"\nsumary_model: "x"\n', encoding="utf-8"
)
try:
    load_settings("typo", base_dir=tmp)
    print("  NOT rejected (bad)")
except ConfigError as e:
    print(f"  rejected: {e}")
