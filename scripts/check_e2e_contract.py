#!/usr/bin/env python3
"""Check or regenerate the test-only real-Odoo verification contract."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.integration.real_odoo.contracts import (  # noqa: E402
    check_matrix_document,
    render_matrix_document,
)
from tests.unit.test_cli_output_modes import PUBLIC_LEAF_CASES  # noqa: E402

MATRIX = ROOT / "openspec/changes/add-reproducible-odoo19-e2e-harness/command-matrix.md"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="rewrite the generated matrix")
    args = parser.parse_args()
    if args.write:
        MATRIX.write_text(
            render_matrix_document(MATRIX.read_text(encoding="utf-8"), PUBLIC_LEAF_CASES),
            encoding="utf-8",
        )
    else:
        check_matrix_document(str(MATRIX), PUBLIC_LEAF_CASES)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
