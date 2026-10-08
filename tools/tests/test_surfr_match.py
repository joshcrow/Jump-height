"""Tests for sim/surfr_match.py — Surfr per-jump rows matched to CG-1
candidates by TIME (spec section 4, tests T-C1..T-C8), plus the context
helpers in sim/candctx.py that the report leans on.

Synthetic rows and candidates only; no private data.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

import candctx  # noqa: E402
import surfr_match as M  # noqa: E402

START = 1000.0             # trace t of the displayed Surfr start
WINDOW = (1000.0, 7000.0)  # session window for the slide null


def mmss(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def distractors(n=40, seed=3, lo=1100.0, hi=6900.0):
    rnd = random.Random(seed)
    out = []
    for k in range(n):
        p = rnd.uniform(lo, hi)
        out.append(M.Cand(cid=1000 + k, pop=p, land=p + 1.7))
    return out


def surfr_for(pops, delta, extra=None, at=0.0):
    """Rows placed so that start + t_into + delta = pop + at exactly."""
    rows = [{"n": k + 1, "t_into_session": mmss(p + at - START - delta)}
            for k, p in enumerate(pops)]
    d = {"session_start_local": "2026-09-14T16:15", "duration_s": 6000,
         "jumps_total": 32, "rows": rows}
    d.update(extra or {})
    return d


def renumber(cands):
    cands = sorted(cands, key=lambda c: c.pop)
    for k, c in enumerate(cands, 1):
        c.cid = k
    return cands


# ------------------------------------------------------------------ T-C1

def test_c1_the_two_on_disk_shapes_parse_without_findings():
    sep14 = {"source": "x", "session_start_local": "2026-09-14T16:15",
             "duration_s": 6180, "jumps_total": 32,
             "rows": [{"n": 1, "height_ft": 6.32, "airtime_s": 3.07,
                       "distance_ft": 66, "t_into_session": "15:21"},
                      {"n": 2, "height_ft": 5.74, "airtime_s": 3.39,
                       "distance_ft": 72, "t_into_session": "15:50"}]}
    rows, f = M.validate(sep14)
    assert f == [] and [r.t_into_s for r in rows] == [921.0, 950.0]
    rows, f = M.validate({"session_start_local": "2026-09-23T15:51", "rows": []})
    assert f == [] and rows == []


def test_c1_a_v1_document_round_trips():
    v1 = {"schema": "surfr-v1", "source": "s", "session_start_local": "2026-09-14T16:15",
          "tz_offset_min": -240, "start_resolution": "minute", "start_rounding": "unknown",
          "duration_s": 6180, "jumps_total": 32, "rows_complete": False,
          "rows": [{"n": 1, "height_ft": 6.32, "airtime_s": 3.07, "distance_ft": 66,
                    "t_into_session": "15:21"}], "note": ""}
    back = json.loads(json.dumps(v1))
    rows, f = M.validate(back)
    assert back == v1 and f == [] and rows[0].height_ft == 6.32


# ------------------------------------------------------------------ T-C2

def test_c2_an_unparseable_time_is_a_named_finding():
    rows, f = M.validate({"rows": [{"n": 1, "t_into_session": "15:21"},
                                   {"n": 2, "t_into_session": "quarter past"}]})
    assert len(rows) == 1
    assert any("n=2" in x and "FINDING" in x for x in f)


def test_c2_duplicates_order_and_range_are_findings():
    _, f = M.validate({"duration_s": 600, "rows": [
        {"n": 1, "t_into_session": "5:00"}, {"n": 1, "t_into_session": "6:00"},
        {"n": 3, "t_into_session": "2:00"}, {"n": 4, "t_into_session": "20:00"}]})
    text = "\n".join(f)
    assert "more than once" in text
    assert "not chronological" in text
    assert "outside [0, duration_s=600]" in text


# ------------------------------------------------------------------ T-C3

def test_c3_a_known_offset_is_recovered_with_every_row_matched():
    planted = [1500.0 + 610.0 * k for k in range(8)]
    # point-like intervals so the residual has a unique minimum
    cands = renumber([M.Cand(0, p, p + 0.02) for p in planted] + distractors())
    res = M.match(surfr_for(planted, 41.0), START, cands, WINDOW)
    assert res.mode == "fit"
    assert res.delta_s == pytest.approx(41.0, abs=0.1)
    assert res.matched == 8
    assert res.p_slide is not None and res.p_slide < 0.01
    assert res.verdict == M.VERDICT_ESTABLISHED
    assert not any("boundary" in f for f in res.findings)


# ------------------------------------------------------------------ T-C4

def test_c4_an_offset_on_the_prior_boundary_is_a_finding():
    planted = [1500.0 + 610.0 * k for k in range(6)]
    cands = renumber([M.Cand(0, p, p + 0.02) for p in planted])
    # true offset +66 s lies just outside the unknown prior's +65 s edge;
    # at +65 every row is still within the 1.5 s tolerance
    res = M.match(surfr_for(planted, 66.0), START, cands, WINDOW)
    assert res.delta_s == pytest.approx(65.0)
    assert any("prior boundary" in f and "FINDING" in f for f in res.findings)


# ------------------------------------------------------------------ T-C5

def test_c5_two_rows_fit_nothing_but_the_gap_test_finds_the_planted_pair():
    a, b = 3000.0, 3029.0
    cands = renumber([M.Cand(0, a, a + 1.76), M.Cand(0, b, b + 1.76)] + distractors())
    res = M.match(surfr_for([a, b], 41.0), START, cands, WINDOW)
    assert res.mode == "gap" and res.delta_s is None
    pair = [(g.cid_a, g.cid_b) for g in res.gap_pairs]
    ids = {c.pop: c.cid for c in cands}
    assert (ids[a], ids[b]) in pair
    g = next(g for g in res.gap_pairs if (g.cid_a, g.cid_b) == (ids[a], ids[b]))
    assert g.implied_delta_s == pytest.approx(41.0, abs=0.6)
    assert res.p_slide is not None and 0.0 <= res.p_slide <= 1.0
    assert res.n_slides > 1000
    assert res.verdict != M.VERDICT_ESTABLISHED      # < 5 rows never establishes
    text = "\n".join(M.render(res))
    assert "No offset fitted" in text


# ------------------------------------------------------------------ T-C6

def test_c6_monotone_assignment_refuses_a_crossing():
    # A long interval A (pop 5) and a point B (pop 9.9); rows at 10 and 11.
    # The crossing row1->B, row2->A has the smaller residual sum (0.1) but is
    # not allowed; the answer is row1->A, row2->B.
    idx = M._Index([M.Cand(1, 5.0, 12.0), M.Cand(2, 9.9, 9.9)])
    m, sres, pairs = M._assign([10.0, 11.0], idx, 1.5)
    assert m == 2
    assert [(i, idx.c[j].cid) for i, j, _ in pairs] == [(0, 1), (1, 2)]
    assert sres == pytest.approx(1.1)


# ------------------------------------------------------------------ T-C7

@pytest.mark.parametrize("planted_n", [2, 8])
def test_c7_jumps_total_cannot_influence_any_output(planted_n):
    planted = [1500.0 + 610.0 * k for k in range(planted_n)]
    cands = renumber([M.Cand(0, p, p + 1.5) for p in planted] + distractors())
    outs = []
    for jt in (32, 0, 7777, None):
        s = surfr_for(planted, 41.0)
        if jt is None:
            del s["jumps_total"]
        else:
            s["jumps_total"] = jt
        res = M.match(s, START, cands, WINDOW)
        outs.append(("\n".join(M.render(res)), [(r.n, c.cid, x) for r, c, x in res.pairs],
                     res.p_slide, res.delta_s))
    assert all(o == outs[0] for o in outs)
    assert "7777" not in outs[2][0]                      # the count is never printed


# ------------------------------------------------------------------ T-C8

def test_c8_incomplete_rows_never_print_false_positive():
    planted = [1500.0 + 610.0 * k for k in range(6)]
    cands = renumber([M.Cand(0, p, p + 1.5) for p in planted] + distractors())
    for extra in ({}, {"rows_complete": False}):
        text = "\n".join(M.render(M.match(surfr_for(planted, 41.0, extra), START,
                                          cands, WINDOW)))
        assert "false positive" not in text.lower()
        assert "no transcribed row" in text.lower()
    text = "\n".join(M.render(M.match(surfr_for(planted, 41.0, {"rows_complete": True}),
                                      START, cands, WINDOW)))
    assert "false positive" in text.lower()


def test_did_not_run_says_why():
    res = M.match(surfr_for([2000.0], 41.0), None, [], WINDOW)
    assert not res.ran and "cannot be placed" in res.reason
    assert "Did not run" in "\n".join(M.render(res))


# ------------------------------------------------------------- candctx bits

def test_wrap_and_bearing_conventions():
    assert candctx.wrap180(190.0) == pytest.approx(-170.0)
    assert candctx.bearing(0.0, 0.0, 0.001, 0.0) == pytest.approx(0.0, abs=1e-6)
    assert candctx.bearing(0.0, 0.0, 0.0, 0.001) == pytest.approx(90.0, abs=1e-6)


def _ctx(**kw):
    c = candctx.Context(epoch_utc=None, tz_offset_min=-240, tz_src="t", garmin_reason="")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_course_uses_the_last_qualifying_record_pair():
    # heading north, then a sharp turn east in the last two fixes
    lat0 = 35.9
    pos_t = [0.0, 1.0, 2.0, 3.0]
    pos_ll = [(lat0, -75.6), (lat0 + 0.0001, -75.6), (lat0 + 0.0002, -75.6),
              (lat0 + 0.0002, -75.5999)]
    crs, age = candctx.course_at(_ctx(pos_t=pos_t, pos_ll=pos_ll), 3.5)
    assert crs == pytest.approx(90.0, abs=1.0) and age == pytest.approx(0.5)
    # a pair closer than 3 m is skipped for the previous one
    pos_ll[3] = (lat0 + 0.0002, -75.60001)
    crs, _ = candctx.course_at(_ctx(pos_t=pos_t, pos_ll=pos_ll), 3.5)
    assert crs == pytest.approx(0.0, abs=1.0)


def test_alignment_lag_is_measured_from_the_preceding_landing():
    c = _ctx(jh_t=[103.0, 106.0, 120.0], jh_v=[1.0, 2.0, 2.0],
             device=[("1", 100.0, 0.5), ("2", 104.0, 0.5)])
    lags = candctx.alignment_lags(c)
    assert [round(x[2], 2) for x in lags] == [2.5, 1.5]
    assert [x[3] for x in lags] == ["1", "2"]
    # a change before every landing is a NEGATIVE lag, not clipped
    c = _ctx(jh_t=[90.0], jh_v=[1.0], device=[("1", 100.0, 0.5)])
    assert candctx.alignment_lags(c)[0][2] < 0
    assert candctx.alignment_lags(_ctx()) is None
