#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Relocation stub for verify_dual_fp8.py (moved to standalone Route 2 repository)."""

import sys

PRE_SPLIT_TAG = "v1-pre-split"
PRE_SPLIT_COMMIT = "5761a1953358fdef4dcad05bfecfe6165e44a1a9"


def main():
    print(
        "verify_dual_fp8.py and its JSON archive have moved to the standalone "
        "Route 2 research repository (link to be added upon publication).\n"
        f"Pre-split snapshot in this repo: git checkout {PRE_SPLIT_TAG} ({PRE_SPLIT_COMMIT[:7]})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
