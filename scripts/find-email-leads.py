#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE))

from job_research.email_leads import main


if __name__ == "__main__":
    raise SystemExit(main())
