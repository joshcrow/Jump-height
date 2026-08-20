# Future metrics — what we could measure, and what would close the door on it

Written 2026-08-19, on the owner's direction to think past jumps ("time on
foil and carving g forces? … make sure we don't close off the options").

The point of this file is not a feature list. It is a **recording audit**: you
can always invent a new metric from data you kept, and you can never invent
one from data you threw away. So the question that matters before a freeze is
not "what should we compute?" but **"what are we failing to keep?"**

---

## 1. The one architectural fact that governs everything

The 50 Hz trace stores **`u16` magnitude in milli-g, and nothing else**
(`sim/trace_codec.py`: `count * u16 little-endian magnitude in milli-g`).

- Three accelerometer axes → collapsed to one scalar before storage.
- The gyro → **read every sample and discarded** (`main.cpp` reads it; only
  the per-jump summary survives).

That single decision is what opens or closes most of the list below. It was
the right call for jump detection — |a| is all the detector needs, and it
buys ~5 hours of recording in 1.9 MB. It is the binding constraint on
everything else.

## 2. What magnitude-only ALREADY supports (more than I expected)

These need no format change. The water session's data can develop all of
them:

| metric | signal in \|a\| | notes |
|---|---|---|
| **Time on foil** | variance / high-frequency energy. Foiling is *smooth* (the board is out of the water); non-foiling **slaps chop**. | The flagship. A windowed variance threshold is probably a first cut; the trace has exactly what a classifier needs. |
| **Carve intensity** | sustained \|a\| above 1 g during riding | Magnitude cannot give *direction* (heel vs toe), but a hard carve raises total specific force and that is the number a rider actually brags about. |
| **Landing quality** | peak \|a\| at the landing spike, already detected | soft vs slammed. Free — the detector finds the spike already. |
| **Chop / conditions severity** | baseline \|a\| variance while riding | a "how rough was it" session stat, and a useful covariate for every other metric. |
| **Pump frequency / cadence** | FFT or zero-crossing of \|a\| while foiling | pumping is periodic and distinctive. |
| **Airtime distribution** | already per-jump | we store only best; total air time and a histogram are free from `jumps.csv`. |
| **Takeoff intensity ("pop")** | \|a\| in the pre-takeoff window | how hard the rider loaded the board. |
| **Touch-downs / breaches** | brief \|a\| spikes inside a foiling stretch | foil ventilating or the board kissing the water. |

## 3. What magnitude-only CLOSES OFF

These are unrecoverable from the current trace, no matter how clever the
offline analysis:

| metric | needs | why lost |
|---|---|---|
| **Carve direction** (heel vs toe, which way) | 3-axis accel | direction is destroyed by `sqrt(x²+y²+z²)`. |
| **Rotation / spins / 360s** | gyro | discarded per sample. |
| **Board pitch & roll, foil ride height proxy** | 3-axis + gyro | orientation cannot be reconstructed from a scalar. |
| **Separating vertical from lateral g** | 3-axis | a 1.5 g carve and a 1.5 g chop hit are identical in \|a\|. |
| **Tacks / gybes as board events** | gyro (heading change) | the watch's GPS can infer these instead — see §5. |

**Per-jump we do keep gyro**: `JumpRecord` carries `med_w_dps` (median
airborne |ω|) alongside `med_a_mg`, `med_acorr_mg`, `n_air`. So rotation is
summarised for jumps and lost for everything else.

## 4. The cost of keeping more

Storage is the whole trade. At 50 Hz in a ~1.9 MB region:

| stored per sample | bytes | region holds |
|---|---|---|
| \|a\| only (today) | 2 | **~5 h** |
| 3-axis accel | 6 | ~1.7 h |
| 3-axis accel + 3-axis gyro | 12 | ~50 min |
| 3-axis accel @ 25 Hz | 6 | ~3.4 h |
| \|a\| @ 50 Hz + 3-axis @ 10 Hz | 2 + 1.2 | ~3 h |

A ~2 h session fits comfortably in the last two. The hybrid row is
attractive: it keeps the detector's input untouched at full rate (zero
detection risk) and adds a low-rate orientation channel purely for analysis.

Other levers, cheaper than they sound:
- **Delta encoding.** Consecutive samples differ by little; a varint delta
  would likely halve the accel cost with no information lost.
- **Event-triggered detail.** Store 3-axis only in windows the detector
  already flags (airborne, high-g), magnitude everywhere else. Best ratio of
  information to bytes, most complexity.
- **The JumpRecord still has spare bytes** — the flight-physics fields fit in
  bytes that were already being wasted, and `_pad[2]` plus `crc2` framing
  means another per-jump scalar or two is nearly free. Anything summarisable
  *per jump* costs almost nothing.

## 5. Division of labour: the watch already knows things we shouldn't duplicate

The puck should measure **board motion**. The watch measures **the rider and
the world**, natively, and Garmin Connect already stores it:

- GPS speed, top speed, distance, track → **watch**. Never re-derive from IMU.
- Heart rate, session duration, calories → **watch**.
- Runs, tacks, gybes → inferable from **GPS heading**, more reliably than
  from a board-mounted gyro.
- Jumps, airtime, height, g-forces, foiling state → **puck**. Nothing else
  can see these.

So the product's job is to add the board-motion layer to an activity that
already has everything else. That framing also keeps the FIT field list
short and honest.

## 6. Recommendation, and what NOT to do before the water

**Do not change the trace format before the session.** Magnitude-only
supports §2 — which includes time on foil, the flagship — and a format change
touches the encoder, the append-point scan, the decoder, and the detector's
own input. That is precisely the class of change the freeze protocol exists
to forbid, for a metric nobody has requested yet.

**Do, before the session (all free):**
1. **Make sure the whole session is recorded.** ~2 h fits in the ~5 h region,
   and the storage-lifecycle decision (`garmin-only.md` §3) must not auto-wipe
   mid-session. This is the one way to lose everything.
2. **Note in the session log what the rider was doing when** — even coarse
   ranges. Time-on-foil is a *classification* problem and it needs labels;
   the `tools/label.py` "none/jump" vocabulary should grow a `foiling` /
   `not-foiling` region kind. Cheap now, impossible retroactively.
3. **Keep the gyro-summary fields populated** — they are already written per
   jump and cost nothing.

**Do, after the session, in this order:**
1. Develop **time on foil** offline from the real trace. It needs no firmware
   change and it validates the whole "richer activity" direction.
2. Then decide the trace format (v3) with real data in hand — by then we will
   know whether the classifier wanted axes, and how badly.
3. Add FIT developer fields as metrics prove out. The path is already proven
   (`jumps`, `best_jump`, `best_airtime`, per-record `jump_height` verified on
   a real activity 2026-08-18); each new field is additive.

## 7. The one-sentence version

Magnitude-only keeps far more doors open than it looks — including time on
foil and carve intensity — and the doors it closes (direction, rotation,
orientation) should be reopened deliberately with a v3 format **after** the
water session tells us which ones are worth the bytes.
