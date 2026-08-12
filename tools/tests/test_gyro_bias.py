"""Pre-takeoff gyro bias subtraction — firmware/include/gyro_bias.h.

Mirrors tools/tests/test_store_host.py's pattern: a g++-built harness
(firmware/test/gyro_bias_harness.cpp) run as a subprocess, one assertion per
PASS/FAIL line it prints.

WHY THIS EXISTS AT ALL, since it was nearly missed: docs/gyro-sim-plan.md §4
lists a pre-takeoff stationary bias subtraction as a **mandatory** pipeline
step, and DECISIONS.md #29 folds it into the gyro configuration. The first
cut of the omega^2*r correction shipped without it. A MEMS gyro's zero-rate
level is spec'd at ±10 dps, the correction squares omega, and the resulting
2*w*b cross-term lands straight in the reported height — about 4.7% of the
correction term from 7 dps of offset at a 300 dps spin.

The load-bearing case is `baseline_frozen_while_airborne`: the estimator must
stop updating in flight, or a spin poisons the very baseline used to correct
that spin.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HARNESS = os.path.join(ROOT, "firmware", "test", "gyro_bias_harness.cpp")


def _gxx() -> str:
    gxx = shutil.which("g++") or shutil.which("c++")
    if not gxx:
        pytest.skip("no g++/c++ on this machine")
    return gxx


@pytest.fixture(scope="module")
def harness_output() -> str:
    """Build and run the harness once; return its stdout."""
    with tempfile.TemporaryDirectory() as td:
        binp = os.path.join(td, "gyro_bias_harness")
        build = subprocess.run(
            [_gxx(), "-std=c++14", "-Wall", "-Wextra",
             "-I", os.path.join(ROOT, "firmware", "include"),
             HARNESS, "-o", binp],
            capture_output=True, text=True,
        )
        assert build.returncode == 0, f"harness failed to compile:\n{build.stderr}"
        run = subprocess.run([binp], capture_output=True, text=True)
        # Print so `pytest -s` shows the documented cost line.
        print(run.stdout)
        assert run.returncode == 0, f"harness reported failures:\n{run.stdout}"
        return run.stdout


CHECKS = [
    "seeds_immediately_biased_rest_reads_zero",
    "true_rate_reads_true_after_convergence",
    "baseline_frozen_while_airborne",
    "spin_still_reads_true_after_long_flight",
    "single_axis_bias_fully_removed",
    "documents_uncorrected_bias_cost_pct",
    # pinned from the 2026-08-12 gyro-crash-hunt (each confirmed by repro
    # before the guard existed; see the harness comments for mechanisms)
    "railed_samples_never_poison_baseline",
    "moving_first_sample_must_not_seed",
    "seed_happens_on_first_plausible_quiet_sample",
    "railed_omega_passes_raw_through_instead_of_freefall",
    "legitimate_spin_correction_still_applies",
]


@pytest.mark.parametrize("check", CHECKS)
def test_gyro_bias_check(harness_output: str, check: str):
    assert f"PASS {check}" in harness_output, (
        f"{check} did not pass:\n{harness_output}"
    )


def test_harness_reports_overall_pass(harness_output: str):
    assert "RESULT: PASS" in harness_output


def test_every_harness_check_is_listed_here(harness_output: str):
    """Guard against the harness growing a check that this file silently
    ignores — a new assertion nobody runs is worse than no assertion."""
    printed = {ln.split(None, 1)[1] for ln in harness_output.splitlines()
               if ln.startswith("PASS ") or ln.startswith("FAIL ")}
    assert printed == set(CHECKS), (
        f"harness checks and CHECKS disagree.\n"
        f"  only in harness: {sorted(printed - set(CHECKS))}\n"
        f"  only in CHECKS:  {sorted(set(CHECKS) - printed)}"
    )
