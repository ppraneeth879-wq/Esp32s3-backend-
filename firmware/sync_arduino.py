"""Copy the shared firmware sources into the Arduino IDE sketch folder.

The repository has two build systems:

  * PlatformIO   -> firmware/src, firmware/include
  * Arduino IDE  -> firmware/arduino/LifeLog

They must hold the same code, but the Arduino sketch needs exactly ONE entry
point. A `.ino` file IS the entry point, so `src/main.cpp` is copied to
`LifeLog.ino` and a `main.cpp` is never placed in the sketch folder - two
entry points means every global and function is defined twice and the link
fails with "multiple definition of ...".

    python firmware/sync_arduino.py
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

FIRMWARE = Path(__file__).resolve().parent
SRC = FIRMWARE / "src"
INC = FIRMWARE / "include"
SKETCH = FIRMWARE / "arduino" / "LifeLog"

# main.cpp becomes the .ino entry point, so it is never copied as main.cpp.
ENTRY_POINT = "main.cpp"
INO_NAME = "LifeLog.ino"


def main() -> int:
    if not SKETCH.exists():
        print(f"sketch folder not found: {SKETCH}")
        return 1

    copied = []

    for header in sorted(INC.glob("*.h")):
        target = SKETCH / header.name
        shutil.copy2(header, target)
        copied.append(target.name)

    for source in sorted(SRC.glob("*.cpp")):
        if source.name == ENTRY_POINT:
            target = SKETCH / INO_NAME
        else:
            target = SKETCH / source.name
        shutil.copy2(source, target)
        copied.append(target.name)

    # A stray main.cpp in the sketch folder is a duplicate entry point.
    stray = SKETCH / ENTRY_POINT
    if stray.exists():
        stray.unlink()
        print(f"removed the duplicate entry point: {stray.name}")

    print(f"synced {len(copied)} files into {SKETCH}")
    for name in copied:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
