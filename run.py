#!/usr/bin/env python3
"""Start Cell Tracking Studio on the local computer."""

from pathlib import Path
import sys

import uvicorn


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))


if __name__ == "__main__":
    uvicorn.run("celltrack.web.app:app", host="127.0.0.1", port=8000, reload=False)
