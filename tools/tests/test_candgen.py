"""Tests for sim/candgen.py — the CG-1 pop -> partial unload -> landing
candidate generator (spec section 3, tests T-A1..T-A11).

Every trace here is SYNTHETIC, built from a stated shape, so each test knows
its answer independently of the code under test. No private session data.
"""

from __future__ import annotations

import ast
import inspect
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

import candgen as C  # noqa: E402


def build(spec, hz=50.0, t0=100.0):
    """(times, mag) from [(duration_s, level_g), ...]; times to the ms like
    the device's trace."""
    dt = 1.0 / hz
    t, m = [], []
    k = 0
    for dur, level in spec:
        n = int(round(dur * hz))
        for _ in range(n):
            t.append(round(t0 + k * dt, 3))
            m.append(level)
            k += 1
    return t, m


def jump(pop_g=3.0, unload_g=0.6, unload_s=0.8, land_g=6.0, lead=3.0, trail=3.0):
    return [(lead, 1.0), (0.1, pop_g), (unload_s, unload_g), (0.1, land_g), (trail, 1.0)]


def one_window(t, m, hz=50.0, **kw):
    return C.MotionWindow(window_id="test", boot="last", source="trace_mag",
                          log_hz=hz, t_s=t, mag_g=m, **kw)


def run(t, m, p=None, hz=50.0):
    return C.generate_all(C.windows_from_trace(t, m, hz), p or C.PRESETS["vest-R"])


# ------------------------------------------------------------------ T-A1

@pytest.mark.parametrize("hz", [50.0, 100.0])
def test_a1_one_pop_unload_impact_gives_exactly_one_candidate(hz):
    t, m = build(jump(), hz)
    got = run(t, m, hz=hz)
    assert len(got) == 1
    c = got[0]
    dt = 1.0 / hz
    pop0 = 100.0 + 3.0
    u0, u1 = pop0 + 0.1, pop0 + 0.1 + 0.8 - dt     # first/last constructed 0.6 g sample
    land = pop0 + 0.9
    half = C.PRESETS["vest-R"].smooth_s / 2       # the stated smoothing half-width
    assert abs(c.pop_t_s - pop0) <= dt + 1e-9
    assert abs(c.unload_t0_s - (u0 + half)) <= dt + 1e-9
    assert abs(c.unload_t1_s - (u1 - half)) <= dt + 1e-9
    assert abs(c.land_t_s - land) <= dt + 1e-9
    assert c.pop_g == 3.0 and c.land_peak_g == 6.0
    assert c.unload_min_g == pytest.approx(0.6)
    assert not c.railed and c.boot == "last"


# ------------------------------------------------------------------ T-A2

def test_a2_gap_inside_the_unload_gives_no_candidate():
    t, m = build([(3.0, 1.0), (0.1, 3.0), (0.6, 0.6)])
    t2, m2 = build([(0.6, 0.6), (0.1, 6.0), (3.0, 1.0)], t0=t[-1] + 0.6)  # 0.6 s gap
    assert run(t + t2, m + m2) == []
    # the same samples with no gap ARE a candidate — the gap is what kills it
    t3, m3 = build([(3.0, 1.0), (0.1, 3.0), (1.2, 0.6), (0.1, 6.0), (3.0, 1.0)])
    assert len(run(t3, m3)) == 1


# ------------------------------------------------------------------ T-A3

def test_a3_a_reboot_just_before_an_unload_cannot_lend_it_a_pop():
    # boot 1 ends on a pop; boot 2 (t restarts near 0) begins in the unload
    t1, m1 = build([(3.0, 1.0), (0.3, 3.5)], t0=5000.0)
    t2, m2 = build([(0.8, 0.6), (0.1, 6.0), (3.0, 1.0)], t0=12.0)
    t, m = t1 + t2, m1 + m2
    assert run(t, m) == []
    # also when handed to generate() as ONE window: it re-segments itself
    assert C.generate(one_window(t, m), C.PRESETS["vest-R"]) == []


# ------------------------------------------------------------------ T-A4

def test_a4_two_unload_runs_before_one_impact_give_one_candidate():
    # merged (separated by 0.1 s of 1.0 g)
    t, m = build([(3.0, 1.0), (0.1, 3.0), (0.4, 0.6), (0.1, 1.0), (0.4, 0.6),
                  (0.1, 6.0), (3.0, 1.0)])
    assert len(run(t, m)) == 1
    # not merged (0.5 s apart) but sharing the one landing
    t, m = build([(3.0, 1.0), (0.1, 3.0), (0.4, 0.6), (0.5, 1.0), (0.4, 0.6),
                  (0.1, 6.0), (3.0, 1.0)])
    got = run(t, m)
    assert len(got) == 1
    assert len({c.land_t_s for c in got}) == 1


# ------------------------------------------------------------------ T-A5

def test_a5_an_impact_between_two_runs_keeps_them_separate():
    t, m = build([(3.0, 1.0), (0.1, 3.0), (0.6, 0.6), (0.06, 6.0), (0.6, 0.6),
                  (0.1, 6.0), (3.0, 1.0)])
    got = run(t, m)
    assert len(got) == 2
    assert got[0].land_t_s < got[1].unload_t0_s
    # Without the impact (a 1.0 g bump of the same length) they merge into one.
    t, m = build([(3.0, 1.0), (0.1, 3.0), (0.6, 0.6), (0.06, 1.0), (0.6, 0.6),
                  (0.1, 6.0), (3.0, 1.0)])
    assert len(run(t, m)) == 1


# ------------------------------------------------------------------ T-A6

def test_a6_the_railed_flag_fires_on_a_railed_landing_only():
    t, m = build(jump(land_g=20.0))
    (c,) = run(t, m)
    assert c.railed and c.land_peak_g == 20.0
    t, m = build(jump(land_g=6.0))
    (c,) = run(t, m)
    assert not c.railed


# ------------------------------------------------------------------ T-A7

FROZEN_DEFAULTS = {
    "unload_g": 0.70, "smooth_s": 0.10, "merge_s": 0.20, "min_unload_s": 0.30,
    "max_unload_s": 6.00, "pop_g": 2.00, "pop_win_s": 1.00, "land_g": 4.00,
    "land_win_s": 1.00, "rail_g": 15.4,
}


def test_a7_defaults_are_pinned_to_the_generator_version():
    # A changed default fails here unless GEN_VERSION changed with it.
    if C.GEN_VERSION == "cg-1":
        assert asdict(C.CGParams()) == FROZEN_DEFAULTS
    assert C.PRESETS["vest-R"] == C.CGParams()
    assert C.PRESETS["vest-L"] == C.CGParams(unload_g=0.8, pop_g=1.5, land_g=2.5)


FROZEN_FIXED = {
    "POP_LEAD_S": 0.05, "LAND_LEAD_S": 0.05, "LAND_PEAK_WIN_S": 0.30,
    "MIN_SEGMENT_SAMPLES": 10, "SMOOTH_EPS_S": 1e-6, "MAX_GAP_S": 0.5,
    "BOOT_RESET_S": 1.0,
}


def test_a7_fixed_constants_are_pinned_to_the_generator_version():
    # The constants outside CGParams shape the output too (review S4: moving
    # POP_LEAD_S to 0.5 or LAND_PEAK_WIN_S to 0.10 used to fail no test, and
    # ride_loop re-ran nothing). A change fails here unless GEN_VERSION moved.
    if C.GEN_VERSION == "cg-1":
        assert C.fixed_constants() == FROZEN_FIXED


def test_a7_the_fixed_constants_are_the_ones_generate_reads():
    # fixed_constants() must report the live values, not copies of them.
    src = inspect.getsource(C)
    for name in ("POP_LEAD_S", "LAND_LEAD_S", "LAND_PEAK_WIN_S", "MIN_SEGMENT_SAMPLES",
                 "_SMOOTH_EPS_S", "MAX_GAP_S"):
        assert src.count(name) >= 3, name     # defined/imported, used, reported
    import score
    assert C.fixed_constants()["MAX_GAP_S"] == score.MAX_GAP_S
    assert C.fixed_constants()["BOOT_RESET_S"] == score.BOOT_RESET_S


def test_a7_params_id_is_stable_and_changes_on_any_field():
    a = C.params_id(C.CGParams())
    assert a == C.params_id(C.CGParams()) and len(a) == 8
    for k, v in asdict(C.CGParams()).items():
        assert C.params_id(replace(C.CGParams(), **{k: v + 0.01})) != a, k


# ------------------------------------------------------------------ T-A8

def test_a8_generate_sees_only_the_window_and_the_params():
    sig = inspect.signature(C.generate)
    assert list(sig.parameters) == ["win", "p"]
    assert sig.parameters["win"].annotation in ("MotionWindow", C.MotionWindow)
    assert sig.parameters["p"].annotation in ("CGParams", C.CGParams)
    tree = ast.parse((REPO / "sim" / "candgen.py").read_text())
    imported, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    for forbidden in ("surfr_match", "fitread", "candctx"):
        assert not any(forbidden in m for m in imported), forbidden
    for forbidden in ("load_surfr", "load_garmin", "load_device_jumps", "jumps_total"):
        assert forbidden not in names, forbidden


# ------------------------------------------------------------------ T-A9

def test_a9_smoothing_is_never_zero_padded_at_a_segment_edge():
    t, m = build([(2.0, 1.0)])
    sm = C._smooth(t, m, 0.10)
    assert sm[0] == pytest.approx(1.0) and sm[-1] == pytest.approx(1.0)
    # a segment that starts at 1.0 g, right after a pop in the previous
    # segment, has no unload at its edge
    t1, m1 = build([(3.0, 1.0), (0.1, 3.0)])
    t2, m2 = build([(1.0, 1.0), (0.1, 6.0), (2.0, 1.0)], t0=t1[-1] + 0.6)
    assert run(t1 + t2, m1 + m2, C.PRESETS["vest-L"]) == []


def test_a9_smoothing_window_is_time_based_across_an_irregular_step():
    # A 0.106 s forward step inside a segment (measured on the 2026-09-14
    # trace): samples across it are NOT averaged into the window.
    t = [0.0, 0.02, 0.04, 0.146, 0.166, 0.186]
    m = [3.0, 3.0, 3.0, 0.3, 0.3, 0.3]
    sm = C._smooth(t, m, 0.10)
    assert sm[3] == pytest.approx(0.3)


# ----------------------------------------------------------------- T-A10

def test_a10_event_windows_fail_loudly():
    with pytest.raises(NotImplementedError):
        C.windows_from_events("anything")


# ----------------------------------------------------------------- T-A11

def test_a11_a_short_event_window_truncates_never_drops():
    t, m = build([(0.2, 1.0), (0.1, 3.0), (0.8, 0.6), (0.1, 6.0), (0.2, 1.0)])
    win = one_window(t, m, pre_s=0.3, post_s=0.3)
    win.source = "six_axis"
    got = C.generate(win, C.PRESETS["vest-R"])
    assert len(got) == 1
    assert got[0].truncated_pre and got[0].truncated_post
    # the same samples as a trace segment are not "truncated": a segment
    # edge is the start of motion, not a buffer limit
    (c,) = run(t, m)
    assert not c.truncated_pre and not c.truncated_post


# ------------------------------------------------------------ other checks

def test_backward_jitter_is_counted_inside_a_candidate():
    t, m = build(jump())
    i = t.index(round(103.5, 3))
    t[i] = round(t[i] - 0.06, 3)        # a sub-second backward step mid-unload
    (c,) = run(t, m)
    assert c.jitter_steps_inside >= 1


def test_decimate_takes_every_other_row():
    t, m = build([(1.0, 1.0)], hz=100.0)
    dt_, dm = C.decimate(t, m, 2)
    assert len(dt_) == 50 and dt_[1] - dt_[0] == pytest.approx(0.02)


def test_the_grid_is_the_spec_grid_and_contains_both_presets():
    g = C.grid_params()
    assert len(g) == 72
    assert C.PRESETS["vest-R"] in g and C.PRESETS["vest-L"] in g


def test_earlier_boot_rows_are_labelled_earlier():
    t1, m1 = build(jump(), t0=5000.0)
    t2, m2 = build(jump(), t0=10.0)
    wins = C.windows_from_trace(t1 + t2, m1 + m2, 50.0, boot_split=len(t1))
    got = C.generate_all(wins, C.PRESETS["vest-R"])
    assert [c.boot for c in got] == ["earlier", "last"]


def test_three_hours_at_100_hz_runs_well_inside_a_minute():
    # Spec section 6.6: < 60 s per session at 100 Hz. A synthetic 3 h trace
    # with a jump every 30 s, both presets.
    hz = 100.0
    spec = []
    for _ in range(360):
        spec += [(26.0, 1.0), (0.1, 3.0), (0.8, 0.6), (0.1, 6.0), (3.0, 1.0)]
    t, m = build(spec, hz)
    t0 = time.time()
    wins = C.windows_from_trace(t, m, hz)
    nR = len(C.generate_all(wins, C.PRESETS["vest-R"]))
    nL = len(C.generate_all(wins, C.PRESETS["vest-L"]))
    took = time.time() - t0
    assert nR == 360 and nL == 360
    assert took < 60.0, f"{took:.1f} s"
