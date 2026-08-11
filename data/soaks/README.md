# Soaks — long-run device records

Multi-hour `stats` logs from a real board, captured by
[`tools/chargelog.py`](../../tools/chargelog.py). Distinct from
`data/sessions/`, which holds jump captures with the schema
[docs/data-pipeline.md](../../docs/data-pipeline.md) defines — a soak has no
labels and no ground truth. It answers "does the thing survive, and what does
the battery actually do", which is not a question about jumps.

Kept in the repo because **re-acquiring one costs a night of wall-clock**.
That is the bar for a file living here: if reproducing it is a matter of
minutes, do not commit it.

---

## `20260810-charge-and-stability-soak.csv`

**548 readings at 60 s, 2026-08-10 22:54 → 2026-08-11 08:49 (9.91 h).**
Seeed XIAO nRF52840 Sense, 250 mAh cell, USB attached throughout, bare board
on a desk (pre-enclosure). Serial, so no BLE central was competing.

### ⚠️ Read this before using any voltage in this file

**Every `vbat_mv` here is ~125 mV LOW.** The capture predates the fix in
`10d26a5`: `vbat_mv()` was still going through `analogRead()` at the SAADC's
3 µs default acquisition time, too short to charge through the divider's
~340 kΩ source impedance (SENSE_FIRST_BOOT.md item 24). Two independent
errors were live at capture time:

| | |
|---|---|
| ~50 mV | acquisition time — **fixed** in `10d26a5` (now 15 µs) |
| ~75 mV | per-unit divider/reference gain — still uncorrected here |

So a "4065 mV" row is a cell actually sitting near **4190 mV**. Do not use
this file as a voltage reference without adding the offset back; DO use it
for shapes, durations, and the timing of events, none of which the error
touches.

### What it captured

- **A full charge**, 3612 → 4065 mV (as read), 7% → 89%, in **3.47 h** —
  the first empirical charge-time figure for this cell, against
  docs/sense.md's datasheet-derived estimate.
- **Charge termination**, `chg 1→0` at **02:23:00**. The BQ25101 deciding it
  is done, observed rather than assumed.
- **340 resting readings** after termination: 4035–4068 mV, drifting −30 mV
  over ~6.4 h on the charger. This is what "full and settled" looks like,
  and it is why the percent curve's 100% anchor at 4200 mV is wrong — 4200
  is a CHARGING voltage that a rested cell never shows.
- **A 9.91 h stability run**: 548 consecutive successful reads, **zero**
  `NO REPLY` and zero port errors. The longest continuous run this firmware
  has had.

### What it does not answer

Off-current. The board was on USB the whole time, so the drain question
(item 25c) is untouched — that needs the cable OUT and `off` sent. The
method for it is written up in item 25c.

`trace_bytes` grew only ~112 kB over the night: the motion gate correctly
idled on a quiet desk, so this is NOT the marathon-fill soak (item 19)
either. That one needs a genuinely active session.
