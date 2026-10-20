#!/usr/bin/env python3
"""Backward-compatible entry point; use ``juice_meter.py`` for new installs."""
from juice_meter import main

if __name__ == "__main__":
    raise SystemExit(main())
