#!/usr/bin/env python3
#
# Copyright (c) 2026 Uptrack contributors
# SPDX-License-Identifier: MIT
#
import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("uptrack.py")), run_name="__main__")
