# Session report — 20260906-192422

**Device:** 15 jumps, best 0.95 m (3.1 ft)
**Offline re-analysis:** 12 jumps, best 1.04 m

Agreement: ⚠️ live vs offline differ — see tables; consider tuning config/params.json and re-running `./tools/jump replay --csv /Users/joshcrow/Jump-height/data/sessions/20260906-192422/trace.csv`

| # | airtime (s) | height (m) | height (ft) |
|---|-------------|------------|-------------|
| 1 | 0.82 | 0.82 | 2.7 |
| 2 | 0.82 | 0.82 | 2.7 |
| 3 | 0.88 | 0.95 | 3.1 |
| 4 | 0.34 | 0.14 | 0.5 |
| 5 | 0.81 | 0.81 | 2.7 |
| 6 | 0.79 | 0.77 | 2.5 |
| 7 | 0.86 | 0.91 | 3.0 |
| 8 | 0.29 | 0.10 | 0.3 |
| 9 | 0.78 | 0.75 | 2.4 |
| 10 | 0.50 | 0.30 | 1.0 |
| 11 | 0.29 | 0.11 | 0.3 |
| 12 | 0.35 | 0.15 | 0.5 |
| 13 | 0.69 | 0.58 | 1.9 |
| 14 | 0.68 | 0.57 | 1.9 |
| 15 | 0.77 | 0.72 | 2.4 |

Params: `Params(g=9.80665, freefall_enter_g=0.35, freefall_confirm_s=0.08, landing_threshold_g=2.5, landing_settle_s=0.5, min_airtime_s=0.25, max_airtime_s=3.0, airtime_offset_s=0.0192, height_scale=1.0, spin_lever_m=0.0)`

_Note: the device accumulates jumps until `clear`; timestamps reset each power-up. Sync + clear after every session to keep reports one-session-per-file._
