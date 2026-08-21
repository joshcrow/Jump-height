"""Permanent regression guard for the float32 timebase defect (B3,
docs/glue-and-forget.md §3a).

BACKGROUND. jump_detector.h's t_s (and takeoff_time_/last_low_time_/
JumpEvent::takeoff_time_s) used to be float. An absolute, ever-growing
uptime crushed through float32 loses sub-ms resolution starting at 18.2 h,
and by 6 months (1.0 s grid) silently DROPS ~12% of real jumps outright —
with no symptom on any surface. The fix was float -> double throughout.

THE FALSIFIER (proven to work BEFORE the fix, confirmed still holds after):
offset every golden-trace timestamp (data/example_session.csv) by
+604,800 s (one week) and diff the C++ firmware detector (host_test.cpp,
jump_detector.h with real params.gen.h values) against the Python mirror
(sim/detector.py via sim/golden.py, native IEEE doubles throughout). Before
the fix this diverges (measured: the C++ side's first jump read
airtime_raw=0.625s / height=0.519m against Python's 0.600s / 0.480m — a
0.025 s gap, 25x the ./tools/jump simtest parity tolerance of 0.002). After
the fix both sides agree exactly, matching every other CSV replay in this
suite. This file pins that reconvergence permanently: if t_s (or any of its
three siblings) ever regresses back to float, THIS test starts failing —
none of the other parity tests would (they replay the same CSV at its
native, un-offset timestamps, comfortably inside float32's precision, so
they cannot see the bug at all).

Mirrors ./tools/jump simtest's own C++/Python parity check (see cmd_simtest
in tools/jump, and firmware/test/host_test.cpp's file comment) rather than
importing tools/jump's internals — same independent-harness convention
test_trace_codec.py and test_store_host.py already use.

Run via ./tools/jump simtest, or directly:
    python3 -m pytest tools/tests/test_timebase_falsifier.py -q
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

HOST_TEST_SRC = REPO / "firmware" / "test" / "host_test.cpp"
GOLDEN_TRACE = REPO / "data" / "example_session.csv"
OFFSET_S = 604800.0  # one week — the falsifier glue-and-forget.md §3a names
PARITY_TOL = 0.002   # same bar cmd_simtest's own C++/Python check uses


def _gxx() -> str:
    gxx = shutil.which("g++") or shutil.which("c++")
    if not gxx:
        raise unittest.SkipTest("no g++/c++ on this machine")
    return gxx


def _build_host_test(tmpdir: str) -> str:
    binp = str(Path(tmpdir) / "host_test_falsifier")
    r = subprocess.run(
        [_gxx(), "-std=c++14", "-Wall", "-Wextra",
         "-I", str(REPO / "firmware" / "include"),
         str(HOST_TEST_SRC), "-o", binp],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"host_test.cpp failed to compile:\n{r.stderr}")
    return binp


def _offset_golden_trace(offset_s: float, tmpdir: str) -> str:
    """A copy of the golden trace with every t_s bumped by `offset_s`,
    same 4-decimal formatting as the checked-in file."""
    lines = GOLDEN_TRACE.read_text().splitlines()
    header, rows = lines[0], lines[1:]
    out = [header]
    for ln in rows:
        if not ln.strip():
            continue
        cells = ln.split(",")
        cells[0] = f"{float(cells[0]) + offset_s:.4f}"
        out.append(",".join(cells))
    path = Path(tmpdir) / "offset_trace.csv"
    path.write_text("\n".join(out) + "\n")
    return str(path)


def _jump_tuples(output: str) -> list[list[float]]:
    """Parse 'JUMP k=v k=v ...' lines into ordered lists of floats — the
    exact format both host_test.cpp and sim/golden.py print (see either
    file's header comment)."""
    return [[float(tok.split("=")[1]) for tok in ln.split()[1:]]
            for ln in output.strip().splitlines() if ln.startswith("JUMP")]


class TestTimebaseFalsifier(unittest.TestCase):
    def test_plus_one_week_offset_reconverges(self) -> None:
        """The permanent regression guard: with the golden trace shifted a
        week into the future, the C++ and Python detectors must still agree
        exactly. (MUTATION CHECK performed by hand during review, not by
        this test — reverting jump_detector.h's t_s to float here makes the
        C++ side read airtime_raw=0.625s/height=0.519m against Python's
        0.600s/0.480m on the first jump, which fails the assertion below;
        restoring double makes it pass again.)"""
        with tempfile.TemporaryDirectory() as td:
            binp = _build_host_test(td)
            offset_csv = _offset_golden_trace(OFFSET_S, td)

            with open(offset_csv) as f:
                cpp = subprocess.run([binp], stdin=f, capture_output=True, text=True)
            py = subprocess.run([sys.executable, str(REPO / "sim" / "golden.py"), offset_csv],
                                capture_output=True, text=True)

            self.assertEqual(cpp.returncode, 0, cpp.stderr)
            self.assertEqual(py.returncode, 0, py.stderr)

            cpp_jumps = _jump_tuples(cpp.stdout)
            py_jumps = _jump_tuples(py.stdout)

            self.assertGreater(len(cpp_jumps), 0, "the offset trace must still produce jumps")
            self.assertEqual(len(cpp_jumps), len(py_jumps),
                             f"C++ found {len(cpp_jumps)} jumps, Python found {len(py_jumps)} "
                             f"— the two detectors disagree on COUNT at t0+{OFFSET_S:.0f}s, "
                             "the float32 timebase defect returned")
            for i, (c, p) in enumerate(zip(cpp_jumps, py_jumps)):
                for j, (cv, pv) in enumerate(zip(c, p)):
                    self.assertLessEqual(
                        abs(cv - pv), PARITY_TOL,
                        f"jump {i} field {j}: C++={cv} vs Python={pv} at t0+{OFFSET_S:.0f}s "
                        f"(tolerance {PARITY_TOL}) — the float32 timebase defect returned")


if __name__ == "__main__":
    unittest.main()
