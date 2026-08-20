# The Garmin-only reality — what changes when the watch is the only interface

Written 2026-08-19, on the owner's direction: *"in the future the user will
only ever interact with the puck via Garmin."*

This is a product-shape decision, not a preference, and it reprioritises real
work. It does **not** change the water session — that day involves a laptop
for analysis regardless. It changes what "finished" means afterwards.

---

## 1. What the watch can actually do today

`PuckLink.mc` sends exactly **one** command: `stats`. It subscribes to
notifications and polls. That is the entire outbound surface.

Everything else a puck can be told to do — `dump`, `clear`, `selftest`,
`format`, `mount`, `off`, `dfu` — is reachable only from `tools/jump`, the
web app, or `blecmd.py`. All three require a laptop.

So in a Garmin-only world the user can: see live jumps, see the session best,
see puck battery (via the new advertisement payload), and see `NO REC` if
storage is down. That is a genuinely decent glanceable product.

What they **cannot** do: clear storage, download anything, run a self-test,
recover a wedged puck, or update firmware.

## 2. The gap that actually bites: storage has no lifecycle

The trace region holds roughly **5 hours of recording**. Jumps hold 2048
records. Neither is a ring buffer — both are append-only, and
`trace_is_full()` stops trace writes permanently once reached.

Measured, and confirmed benign in the short term (2026-08-18 soak): when the
trace fills, **jump detection and storage continue at full rate**. The device
does not break; it stops keeping raw samples.

But with no way to clear from the watch, the timeline is:

| after | state |
|---|---|
| ~5 h of cumulative recording | trace region full, raw samples stop forever |
| ~2048 jumps (≈100 sessions) | jump records stop too |

The first row is the one that matters. A user who never opens a laptop gets a
puck that silently stops keeping raw data after its first few sessions —
**with no symptom on the watch**, because jumps keep flowing and the watch
only ever sees jumps.

That is the same failure shape as the unmounted-flash bug fixed this morning:
everything looks healthy, and something quietly stopped.

## 3. Options for the storage lifecycle (decision needed, not urgent)

1. **Ring-buffer the trace** — overwrite oldest. Keeps "the last N hours"
   always available. Biggest change; touches the append-point scan and the
   decode path.
2. **Auto-clear the trace at session start** — on the first motion after a
   long idle, wipe and start fresh. Matches how the user thinks ("this
   session"), and makes `clear` an implementation detail rather than a chore.
   Cheapest correct-feeling option. Now safe to do at all, since `clear` no
   longer watchdog-resets on a full region (fixed 2026-08-19).
3. **Watch-triggered clear** — add a second outbound command. Small firmware
   change, but it puts a destructive operation behind a watch UI, which needs
   a confirmation flow the data-field API is poor at.
4. **Accept it** — declare the trace a developer feature and the FIT record
   the user's data. Defensible: the watch already writes jumps into Garmin's
   own activity file, which syncs to Garmin Connect without us. Costs the
   ability to re-analyse a session later.

**Leaning: (2), with (4) as the honest framing.** The user's data is the FIT
record; the trace is ours, and it should manage itself.

## 4. What this reprioritises

**Up:**
- **The glance/widget** (`garmin/puckglance/`) stops being a nice-to-have and
  becomes the puck's only pre-session health check. It needs to render on a
  real wrist and prove BLE works from glance scope.
- **Everything the watch displays** — battery, `NO REC`, link state — is now
  the entire diagnostic surface. `docs/ble-dependability.md`'s layer 5
  ("distinguish asleep / out of range / flat") is no longer polish.
- **Storage lifecycle** (§3) — previously invisible, now a real product bug.
- **Self-healing behaviour generally.** Nobody is going to type `mount`. The
  30 s auto-remount shipped today is exactly the right instinct; the same
  question should be asked of every other recoverable state.

**Down:**
- **The web app.** Adding the advertised-battery display to it was on
  tonight's list; it is now near-worthless and has been dropped. The web app
  remains a bench/debug tool.
- **CLI ergonomics** for anything user-facing. `tools/jump` is a bench tool,
  and should be documented as one rather than polished as a product surface.

**Unchanged:**
- The water session's tooling. That day has a laptop.
- Download integrity in `tools/jump` — it protects the analysis data, which
  is *our* need and still real.

## 5. The honest statement of where this leaves the product

A Garmin-only puck today would work for a session and then quietly stop
keeping raw data, with no way for its owner to notice or fix it. Everything
else — detection, display, FIT recording, battery visibility, storage
self-heal — is already there.

So the gap between "works" and "shippable to someone who owns only a watch"
is **one storage-lifecycle decision and the glance proven on a wrist.**
That is a much shorter list than it would have been a week ago.
