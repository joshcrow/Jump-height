"""Tests for sim/score.py — the load-band candidate generator, the trace
timebase reader, the alignment math and the offset solver.

The traces here are SYNTHETIC and built from stated physics, so each test
knows the answer independently of the code under test. That matters more than
usual for this module: its whole job is to say what a real trace does and does
not contain, and a test that merely re-ran the implementation would agree with
any bug it had.

Three of these exist because the real data had the defect and the first
implementation got it wrong:
  * `test_window_walker_*` — a 3.0 s window landed on a 50 Hz sample boundary
    and a 3.8 s one did not, so the 3.8 s row reported "no contiguous window
    that long" inside a 103-minute unbroken stretch.
  * `test_boot_reset_*` — a reboot is a NEGATIVE time step, and every
    continuity test in the module was `dt <= max_gap_s`, which accepts
    -287,847 s happily.
  * `test_device_jumps_*` — the morning bundle's 20 stored jumps predate its
    own session (`session_jumps=0`).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))
sys.path.insert(0, str(REPO / "tools"))

import score  # noqa: E402

HZ = 50.0
DT = 1.0 / HZ
G = 9.80665


# --------------------------------------------------------------- synthesis


def _samples(spec, t0=100.0):
    """Build (times, mag) from [(duration_s, level_g), ...] at 50 Hz."""
    times, mag = [], []
    t = t0
    for dur, level in spec:
        n = int(round(dur / DT))
        for _ in range(n):
            times.append(round(t, 6))
            mag.append(level)
            t += DT
    return times, mag


def ballistic_hop(airtime_s, lead_s=3.0, trail_s=3.0, load_g=0.05,
                  pop_g=2.2, spike_g=3.4, t0=100.0):
    """A TRUE ballistic jump: near-zero load for the whole flight.

    load_g is 0.05 and not 0.0 because a real accelerometer in free fall reads
    its own noise floor, and a generator that only fires on an exact zero
    would be testing arithmetic rather than detection.
    """
    return _samples([
        (lead_s, 1.0),
        (2 * DT, pop_g),
        (airtime_s, load_g),
        (2 * DT, spike_g),
        (trail_s, 1.0),
    ], t0=t0)


def wing_flight(airtime_s=3.0, load_g=0.5, lead_s=3.0, trail_s=3.0,
                pop_g=2.2, spike_g=3.4, t0=100.0):
    """A wing-supported flight: the wing carries half the rider, so the
    accelerometer reads ~0.5 g for the whole flight rather than ~0."""
    return _samples([
        (lead_s, 1.0),
        (2 * DT, pop_g),
        (airtime_s, load_g),
        (2 * DT, spike_g),
        (trail_s, 1.0),
    ], t0=t0)


def riding_noise(dur_s=60.0, t0=100.0, seed=7):
    """Ordinary riding: 1 g with chop, never a sustained low-load band."""
    import random
    rng = random.Random(seed)
    n = int(dur_s / DT)
    times = [round(t0 + i * DT, 6) for i in range(n)]
    mag = [max(0.05, 1.0 + rng.gauss(0.0, 0.18)) for _ in range(n)]
    return times, mag


# ------------------------------------------------- candidate generator: hops


def test_ballistic_hop_is_found_with_its_airtime():
    times, mag = ballistic_hop(0.9)
    cp = score.CandidateParams(band_g=0.6, pop_g=1.5, spike_g=2.5, min_air_s=0.4)
    cands = score.find_candidates(times, mag, cp)
    assert len(cands) == 1
    c = cands[0]
    # Airtime runs takeoff -> landing spike, so it carries the two 0.02 s pop
    # samples' worth of edge; one sample of slack either way.
    assert c.airtime_s == pytest.approx(0.9, abs=3 * DT)
    assert c.band_s == pytest.approx(0.9, abs=3 * DT)
    assert c.min_load_g < 0.1
    assert c.pop_g == pytest.approx(2.2)
    assert c.spike_g == pytest.approx(3.4)


def test_ballistic_hop_height_matches_the_closed_form():
    times, mag = ballistic_hop(0.9)
    c = score.find_candidates(times, mag)[0]
    assert c.h_ballistic_m == pytest.approx(G * c.airtime_s ** 2 / 8.0, rel=1e-9)
    # A true ballistic hop has L ~ 0, so the lift correction must barely move it.
    assert c.lift_fraction < 0.15
    assert c.h_lift_m == pytest.approx(c.h_ballistic_m, rel=0.2)


def test_hop_below_min_airtime_is_rejected():
    times, mag = ballistic_hop(0.20)
    cp = score.CandidateParams(min_air_s=0.4)
    assert score.find_candidates(times, mag, cp) == []


def test_hop_without_a_landing_spike_is_rejected():
    """No spike => no candidate. A flight the trace never closes is not a jump;
    the stock detector has the same rule for the same reason (it once stored a
    '57 m' jump by closing a stale flight on a stray spike)."""
    times, mag = ballistic_hop(0.9, spike_g=1.1)
    assert score.find_candidates(times, mag) == []


def test_hop_without_a_takeoff_pop_is_rejected():
    times, mag = ballistic_hop(0.9, pop_g=1.0)
    cp = score.CandidateParams(pop_g=1.5)
    assert score.find_candidates(times, mag, cp) == []


# ------------------------------------------- candidate generator: wing flight


def test_wing_flight_needs_a_band_above_the_freefall_gate():
    """The plan's central claim, as a test: a 3 s flight held at 0.5 g is
    invisible to a 0.35 g free-fall gate and visible to a 0.6 g load band."""
    times, mag = wing_flight(airtime_s=3.0, load_g=0.5)

    tight = score.find_candidates(
        times, mag, score.CandidateParams(band_g=0.35, min_air_s=0.4))
    assert tight == [], "a 0.35 g gate must not see a 0.5 g flight"

    loose = score.find_candidates(
        times, mag, score.CandidateParams(band_g=0.6, min_air_s=0.4))
    assert len(loose) == 1
    assert loose[0].airtime_s == pytest.approx(3.0, abs=3 * DT)
    assert loose[0].mean_load_g == pytest.approx(0.5, abs=0.06)


def test_lift_correction_halves_a_half_supported_flight():
    """h = (1 - L) g T^2 / 8 with L = mean load in g. At L = 0.5 the corrected
    height is half the ballistic one — computed here from the definition, not
    from the module."""
    times, mag = wing_flight(airtime_s=3.0, load_g=0.5)
    c = score.find_candidates(times, mag, score.CandidateParams(band_g=0.6))[0]
    assert c.lift_fraction == pytest.approx(0.5, abs=0.06)
    expected = (1.0 - c.mean_load_g) * G * c.airtime_s ** 2 / 8.0
    assert c.h_lift_m == pytest.approx(expected, rel=1e-9)
    assert c.h_lift_m < c.h_ballistic_m
    assert c.h_lift_m == pytest.approx(c.h_ballistic_m * 0.5, rel=0.15)


def test_lift_correction_clamps_at_zero_never_negative():
    """L > 1 means the 'flight' was never unloaded. A negative height would
    look like a measurement; zero says the model does not apply."""
    c = score.Candidate(takeoff_s=0.0, land_s=1.0, airtime_s=1.0, band_s=0.8,
                        mean_load_g=1.4, min_load_g=1.1, pop_g=2.0, spike_g=3.0)
    assert c.h_lift_m == 0.0
    assert c.h_ballistic_m > 0.0


# ------------------------------------------- candidate generator: false alarms


def test_riding_noise_alone_yields_no_candidates():
    times, mag = riding_noise(120.0)
    cp = score.CandidateParams(band_g=0.6, pop_g=1.5, spike_g=2.5, min_air_s=0.4)
    assert score.find_candidates(times, mag, cp) == []


def test_noise_around_a_real_hop_still_finds_exactly_one():
    nt, nm = riding_noise(30.0, t0=100.0)
    ht, hm = ballistic_hop(0.8, lead_s=1.0, trail_s=1.0, t0=nt[-1] + DT)
    t2, m2 = riding_noise(30.0, t0=ht[-1] + DT, seed=11)
    times = nt + ht + t2
    mag = nm + hm + m2
    cands = score.find_candidates(
        times, mag, score.CandidateParams(band_g=0.6, min_air_s=0.4))
    assert len(cands) == 1
    assert cands[0].airtime_s == pytest.approx(0.8, abs=3 * DT)


def test_band_tolerance_survives_a_single_stray_sample():
    """One noisy sample poking above the band must not truncate the measured
    flight — the reason CandidateParams carries band_tol_s at all.

    REVISED 2026-09-15. This used to assert `len(split) == 2`: with zero
    tolerance both halves reached the same landing spike and one jump was
    reported as TWO candidates. That double-count was a defect of
    find_candidates, not a property of band_tol_s — the scan resumed inside
    the flight after emitting — and it reached the real data (two candidates
    sharing the spike at t=10745.367 in the evening trace, inside the count
    that chose the scorecard's operating point). The generator now resumes
    past the landing sample, so ONE landing spike yields at most ONE
    candidate whatever the tolerance.

    What band_tol_s still buys is the measurement: without it, `band_s` — how
    much of the flight was genuinely unloaded, the number the whole scorecard
    turns on — is truncated at the stray sample.
    """
    times, mag = ballistic_hop(1.0)
    mag[len(mag) // 2] = 0.9  # above a 0.6 g band, for exactly one sample

    tol = score.find_candidates(
        times, mag, score.CandidateParams(band_g=0.6, band_tol_s=0.10,
                                          min_air_s=0.4))
    assert len(tol) == 1
    assert tol[0].airtime_s == pytest.approx(1.0, abs=3 * DT)
    assert tol[0].band_s == pytest.approx(1.0, abs=3 * DT)

    split = score.find_candidates(
        times, mag, score.CandidateParams(band_g=0.6, band_tol_s=0.0,
                                          min_air_s=0.4))
    assert len(split) == 1, "one landing spike is at most one candidate"
    assert split[0].airtime_s == pytest.approx(1.0, abs=3 * DT)
    assert split[0].band_s == pytest.approx(0.5, abs=0.05), \
        "zero tolerance truncates the measured band at the stray sample"


# ------------------------------------------------------ the low-load reachability probe


def test_window_walker_finds_a_band_at_a_non_sample_aligned_length():
    """A 3.8 s window is 190 samples at 50 Hz but must not depend on landing
    exactly on a boundary — the bug this test was written for reported 'no
    contiguous window that long' inside a 103-minute continuous trace."""
    times, mag = wing_flight(airtime_s=5.0, load_g=0.5, lead_s=2.0, trail_s=2.0)
    for w in (1.0, 2.0, 3.0, 3.8, 4.5):
        got = score.lowest_mean_load(times, mag, w)
        assert got is not None, f"no window found at {w} s"
        assert got[0] == pytest.approx(0.5, abs=0.05), w


def test_window_walker_reports_none_when_no_window_is_long_enough():
    """Absence must be reported as absence, not as a low number."""
    times, mag = _samples([(1.0, 1.0)])
    assert score.lowest_mean_load(times, mag, 3.0) is None


def test_window_walker_will_not_bridge_a_gap():
    """Two 3 s stretches of 0.3 g either side of a 60 s sleep are not a 6 s
    low-load window.

    (The stretches are 3 s of SAMPLES, which span 3 s - one sample interval;
    hence the 2.9 s query rather than 3.0.)"""
    a_t, a_m = _samples([(3.0, 0.3)], t0=0.0)
    b_t, b_m = _samples([(3.0, 0.3)], t0=a_t[-1] + 60.0)
    times, mag = a_t + b_t, a_m + b_m
    assert score.lowest_mean_load(times, mag, 2.9)[0] == pytest.approx(0.3, abs=0.01)
    assert score.lowest_mean_load(times, mag, 4.0) is None


# --------------------------------------------------------------- the timebase


def test_boot_reset_is_detected_and_splits_the_trace():
    a_t, a_m = _samples([(2.0, 1.0)], t0=287_000.0)
    b_t, b_m = _samples([(2.0, 1.0)], t0=0.031)
    times, mag = a_t + b_t, a_m + b_m
    tb = score.analyse_timebase(times)
    assert tb.multi_boot
    assert len(tb.boot_resets) == 1
    assert tb.last_boot_start_idx == len(a_t)
    assert tb.last_boot_t0 == pytest.approx(0.031)
    assert tb.t_max == pytest.approx(a_t[-1])


def test_boot_reset_is_not_continuous():
    """The bug: -287,847 s passes `dt <= 0.5`. Continuity must test both ends."""
    assert not score.is_continuous(-287_847.9)
    assert not score.is_continuous(60.0)
    assert score.is_continuous(0.02)
    assert score.is_continuous(-0.078), "sub-second jitter is tolerated"


def test_boot_reset_breaks_a_contiguous_segment():
    a_t, _ = _samples([(2.0, 1.0)], t0=287_000.0)
    b_t, _ = _samples([(2.0, 1.0)], t0=0.031)
    segs = score.contiguous_segments(a_t + b_t)
    assert len(segs) == 2
    assert segs[0] == (0, len(a_t) - 1)


def test_boot_reset_cannot_manufacture_a_flight():
    """A reboot lands mid-band: without the continuity guard the generator
    would stitch the two boots into one impossibly long low-load flight."""
    a_t, a_m = _samples([(1.0, 1.0), (2 * DT, 2.2), (0.5, 0.05)], t0=287_000.0)
    b_t, b_m = _samples([(0.5, 0.05), (2 * DT, 3.4), (1.0, 1.0)], t0=0.031)
    cands = score.find_candidates(a_t + b_t, a_m + b_m,
                                  score.CandidateParams(min_air_s=0.4))
    assert cands == [], "a flight must not span a power cycle"


def test_sub_second_jitter_is_recorded_but_not_a_reboot():
    times, mag = _samples([(2.0, 1.0)])
    times[50] = times[50] + 0.08  # makes step 51 go backwards by ~0.06 s
    tb = score.analyse_timebase(times)
    assert not tb.multi_boot
    assert len(tb.jitter_steps) == 1
    assert len(score.contiguous_segments(times)) == 1


# ------------------------------------------------------- alignment, FIT-free


def _write_session(tmp_path, session=None, surfr=None, trace=True):
    d = tmp_path / "sess"
    d.mkdir(exist_ok=True)
    if trace:
        times, mag = wing_flight(airtime_s=1.0, load_g=0.5, t0=100.0)
        lines = ["t,mag"] + [f"{t},{m}" for t, m in zip(times, mag)]
        (d / "trace.csv").write_text("\n".join(lines) + "\n")
    if session is not None:
        (d / "session.json").write_text(json.dumps(session))
    if surfr is not None:
        (d / "surfr.json").write_text(json.dumps(surfr))
    return d


def test_alignment_runs_with_no_garmin_file(tmp_path):
    """garmin.fit absent must be a FINDING and must not stop the command."""
    d = _write_session(tmp_path, session={
        "trace_epoch_utc": "2026-09-14T18:07:29.427Z",
        "synced_at_utc": "2026-09-14T18:09:29.427Z",
        "device_uptime_s_at_sync": 120.0,
    })
    s = score.score_session(d)
    assert s is not None
    assert not s.alignment.garmin.present
    joined = "\n".join(s.alignment.findings)
    assert "garmin.fit absent" in joined
    assert "DID NOT RUN" in joined
    card = score.render_scorecard(s)
    assert "score.md" in card


def test_epoch_self_check_flags_an_inconsistent_epoch(tmp_path):
    """epoch + uptime must land on the sync instant; 600 s out must say so."""
    d = _write_session(tmp_path, session={
        "trace_epoch_utc": "2026-09-14T18:07:29.427Z",
        "synced_at_utc": "2026-09-14T18:19:29.427Z",   # 720 s later
        "device_uptime_s_at_sync": 120.0,              # but only 120 s of uptime
    })
    s = score.score_session(d)
    joined = "\n".join(s.alignment.findings)
    assert "INCONSISTENT" in joined
    assert "+600.000 s" in joined or "-600.000 s" in joined


def test_missing_epoch_is_a_finding_not_a_default(tmp_path):
    d = _write_session(tmp_path, session={"unit": "JumpHeight-E2C4"})
    s = score.score_session(d)
    assert s.alignment.epoch_utc is None
    assert any("no `trace_epoch_utc`" in f for f in s.alignment.findings)
    assert s.alignment.t_to_utc(10.0) is None


def test_missing_synced_at_is_reported_as_an_unrun_check(tmp_path):
    d = _write_session(tmp_path, session={
        "trace_epoch_utc": "2026-09-14T18:07:29.427Z"})
    s = score.score_session(d)
    assert any("could not be self-checked" in f for f in s.alignment.findings)


def test_surfr_window_uses_the_manifest_timezone_not_a_guess(tmp_path):
    d = _write_session(
        tmp_path,
        session={"trace_epoch_utc": "2026-09-14T18:07:29.427Z",
                 "manifest": {"tz_offset_min": -240}},
        surfr={"session_start_local": "2026-09-14T16:15", "duration_s": 6180,
               "jumps_total": 32, "rows": []})
    s = score.score_session(d)
    a = s.alignment
    assert a.surfr_tz_offset_min == -240
    assert "manifest" in a.surfr_tz_source
    assert a.surfr_start.isoformat() == "2026-09-14T20:15:00+00:00"
    assert a.surfr_end.isoformat() == "2026-09-14T21:58:00+00:00"


def test_surfr_timezone_fallback_is_labelled_assumed(tmp_path):
    d = _write_session(
        tmp_path,
        session={"trace_epoch_utc": "2026-09-14T18:07:29.427Z"},
        surfr={"session_start_local": "2026-09-14T16:15", "duration_s": 600,
               "jumps_total": 3, "rows": []})
    s = score.score_session(d)
    assert "ASSUMED" in s.alignment.surfr_tz_source


def test_no_trace_csv_returns_none(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert score.score_session(d) is None


@pytest.mark.parametrize("raw,expect", [
    ("15:21", 921.0),
    ("15:50", 950.0),
    ("0:07", 7.0),
    ("1:02:03", 3723.0),
    (930, 930.0),
    ("", None),
    (None, None),
    ("nonsense", None),
])
def test_parse_t_into_session(raw, expect):
    assert score.parse_t_into_session(raw) == expect


# ------------------------------------------------------------ offset solver


def test_offset_solver_recovers_a_known_shift():
    """Candidates are the truth; Surfr's rows are those truths minus a
    constant. The solver must return that constant."""
    truth = [100.0, 250.0, 430.0, 610.0, 905.0]
    shift = -37.5
    surfr = [t + shift for t in truth]
    fit = score.solve_offset(surfr, truth, search_s=120.0, step_s=0.25)
    assert fit.offset_s == pytest.approx(-shift, abs=0.25)
    assert fit.mean_abs_residual_s == pytest.approx(0.0, abs=0.25)
    assert fit.matched_idx == [0, 1, 2, 3, 4]


def test_offset_solver_recovers_a_positive_shift_with_distractors():
    truth = [100.0, 250.0, 430.0, 610.0, 905.0]
    distractors = [140.0, 300.0, 500.0, 700.0, 800.0, 1000.0]
    surfr = [t - 61.0 for t in truth]
    fit = score.solve_offset(surfr, sorted(truth + distractors), search_s=120.0)
    assert fit.offset_s == pytest.approx(61.0, abs=0.5)


def test_offset_solver_stays_inside_the_search_range():
    truth = [100.0, 250.0, 430.0]
    surfr = [t - 400.0 for t in truth]      # far outside +-120 s
    fit = score.solve_offset(surfr, truth, search_s=120.0)
    assert -120.0 <= fit.offset_s <= 120.0


def test_offset_solver_does_not_improve_by_matching_nothing():
    """The cost charges max_pair_s for an unmatched row on purpose: without
    it, sliding every row away from every candidate would score zero."""
    truth = [100.0, 250.0, 430.0]
    surfr = [t - 10.0 for t in truth]
    fit = score.solve_offset(surfr, truth, search_s=120.0, max_pair_s=20.0)
    assert fit.offset_s == pytest.approx(10.0, abs=0.5)
    assert all(r is not None for r in fit.residuals_s)


def test_offset_solver_returns_none_without_inputs():
    assert score.solve_offset([], [1.0, 2.0]) is None
    assert score.solve_offset([1.0], []) is None


# ------------------------------------------------------- device jump coverage


def test_device_jumps_from_another_boot_are_called_out():
    times, _ = _samples([(2.0, 1.0)], t0=0.0)
    jumps = [{"takeoff": 170011.6, "airtime": 0.365, "height": 0.163}]
    note = score.device_jump_coverage(
        jumps, times,
        {"manifest": {"stats_before": "STATS session_jumps=0 stored_jumps=20"}})
    assert "session_jumps=0" in note
    assert "NONE of these rows were recorded in this session" in note
    assert "0 have a takeoff_s inside" in note


def test_device_jumps_unknown_provenance_is_not_a_pass():
    times, _ = _samples([(2.0, 1.0)], t0=0.0)
    note = score.device_jump_coverage([{"takeoff": 1.0, "airtime": 0.4,
                                        "height": 0.2}], times, {})
    assert "WAS NOT DETERMINED" in note


# ---------------------------------------------------------- stock detector


def test_stock_detector_is_the_control_and_is_untouched():
    """score.py must not mutate the firmware mirror's defaults."""
    from detector import Params
    p = Params()
    assert p.freefall_enter_g == 0.35
    assert p.max_airtime_s == 3.00
    times, mag = ballistic_hop(0.9)
    events = score.run_stock_detector(times, mag)
    assert len(events) == 1
    assert Params().freefall_enter_g == 0.35


def test_stock_detector_misses_the_wing_flight_the_band_finds():
    """The two generators, side by side, on the same synthetic 3 s wing
    flight — the comparison the scorecard prints."""
    times, mag = wing_flight(airtime_s=3.0, load_g=0.5)
    assert score.run_stock_detector(times, mag) == []
    band = score.find_candidates(
        times, mag, score.CandidateParams(band_g=0.6, min_air_s=0.4))
    assert len(band) == 1


# ------------------------------------------------------------------ sweep


def test_sweep_covers_the_grid_and_is_monotone_in_band_g():
    """A looser band can never find fewer candidates, all else equal."""
    nt, nm = riding_noise(20.0, t0=100.0)
    ht, hm = wing_flight(3.0, load_g=0.55, lead_s=1.0, trail_s=1.0, t0=nt[-1] + DT)
    times, mag = nt + ht, nm + hm
    rows = score.sweep_table(times, mag)
    assert len(rows) == (len(score.SWEEP_BAND_G) * len(score.SWEEP_POP_G)
                         * len(score.SWEEP_SPIKE_G) * len(score.SWEEP_MIN_AIR_S))
    for pop in score.SWEEP_POP_G:
        for spike in score.SWEEP_SPIKE_G:
            for mn in score.SWEEP_MIN_AIR_S:
                counts = [r.count for r in rows
                          if r.pop_g == pop and r.spike_g == spike
                          and r.min_air_s == mn]
                assert counts == sorted(counts), (pop, spike, mn, counts)


def test_best_row_for_count_breaks_ties_toward_the_tighter_setting():
    rows = [
        score.SweepRow(0.8, 1.2, 2.0, 0.3, 30, [], [], 0.0),
        score.SweepRow(0.6, 1.5, 2.5, 0.4, 30, [], [], 0.0),
        score.SweepRow(0.7, 1.2, 2.0, 0.3, 45, [], [], 0.0),
    ]
    best = score.best_row_for_count(rows, 32)
    assert best.band_g == 0.6


def test_best_row_for_count_handles_an_empty_sweep():
    assert score.best_row_for_count([], 32) is None


# ------------------------------------------- adversarial review 2026-09-15


def test_two_bands_sharing_one_landing_spike_are_one_candidate():
    """One jump must not be counted twice.

    A band cut short by an above-band excursion longer than band_tol_s used
    to leave the scan resuming at last_low + 1, i.e. INSIDE the flight, so
    the remainder re-opened a second band that reached the SAME landing
    sample. MEASURED on data/sessions/20260914-210637-E2C4 at the scorecard's
    own chosen operating point (band 0.7 g, pop 1.2 g, spike 2.5 g, min air
    0.4 s): candidates at t=10743.627 and t=10744.347 both landed on the
    spike at t=10745.367, so the "32 candidates, exactly Surfr's 32" that
    chose the operating point was 31 events plus a duplicate. 20 of the 90
    sweep grid points carried at least one such pair.
    """
    times, mag = _samples([
        (2.0, 1.0),          # riding
        (2 * DT, 2.2),       # pop
        (0.6, 0.30),         # band, part one
        (0.20, 0.95),        # excursion well past band_tol_s (0.10 s)
        (0.6, 0.30),         # band, part two — same flight, same landing
        (2 * DT, 3.4),       # ONE landing spike
        (2.0, 1.0),
    ])
    cp = score.CandidateParams(band_g=0.6, pop_g=1.5, spike_g=2.5,
                               min_air_s=0.4, band_tol_s=0.10)
    cands = score.find_candidates(times, mag, cp)
    assert len(cands) == 1, [(c.takeoff_s, c.land_s) for c in cands]
    assert len({c.land_s for c in cands}) == len(cands)


def test_candidates_never_overlap_in_time():
    """No candidate may start before the previous one has landed."""
    nt, nm = riding_noise(10.0, t0=100.0)
    a_t, a_m = _samples([(2 * DT, 2.2), (0.5, 0.3), (0.15, 0.95), (0.5, 0.3),
                         (2 * DT, 3.4)], t0=nt[-1] + DT)
    b_t, b_m = riding_noise(10.0, t0=a_t[-1] + DT, seed=3)
    times, mag = nt + a_t + b_t, nm + a_m + b_m
    cands = score.find_candidates(
        times, mag, score.CandidateParams(band_g=0.6, min_air_s=0.4))
    for prev, nxt in zip(cands, cands[1:]):
        assert nxt.takeoff_s >= prev.land_s, (prev.takeoff_s, prev.land_s,
                                              nxt.takeoff_s)


def test_offset_solver_reports_the_right_index_for_equal_takeoffs():
    """Two candidates at the SAME takeoff time must not collapse.

    matched_idx used to be recovered with `{takeoff: i}`, a dict keyed on a
    float, so a repeated takeoff kept only the last index and the row that
    matched the other one rendered as "NO CANDIDATE within the pairing
    window". The real evening trace repeats a timestamp (t=9902.002), so
    equal candidate takeoffs are reachable, not hypothetical.
    """
    cand = [100.0, 250.0, 250.0, 430.0]
    surfr = [t - 5.0 for t in (100.0, 430.0)]
    fit = score.solve_offset(surfr, cand, search_s=60.0)
    assert fit.offset_s == pytest.approx(5.0, abs=0.25)
    assert all(i is not None for i in fit.matched_idx), fit.matched_idx
    for i, s in zip(fit.matched_idx, surfr):
        assert cand[i] == pytest.approx(s + fit.offset_s, abs=0.5)


def _scored(tmp_path, surfr):
    return score.score_session(_write_session(
        tmp_path,
        session={"trace_epoch_utc": "2026-09-14T18:07:29.427Z",
                 "manifest": {"tz_offset_min": -240}},
        surfr=surfr))


def test_scorecard_never_prints_another_sessions_surfr_airtimes(tmp_path):
    """The card's header promises every number is measured from THIS
    directory. "3.8 / 3.4 / 3.1 s for the 2026-09-14 evening" was a literal,
    so the 2026-09-14 MORNING card — 0 transcribed rows, no Surfr airtime —
    printed the evening's three numbers twice as if they were its own."""
    card = score.render_scorecard(_scored(
        tmp_path, {"jumps_total": 12, "rows": []}))
    assert "3.8 / 3.4 / 3.1" not in card
    assert "2026-09-14 evening" not in card

    own = score.render_scorecard(_scored(tmp_path, {
        "jumps_total": 3, "max_airtime_s": 2.2,
        "session_start_local": "2026-09-14T14:07", "duration_s": 600,
        "rows": [{"n": 1, "airtime_s": 2.20, "t_into_session": "0:20"},
                 {"n": 2, "airtime_s": 1.10, "t_into_session": "0:40"}]}))
    assert "2.20, 1.10" in own


def test_scorecard_does_not_claim_a_fit_that_never_ran(tmp_path):
    """"A fitted offset over 2 rows is not a fitted offset" was printed with
    a hard-coded 2 even when 0 rows were transcribed and the solver never
    ran — an assertion about a measurement that did not happen."""
    s = _scored(tmp_path, {"jumps_total": 12, "rows": []})
    assert s.fit is None
    card = score.render_scorecard(s)
    assert "over 2 rows" not in card
    assert "No offset was fitted" in card
    assert "0 transcribed Surfr row(s)" in card


def test_scorecard_names_the_scope_of_the_three_second_claim(tmp_path):
    """The 3 s minimum-mean-load figure is computed over the SCORED window,
    not the whole trace, and the two differ: measured on
    data/sessions/20260914-210637-E2C4, 0.947 g over the 103-minute
    Surfr/Garmin union and 0.944 g over all 417 minutes. The card must say
    which one it means."""
    s = _scored(tmp_path, {"jumps_total": 3, "max_airtime_s": 2.2,
                           "session_start_local": "2026-09-14T14:07",
                           "duration_s": 600, "rows": []})
    card = score.render_scorecard(s)
    assert "lowest mean load over ANY contiguous 3.0 s window" in card
    scope = s.window_note.split(" — ")[0].split(";")[0].strip()
    assert f"({scope})" in card
    assert "session window" in scope or "whole trace" in scope
