"""Adversarial sea-noise generators for the detector's false-positive
property tests (tools/tests/test_seastate.py).

*** ROBUSTNESS PROPERTY TESTS, NOT TUNING. ***
Nothing here, and nothing in the test file, ever adjusts config/params.json
or sim/detector.py's thresholds to make a noise stream stop triggering. If
a generator below ever produces a false-positive jump, that is a genuine
finding about the detector and gets reported as one. And per
docs/roadmap.md's Tier-A backlog framing ("desk guesses about what foiling
'feels like' to the sensor would be fiction"), every parameter in this
module is a bench stand-in grounded in docs/research.md's cited numbers —
not a validated model of real sea state. That only exists once a real
on-water session trace does.

Three regimes, matching the false-positive risk profile research.md §6
identifies. Buoy-style narrowband filtering doesn't transfer to a jump's
broadband transient (that's the argument FOR the airtime method) — but the
flip side is the reassurance this module exists to check empirically:
broadband ocean chop can't fake a jump either. The one regime oceanography
doesn't model, and the one research.md flags as the *real* risk, is
structure-borne board slap — modeled here as spikes with no preceding dip.

  * orbital_chop(H, T)     — smooth wave-orbital acceleration, a few % of
    g: two orders of magnitude below the 0.35 g free-fall gate for typical
    wind chop (research.md §6: ~0.07 g for H=0.3 m / T=3 s).
  * board_slap(rate, peak) — sharp, short (10-40 ms) transients ABOVE 1 g,
    Poisson-arrival, with NO preceding low reading — the actual
    false-positive risk research.md names ("Real false positives are
    structure-borne board slap, a different regime oceanography doesn't
    model").
  * handling_chatter(amp)  — broadband jitter for the "carried around, not
    riding" regime (walking to the car, stashed in a bag, clipped to a
    harness paddling out).

All three return (times_s, accel_mag_g) at 200 Hz — the same |a| stream
sim/generate.py produces and sim/detector.py consumes. Pure standard
library (random + math), deterministic given a seed.
"""

from __future__ import annotations

import math
import random
from typing import List, Optional, Tuple

Stream = Tuple[List[float], List[float]]  # (times_s, accel_mag_g)

G = 9.80665  # gravity, m/s^2 — matches sim/detector.py's Params.g default


def orbital_chop(
    H: float,
    T: float,
    duration_s: float,
    fs_hz: float = 200.0,
    seed: int = 0,
    dither_g: float = 0.01,
) -> Stream:
    """Airy (linear wave theory) orbital acceleration, superposed on 1 g.

    Peak orbital acceleration a = (2*pi/T)**2 * H/2 (m/s^2) — the standard
    small-amplitude/deep-water result for the acceleration amplitude of
    fluid particle motion under a surface wave of height H and period T.
    research.md §6 cites ~0.07 g for H=0.3 m, T=3 s (typical wind chop);
    that is exactly this formula: (2*pi/3)**2 * 0.15 / 9.80665 = 0.0671 g.

    Modeled, like sim/generate.py's own "slow swell" term, as a single
    additive term on the 1 g baseline rather than a full 3-axis vector
    composition — consistent with the rest of this codebase's scalar |a|
    treatment (sim/generate.py's synth_session() does the same for its
    swell term). A random per-seed phase (not amplitude — the formula's
    amplitude is exactly what's under test) gives seed variety; a small
    fixed-amplitude Gaussian dither approximates ordinary accelerometer
    self-noise riding on top of the smooth swell (real sensors are never
    perfectly noiseless even in calm water).
    """
    if T <= 0.0:
        raise ValueError("T must be positive")
    rng = random.Random(seed)
    n = int(duration_s * fs_hz)
    times = [i / fs_hz for i in range(n)]

    a_orb_ms2 = (2.0 * math.pi / T) ** 2 * (H / 2.0)
    a_orb_g = a_orb_ms2 / G
    freq_hz = 1.0 / T
    phase = rng.uniform(0.0, 2.0 * math.pi)

    accel = [
        1.0 + a_orb_g * math.sin(2.0 * math.pi * freq_hz * t + phase)
        + rng.gauss(0.0, dither_g)
        for t in times
    ]
    accel = [a if a > 0.0 else 0.0 for a in accel]
    return times, accel


def board_slap(
    rate: float,
    peak: float,
    duration_s: float,
    fs_hz: float = 200.0,
    seed: int = 0,
    spike_ms: Tuple[float, float] = (10.0, 40.0),
    baseline_dither_g: float = 0.02,
    exclude_windows: Optional[List[Tuple[float, float]]] = None,
) -> Stream:
    """Structure-borne board-slap transients: short spikes ABOVE 1 g with
    NO preceding free-fall dip — the false-positive risk research.md §6
    actually flags (as opposed to chop, which the physics rules out).

    Arrival times are a Poisson process at `rate` events/second (seeded
    exponential inter-arrival times — the standard construction). Each
    event is a 10-40 ms spike (spike_ms) at magnitude `peak` g (jittered
    +/- ~0.25 g; property-test sweeps vary `peak` itself across the
    plausible 3-6 g range research.md cites, rather than baking that range
    in here). Between spikes the baseline is a tiny dither around 1 g —
    deliberately small, because "no preceding free-fall" is the whole
    point of this regime: a slap must never itself dip toward 0 g first.

    `exclude_windows`: optional [(start_s, end_s), ...] time ranges where
    no slap may land. Used by the "real jump on a noisy background"
    property test: a board can't be slapping through chop AT THE SAME
    INSTANT it is airborne (those are mutually exclusive physical states),
    and the detector's single-threshold landing rule means a stray slap
    landing inside a real flight would (correctly, per that rule) be read
    as the landing — a real and separate collision hazard, not something
    this generator should paper over by chance. Excluding the known
    flight window(s) keeps that test about background noise, which is
    what it's meant to probe.
    """
    if fs_hz <= 0:
        raise ValueError("fs_hz must be positive")
    rng = random.Random(seed)
    n = int(duration_s * fs_hz)
    times = [i / fs_hz for i in range(n)]
    accel = [1.0 + rng.gauss(0.0, baseline_dither_g) for _ in range(n)]

    guard_windows = exclude_windows or []

    def _excluded(t: float) -> bool:
        return any(lo <= t <= hi for lo, hi in guard_windows)

    events: List[float] = []
    if rate > 0.0:
        t = 0.0
        while True:
            t += rng.expovariate(rate)
            if t >= duration_s:
                break
            if not _excluded(t):
                events.append(t)

    for te in events:
        dur_s = rng.uniform(*spike_ms) / 1000.0
        amp = max(0.0, peak + rng.gauss(0.0, 0.25))
        i0 = max(0, int(te * fs_hz))
        i1 = min(n, i0 + max(1, int(dur_s * fs_hz)))
        for i in range(i0, i1):
            accel[i] = amp + rng.gauss(0.0, 0.15)

    accel = [a if a > 0.0 else 0.0 for a in accel]
    return times, accel


def handling_chatter(
    amp: float,
    duration_s: float,
    fs_hz: float = 200.0,
    seed: int = 0,
    baseline_g: float = 1.0,
    low_freq_fraction: float = 0.4,
    low_freq_tau_s: float = 0.05,
) -> Stream:
    """Broadband jitter for the "carried around, not riding" regime.

    Modeled as two superposed components rather than pure i.i.d. Gaussian,
    so a sustained (multi-sample) low excursion is at least *possible* to
    probe instead of being ruled out by construction:

      * a high-frequency component: i.i.d. Gaussian each sample — flat
        (broadband) spectrum out to Nyquist, the literal "jitter."
      * a low-frequency component: a mean-reverting AR(1)/Ornstein-
        Uhlenbeck process with time constant low_freq_tau_s (default 50 ms,
        the same order as the detector's freefall_confirm_s=0.08 s) —
        representing slower arm-swing-scale motion, which (unlike white
        noise) CAN sustain an excursion across several consecutive
        samples. This is deliberately the more adversarial of the two: it
        gives the false-positive property tests an honest chance to find
        something, rather than testing a straw-man model that can't dip
        for 80 ms by construction.

    The two components' variances are weighted (by low_freq_fraction) to
    sum back to `amp**2`, so `amp` alone still sets the overall jitter
    scale a caller/sweep would expect.

    Caveat (a modeling limitation, not a proven-safe claim): this is still
    a stationary, zero-mean process. A real handled device could see a
    genuine sustained deceleration (e.g. actually dropped, or swung hard
    downward) that this model does not represent — this generator covers
    "ordinary carrying jitter," not "the device was in true free-fall
    because someone dropped it," which is a different (and uninteresting
    for detector false-positive purposes — it *should* trigger) scenario.
    """
    rng = random.Random(seed)
    n = int(duration_s * fs_hz)
    times = [i / fs_hz for i in range(n)]
    dt = 1.0 / fs_hz

    frac = max(0.0, min(1.0, low_freq_fraction))
    w_lf = math.sqrt(frac)
    w_hf = math.sqrt(1.0 - frac)
    sigma_lf = amp * w_lf
    sigma_hf = amp * w_hf
    rho = math.exp(-dt / low_freq_tau_s) if low_freq_tau_s > 0.0 else 0.0
    innov_scale = math.sqrt(max(0.0, 1.0 - rho * rho))

    x = 0.0
    accel = [0.0] * n
    for i in range(n):
        x = rho * x + innov_scale * rng.gauss(0.0, sigma_lf)
        accel[i] = baseline_g + x + rng.gauss(0.0, sigma_hf)

    accel = [a if a > 0.0 else 0.0 for a in accel]
    return times, accel


def superpose(*streams: Stream) -> Stream:
    """Superpose same-length/same-timebase |a| streams onto ONE shared 1 g
    baseline: sum each stream's deviation from 1 g, then add back a single
    1 g (naive addition would double- or triple-count gravity, since every
    generator above already includes its own 1 g baseline).

    All streams must share the same duration_s and fs_hz (so the same
    times_s array applies to all of them).
    """
    if not streams:
        raise ValueError("superpose() needs at least one stream")
    times0 = streams[0][0]
    n = len(times0)
    for t, a in streams:
        if len(t) != n or len(a) != n:
            raise ValueError(
                "superpose(): all streams must share the same length/timebase "
                "(same duration_s and fs_hz)"
            )
    accel = [1.0 + sum(a[i] - 1.0 for _, a in streams) for i in range(n)]
    accel = [a if a > 0.0 else 0.0 for a in accel]
    return times0, accel
