# STATUS — what is true now

**This file wins.** If any other document disagrees about status, this one is
right and that one is stale. Fix the other one.

Rewritten 2026-08-23, from 2,507 lines to this. The old file was 33 dated
entries across ~190 sections — a log, kept in prose, beside a git history that
already stored it perfectly. It failed the one job it had: on 2026-08-20 a
fact written correctly *inside it* was missed anyway, because nothing could
reach it at the moment of use. The chronology lives at
`archive/docs-2026-08-23`; the commits are the log. **Do not rebuild the log
here.**

Every claim below cites a `file:line`, a commit, or a measurement — never
another document. Docs are the thing under suspicion.

---

## READ THIS FIRST

| Question | Answer | Authority |
|---|---|---|
| Which boards exist, and which has a **battery**? | **Only the OG (`JumpHeight-E2C4`).** The spare (`45ED`) and the Puck (`8673`) are USB-only; their battery readings are a floating divider — noise. | `docs/bench-playbook.md` §1 |
| Which board can measure power or run untethered? | **The OG only.** Drain, endurance and DC/DC numbers are meaningless elsewhere. | same |
| Why did my BLE reading change between calls? | **Three boards can advertise.** Unpinned tools answer from whichever replies first. Always `--name`. | `tools/blepin.py` |
| Can I trust a "dead board" verdict? | **No — four have been wrong.** Nothing was ever damaged. Establish the board's *configuration* first. | `docs/xiao-hardware-truth.md` |
| What firmware is on the OG? | **`src=76df4a83`.** Confirm with `stats`; never infer from a commit date. | live read, 2026-08-24 |
| Are the OG's heights trustworthy today? | **Bench-calibrated, yes** — drop ritual re-run 2026-08-24: 8 drops from 101.6 cm, bias −19 ms ±9, `airtime_offset_s=0.0192`, `off_src=device`, survived a reflash. `height_scale` remains defaults *by design* until the on-water video calibration. | live read, below |
| How does the app reach the rider's watch? | **Connect IQ store approval only.** Sideloading is impossible on the Instinct 3. | `docs/watch.md` |
| When is the water day? | **No date exists anywhere in this repo.** The freeze is *defined* as ≥4 days before it, so there is no freeze window. | — |

---

## The OG, read over USB on 2026-08-24

```
INFO fw=0.4.3 sample_hz=200 log_hz=50 motion_thresh_g=0.12 idle_timeout_s=20
     ble=1 vbat_mv=4094 batt_pct=93 chg=1 src=76df4a83
CAL  airtime_offset_s=0.0192 height_scale=1.000
     source=device off_src=device scale_src=defaults vbat_src=defaults
SELFTEST i2c / whoami / accel / noise / ble / flash — 6/6 PASS
STATS stored_jumps=0 (session synced to two copies, then cleared)
```

`dcdc=1` at every boot (audit F-05). The drop calibration was applied live,
then **baked and reflashed the same night, and survived the reflash in NVS**
— the flashed build and the tree agree at `src=76df4a83`. The pre-audit
session (18 jumps, 1.58 MB trace) is in `data/sessions/20260824-183054` and
a verified second copy.

---

## Open gates before the water day

Ordered by what blocks what.

1. **Set the date.** Everything sequences off it, and no freeze window can
   begin without it.
2. **Connect IQ store: SUBMITTED 2026-08-24**, from a same-night rebuild
   (79,136 B, 4/4 variants). In review — Garmin's banner says up to 3 days.
   On approval: install via the Connect IQ phone app on the rider's watch,
   then the desk sequence in `docs/watch.md` (two-central test is the
   highest-value item). The `.iq` stays gitignored — **rebuild before any
   resubmission; size and mtime are the only handle on which build is on
   disk.**
3. ~~Re-run the drop calibration on the OG.~~ **DONE 2026-08-24** — 8 drops,
   101.6 cm, bias −19 ms ±9, saved to device NVS and `config/params.json`,
   baked into `src=76df4a83`, survived the reflash. The pending flash batch
   (F-25 help string + comment updates) landed in the same evening; tree and
   device agree and the session card's provenance gate is green.
4. **Week-0 items: four of five have not started.** Named 2026-08-20 as
   "latency-gated, begin regardless of era" — the saltwater adhesive coupon (a
   six-month soak needs six months of calendar), a temperature logger, and the
   µA-meter decision. Only the store package moved.
5. **Glue vs removable** — undecided, and the answer deletes or keeps an entire
   era of work.

## Open findings

`docs/audit-2026-08-22.md` carries the detail. F-01…F-21 are closed.

| | Severity | What |
|---|---|---|
| **F-22** | minor | `trace_bytes()` over-reports once the region fills; self-corrects at the next boot |
| **F-23** | minor | Full-chip mount is ~80× empty (74 ms vs 0.93 ms). The walk is the floor; no counter scheme fixes it |
| **F-24** | minor | Self-arm cannot bootstrap at a small lever arm. **Not reachable** — `JH_SPIN_SELFARM_ENABLED = 0` |
| ~~F-26~~ | closed | `sim/selfdiag.py` had no test at all — 11/11 mutants survived a 223-test run. Now 17 tests, 10/11 mutants killed |
| ~~F-27~~ | closed | `jump eval --split` was unguarded; inverting the filter passed the suite. Now killed by a partition property test |
| ~~F-25~~ | closed | `jump status` reported the help *string* as "commands in binary", hiding `gyro`/`pincensus`/`vbatscan`. Tool label fixed 08-23; help string shipped in the 08-24 flash (`42dbd59`, on-device at `src=76df4a83`) — `jump status` now shows 21 with no gap |

## Closed recently — do not re-open

- **The float32 timebase** (`glue-and-forget.md` §3a), the six-month
  silent-jump-dropping failure. `jump_detector.h:62` and `:152` are `double`;
  `jh_store.cpp:1001` no longer re-narrows `atof`; `trace_codec.h:224` uses
  `llround` with an explicit int32 bound. Falsifier passes:
  `tools/tests/test_timebase_falsifier.py:48`. Commit `37394ae` also caught a
  hazard *inside the fix* — an abort that would have killed a puck at 24.9 days.
- **`jump eval` discovered sessions only one level deep**, so the repo's only
  `labels.csv` was invisible for the eight days it existed, and placeholder
  labels were scored as truth. Fixed in `e9fa917`: discovery is recursive, and
  inadmissible ground truth is refused with a stated reason.
- **Cold-boot selftest false FAIL** — `main.cpp:494-516`, DECISION #35.
- **The detector gate question, open since 2026-08-15, is CLOSED** — DECISION
  #41. E7/E8 recommended `freefall_enter_g` 0.26; E11 measured what nobody
  had, at 400,000 paired jumps: the recommendation misses **48** where the
  shipped gate misses **3**, 45 of them missed by it alone. It stays at
  **0.35 / 0.25**, and the 2026-08-24 drop calibration measured at those
  gates therefore stands — no re-drop.

## Retracted — do not resurrect

A number nobody measured is not a number. These were published, then withdrawn:

- **"100% detected"** in the wing ballistic sim — undersampling. The 200k rerun
  found 5 silent misses, all at the 0.35 g free-fall gate; RMSE is **4.6 cm**
  (not the earlier 4.2 cm) and the >1.20× tail was noise.
- **16.3 mA** gauge figure, **11.6 mA** walk figure, **"~15 h endurance"**.
- **"OTA on a glued device is proven"** — bench-only. A mid-transfer
  disconnect leaves a dark bootloader that only a physical reset recovers, and
  the `dfu` trigger is **unauthenticated**: any BLE peer in range can command a
  puck into its bootloader.
- **The "124 KB vs 32 KB" watch-memory fear** — that was PRG *file size*.
  Measured static memory is 12,417 B of 32,768 B.
- **The ~7–11 mA / 25.7–34 h power figures** are *pending*, not corrected.
  DC/DC changes the regime and nobody has re-measured.

## Known-unmeasured

Stated plainly so an absence is never mistaken for a pass:

- The detector has **never seen water.** Every number is bench or simulator.
- **Nothing has tested whether a 1.5 m reading means 1.5 m.**
- Off-current has never been measured and is **unmeasurable** with the
  instruments this project owns — cell self-discharge is the same order as the
  signal.
- The Instinct has never rendered a jump. All watch evidence is Epix.
- `jump drop` has 0 non-fake runs; `jump monitor` and `setup` have no test
  coverage.

---

## Using this file

- **A state is a claim about the past, not a promise.** "Proven on hardware"
  means it worked on a date, on a build.
- `./tools/jump status` machine-checks what it can — the build, the suites and
  a live device — and marks the rest UNKNOWN rather than assuming.
- **No plan may list work without checking here first.** That one step would
  have prevented every rediscovery this project has paid for.
