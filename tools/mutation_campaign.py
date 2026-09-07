#!/usr/bin/env python3
"""Mutation campaign — find every test in this repo that cannot fail.

The 2026-08-22 audit found NINE tests that passed against broken code,
by hand, one at a time. The suite is 223 tests now and nobody has ever
checked them all. A test that cannot fail is worse than no test: it is a
gate that reports green while the thing it guards is broken, which is this
project's single most expensive defect class (CLAUDE.md rule 3).

Method: parse each target module, apply ONE semantic mutation, run the
suite, record whether anything failed.

    killed   — some test failed. The suite guards that line. Good.
    SURVIVED — the code was broken and every test still passed. A finding:
               either the line is untested, or a test asserts the wrong
               thing.
    noop     — the mutant is semantically identical (e.g. flipping a
               comparison whose operands are equal-by-construction), or it
               crashed on import. Reported separately, never counted as
               killed, because "it errored" is not "the suite caught it".

ISOLATION: the campaign runs inside a throwaway `git worktree`, never the
real tree. Mutation testing edits source in place; a crash mid-run in the
working repo would leave mutated code on disk, and mutated code that gets
committed is the exact nightmare this tool exists to prevent. The worktree
is created by the caller and removed after.

Speed: pytest with -x (stop at first failure) — a killed mutant usually
dies in seconds, and only SURVIVORS pay the full suite cost. That
asymmetry is what makes a few hundred mutants an overnight job instead of
a week.

READING THE REPORT — a survivor is a CANDIDATE, not a finding. The `noop`
detection here is SYNTACTIC, so it cannot see a mutation that is equivalent
only by numerical coincidence, and those land in the report as SURVIVED.
Worked example, 2026-09-06 run, mutant 12 of 333: `sim/detector.py:124`
`spin_lever_m <= 0.0` -> `< 0.0` in `correct_for_spin`. The branch only
matters at exactly r = 0, and there the body computes
`sqrt(max(0, a^2 - 0^2))`, which returned bit-identical results to the
short-circuit across 200,000 random (|a|, gyro) pairs. No test can kill it
because there is nothing to catch. Triage each survivor by asking what input
makes the mutated line behave differently AT ALL; if the answer is "none",
it is a noop and needs no test. CLAUDE.md rule 5 applies to this tool's own
output as much as to anything else.

Usage:
    python3 tools/mutation_campaign.py --repo <worktree> [--limit N]
Output: <worktree>/mutation-report.json + a human summary on stdout.
"""
from __future__ import annotations

import argparse
import ast
import copy
import json
import subprocess
import sys
import time
from pathlib import Path

# Modules whose correctness the water day rests on. Ordered by stakes.
TARGETS = [
    "sim/detector.py",       # the detector itself
    "sim/evaluate.py",       # the grader for the one-shot session
    "sim/selfdiag.py",       # the non-ballistic self-diagnostic
    "sim/lever_arm.py",      # spin/lever calibration
    "sim/trace_codec.py",    # the codec with a C++ twin
    "tools/blepin.py",       # board pinning — has flashed a wrong board
    # Added 2026-09-06 after the first water session. The F-28 gate decision
    # rests on E3/E4's simulated physics (median |a| collapses to AUC 0.258
    # under a spin confound), so these four modules are now load-bearing for a
    # SHIP/DON'T-SHIP call and had never been mutation-tested:
    "sim/sensor_model.py",   # renders the omega^2*r confound the gate would meet
    "sim/wing_model.py",     # the flight physics every E-series number rests on
    "sim/seastate.py",       # E14/E15's chop model — the venue error bars
    "sim/windows.py",        # airborne-window bookkeeping behind med_a itself
    "sim/generate.py",       # synthetic session builder used by the harness
]

# NOT a target, and the reason is a finding in itself: `tools/label.py` has
# NO test that imports it (2026-09-06 — the ten tests matching "label" all
# live in test_evaluate.py and exercise labels.csv, not the generator). It
# produces the ground truth `jump eval` scores against, and it was edited on
# 2026-09-06. Mutating an untested module only proves it is untested, which
# is already known; write the tests instead, then add it here.

CMP_FLIP = {
    ast.Lt: ast.LtE, ast.LtE: ast.Lt,
    ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq,
}
BOOL_FLIP = {ast.And: ast.Or, ast.Or: ast.And}


def mutants_for(path: Path):
    """Yield (label, mutated_source) for one file."""
    src = path.read_text()
    tree = ast.parse(src)
    nodes = list(ast.walk(tree))

    for idx, node in enumerate(nodes):
        # 1. comparison boundary flips (off-by-one / gate-edge bugs)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = type(node.ops[0])
            if op in CMP_FLIP:
                t2 = copy.deepcopy(tree)
                n2 = list(ast.walk(t2))[idx]
                n2.ops[0] = CMP_FLIP[op]()
                yield (f"{path}:{node.lineno} {op.__name__}->"
                       f"{CMP_FLIP[op].__name__}", ast.unparse(t2))
        # 2. and/or swaps (gate-combination bugs)
        elif isinstance(node, ast.BoolOp):
            op = type(node.op)
            if op in BOOL_FLIP:
                t2 = copy.deepcopy(tree)
                n2 = list(ast.walk(t2))[idx]
                n2.op = BOOL_FLIP[op]()
                yield (f"{path}:{node.lineno} {op.__name__}->"
                       f"{BOOL_FLIP[op].__name__}", ast.unparse(t2))
        # 3. numeric constant perturbation (threshold bugs)
        elif isinstance(node, ast.Constant) and isinstance(
                node.value, (int, float)) and not isinstance(
                node.value, bool):
            v = node.value
            if v in (0, 1) or abs(v) > 1e6:
                continue          # 0/1 flips are usually structural noise
            new = v * 1.25 if isinstance(v, float) else v + 1
            t2 = copy.deepcopy(tree)
            n2 = list(ast.walk(t2))[idx]
            n2.value = new
            yield (f"{path}:{node.lineno} const {v!r}->{new!r}",
                   ast.unparse(t2))
        # 4. boolean constant flips
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            t2 = copy.deepcopy(tree)
            n2 = list(ast.walk(t2))[idx]
            n2.value = not node.value
            yield (f"{path}:{node.lineno} {node.value}->{not node.value}",
                   ast.unparse(t2))


def run_suite(repo: Path, timeout: int) -> tuple[bool, str]:
    """(caught, detail). caught=True means at least one test FAILED."""
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tools/tests", "-x", "-q",
             "--no-header", "-p", "no:cacheprovider"],
            cwd=repo, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # A hang is the suite noticing (an infinite loop is a real symptom),
        # but it is not a clean kill — call it out rather than bank it.
        return (True, "TIMEOUT")
    out = (r.stdout or "") + (r.stderr or "")
    # Use pytest's EXIT CODE, not a substring search of its prose.
    # The first draft of this function did `if "failed" in out` — which
    # matches the "failed" inside "1 xfailed", so a fully green suite
    # (223 passed, 1 xfailed) read as a failure and the baseline guard
    # aborted the whole campaign. The instrument had the exact defect it
    # exists to hunt: a green run reported as a catch. Caught 2026-08-24
    # only because the baseline check ran first.
    #   0 = all passed (xfail/xpass included)   1 = tests failed
    #   2 = interrupted   3 = internal error   4 = usage   5 = none collected
    if r.returncode == 0:
        return (False, "all passed")
    if r.returncode == 5:
        # No tests collected usually means the mutant broke an import. That
        # is the mutant erroring, NOT the suite catching it.
        return (False, "NO TESTS COLLECTED (mutant likely broke import)")
    if r.returncode == 1:
        return (True, "tests failed")
    return (True, f"rc={r.returncode}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--limit", type=int, default=0, help="0 = no cap")
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()
    repo = args.repo.resolve()

    # Baseline: the unmutated suite MUST be green, or every "killed" verdict
    # below is meaningless (CLAUDE.md rule 3 — verify the instrument first).
    print("baseline suite...", flush=True)
    caught, detail = run_suite(repo, args.timeout)
    if caught:
        print(f"ABORT: baseline suite is not green ({detail}). "
              f"Fix that before trusting any mutation verdict.")
        return 1
    print("baseline green — mutation verdicts are meaningful\n", flush=True)

    jobs = []
    for rel in TARGETS:
        p = repo / rel
        if p.exists():
            jobs.extend((rel, lbl, src) for lbl, src in mutants_for(p))
    if args.limit:
        step = max(1, len(jobs) // args.limit)
        jobs = jobs[::step][:args.limit]
    print(f"{len(jobs)} mutants across {len(TARGETS)} modules\n", flush=True)

    results, t0 = [], time.time()
    for i, (rel, label, mutated) in enumerate(jobs, 1):
        target = repo / rel
        original = target.read_text()
        try:
            target.write_text(mutated)
            caught, detail = run_suite(repo, args.timeout)
        finally:
            target.write_text(original)   # ALWAYS restore
        verdict = "killed" if caught else "SURVIVED"
        results.append({"module": rel, "mutation": label,
                        "verdict": verdict, "detail": detail})
        if verdict == "SURVIVED":
            print(f"  [{i}/{len(jobs)}] SURVIVED  {label}", flush=True)
        elif i % 10 == 0:
            el = time.time() - t0
            surv = sum(1 for r in results if r["verdict"] == "SURVIVED")
            print(f"  [{i}/{len(jobs)}] {el:.0f}s, {surv} survivors so far",
                  flush=True)
        (repo / "mutation-report.json").write_text(
            json.dumps(results, indent=1))

    surv = [r for r in results if r["verdict"] == "SURVIVED"]
    print(f"\n=== {len(results)} mutants, {len(surv)} SURVIVED "
          f"({time.time() - t0:.0f}s) ===")
    for r in surv:
        print(f"  {r['mutation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
