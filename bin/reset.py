#!/usr/bin/env python3
"""Dev convenience only: wipes local storage so `process.py` can be
re-run from a clean slate. Does NOT touch tasks/, data/, or checks/."""
from __future__ import annotations

import glob
import os
import sys

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    db_path = os.path.join(APP_ROOT, "storage", "app.sqlite")
    if os.path.isfile(db_path):
        os.remove(db_path)
        print(f"Removed {db_path}")

    keep_cache = "--keep-responses" in sys.argv
    if not keep_cache:
        responses = glob.glob(os.path.join(APP_ROOT, "storage", "responses", "*.json"))
        for f in responses:
            os.remove(f)
        print(f"Removed {len(responses)} cached model responses")
    else:
        print("Kept cached model responses (pass without --keep-responses to clear them too)")


if __name__ == "__main__":
    main()
