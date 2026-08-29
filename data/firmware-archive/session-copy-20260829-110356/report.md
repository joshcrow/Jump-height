# Session report — 20260829-110356

**Device:** 12 jumps, best 1.13 m (3.7 ft)
**Offline re-analysis:** 12 jumps, best 1.48 m

Agreement: ⚠️ live vs offline differ — see tables; consider tuning config/params.json and re-running `./tools/jump replay --csv /Users/joshcrow/Jump-height/data/sessions/20260829-110356/trace.csv`

| # | airtime (s) | height (m) | height (ft) |
|---|-------------|------------|-------------|
| 1 | 0.96 | 1.13 | 3.7 |
| 2 | 0.53 | 0.35 | 1.1 |
| 3 | 0.54 | 0.36 | 1.2 |
| 4 | 0.38 | 0.18 | 0.6 |
| 5 | 0.88 | 0.95 | 3.1 |
| 6 | 0.37 | 0.17 | 0.6 |
| 7 | 0.41 | 0.20 | 0.7 |
| 8 | 0.27 | 0.09 | 0.3 |
| 9 | 0.30 | 0.11 | 0.4 |
| 10 | 0.83 | 0.84 | 2.8 |
| 11 | 0.95 | 1.11 | 3.7 |
| 12 | 0.93 | 1.06 | 3.5 |

Params: `Params(g=9.80665, freefall_enter_g=0.35, freefall_confirm_s=0.08, landing_threshold_g=2.5, landing_settle_s=0.5, min_airtime_s=0.25, max_airtime_s=3.0, airtime_offset_s=0.0192, height_scale=1.0, spin_lever_m=0.0)`

_Note: the device accumulates jumps until `clear`; timestamps reset each power-up. Sync + clear after every session to keep reports one-session-per-file._
