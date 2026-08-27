# Session report — 20260827-122115

**Device:** 8 jumps, best 0.27 m (0.9 ft)
**Offline re-analysis:** 8 jumps, best 0.26 m

Agreement: ✅ live and offline detection agree

| # | airtime (s) | height (m) | height (ft) |
|---|-------------|------------|-------------|
| 1 | 0.47 | 0.27 | 0.9 |
| 2 | 0.46 | 0.26 | 0.9 |
| 3 | 0.46 | 0.26 | 0.9 |
| 4 | 0.46 | 0.26 | 0.9 |
| 5 | 0.46 | 0.26 | 0.9 |
| 6 | 0.47 | 0.27 | 0.9 |
| 7 | 0.44 | 0.24 | 0.8 |
| 8 | 0.47 | 0.27 | 0.9 |

Params: `Params(g=9.80665, freefall_enter_g=0.35, freefall_confirm_s=0.08, landing_threshold_g=2.5, landing_settle_s=0.5, min_airtime_s=0.25, max_airtime_s=3.0, airtime_offset_s=0.0192, height_scale=1.0, spin_lever_m=0.0)`

_Note: the device accumulates jumps until `clear`; timestamps reset each power-up. Sync + clear after every session to keep reports one-session-per-file._
