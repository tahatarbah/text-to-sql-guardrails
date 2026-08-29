#!/usr/bin/env python
"""Smoke-test a single question through the pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.pipeline import run_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one Text-to-SQL pipeline query")
    parser.add_argument("question", nargs="?", default="How many rows are in each table?")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    result = run_pipeline(args.question, execute=not args.validate_only)
    print(json.dumps(result.to_dict(), indent=2, default=str))
    return 0 if result.status in ("executed", "validated") else 1


if __name__ == "__main__":
    raise SystemExit(main())
