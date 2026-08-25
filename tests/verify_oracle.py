#!/usr/bin/env python3
"""Artwork oracle verification harness.

Executes `theater-art` on the test fixture inputs and compares pixel-by-pixel
against the reference Pillow-rendered corpus stored in tests/fixtures/artwork_reference/.
"""

from __future__ import annotations

import hashlib
import json
import operator
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "artwork_reference"
MANIFEST_FILE = FIXTURES_DIR / "manifest.json"

MAX_ALLOWED_AVG_MAE = 0.50
# Peak single-channel difference threshold, derived from broadband fixtures to
# absorb floating-point variation across toolchains.
MAX_ALLOWED_PEAK_DELTA = 18


def compare_argb_buffers(actual: bytes, expected: bytes) -> tuple[float, float, float, int]:
    """Return per-channel mean absolute errors and the peak RGB delta.

    Raise ValueError when the buffers have different lengths.
    """
    if len(actual) != len(expected):
        raise ValueError(
            f"Buffer size mismatch: actual {len(actual)} bytes, expected {len(expected)} bytes"
        )

    if not actual:
        return (0.0, 0.0, 0.0, 0)

    pixel_count = len(actual) // 4
    mae: list[float] = []
    peak = 0
    # R, G, B within each BGRA pixel. Alpha is constant and carries no signal.
    for offset in (2, 1, 0):
        deltas = bytes(map(abs, map(operator.sub, actual[offset::4], expected[offset::4])))
        mae.append(sum(deltas) / pixel_count)
        peak = max(peak, max(deltas))

    return (mae[0], mae[1], mae[2], peak)


def main() -> None:
    if not MANIFEST_FILE.exists():
        sys.exit(f"Error: Manifest not found at {MANIFEST_FILE}")

    manifest = json.loads(MANIFEST_FILE.read_text())

    # Locate theater-art. THEATER_ART_BIN wins so bin/check can point the oracle at an
    # instrumented build; without it this silently verifies the wrong binary.
    repo_root = Path(__file__).parent.parent
    env_bin = os.environ.get("THEATER_ART_BIN")
    if env_bin:
        art_bin = Path(env_bin)
        if not (art_bin.is_file() and os.access(art_bin, os.X_OK)):
            sys.exit(f"Error: THEATER_ART_BIN is set but not executable: {art_bin}")
    else:
        art_bin = repo_root / "src" / "theater_mode" / "art" / "theater-art"
        if not art_bin.exists():
            which = shutil.which("theater-art")
            if which:
                art_bin = Path(which)
            else:
                sys.exit(f"Error: theater-art binary not found at {art_bin}")

    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    if verbose:
        print(f"[oracle] Verifying {len(manifest)} test cases using {art_bin}...")

    all_passed = True

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        for case_id, info in manifest.items():
            src_file = FIXTURES_DIR / info["src_path"]
            ref_file = FIXTURES_DIR / info["ref_file"]
            target_w, target_h = info["target_size"]
            dim_millis = round(max(0.0, min(1.0, float(info["dim_factor"]))) * 1000)

            out_file = tmp_path / f"{case_id}_out.argb"

            cmd = [
                str(art_bin),
                str(src_file),
                str(out_file),
                str(target_w),
                str(target_h),
                str(dim_millis),
            ]

            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                print(f"  [FAIL] {case_id}: theater-art failed with code {proc.returncode}")
                if proc.stderr:
                    print(f"         stderr: {proc.stderr.strip()}")
                all_passed = False
                continue

            if not out_file.exists():
                print(f"  [FAIL] {case_id}: Output file not generated")
                all_passed = False
                continue

            actual_bytes = out_file.read_bytes()
            expected_bytes = ref_file.read_bytes()

            # The manifest records what the generator produced, so a fixture that was
            # corrupted or half-regenerated fails as itself instead of as a pipeline drift.
            if (
                len(expected_bytes) != info["byte_len"]
                or hashlib.sha256(expected_bytes).hexdigest() != info["sha256"]
            ):
                print(f"  [FAIL] {case_id}: reference fixture does not match its manifest entry")
                all_passed = False
                continue

            mae_r, mae_g, mae_b, max_delta = compare_argb_buffers(actual_bytes, expected_bytes)
            avg_mae = (mae_r + mae_g + mae_b) / 3.0

            passed = (avg_mae <= MAX_ALLOWED_AVG_MAE) and (max_delta <= MAX_ALLOWED_PEAK_DELTA)
            status = "OK" if passed else "FAIL"

            if verbose or not passed:
                print(
                    f"  [{status}] {case_id:<26} -> "
                    f"MAE: R={mae_r:.2f} G={mae_g:.2f} B={mae_b:.2f} (avg={avg_mae:.2f}), "
                    f"max_delta={max_delta} [{info['note']}]"
                )

            if not passed:
                all_passed = False
                print(
                    f"         GATE FAILED: avg_mae={avg_mae:.2f} (limit {MAX_ALLOWED_AVG_MAE}), "
                    f"max_delta={max_delta} (limit {MAX_ALLOWED_PEAK_DELTA})"
                )

    if not all_passed:
        sys.exit(
            "[oracle] Verification failed: output diverged beyond acceptable perceptual bounds."
        )

    print(f"[oracle] Reference corpus passed ({len(manifest)} cases).")


if __name__ == "__main__":
    main()
