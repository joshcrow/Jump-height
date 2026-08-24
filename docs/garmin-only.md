# The three surfaces — who uses what, and what each must be able to do

Written 2026-08-19 on the owner's direction, **corrected the same evening**
after a first draft wrongly concluded the web app was near-worthless.

| surface | user | job |
|---|---|---|
| **Garmin watch** | the end user | the product. Glanceable state during a session, jumps into the FIT activity. Read-only by design. |
| **Web app** (phone) | the owner, in the field | **admin on the go.** The laptop's job when there is no laptop: check the puck, sync, clear, self-test, from a phone at the beach or in the van. |
| **`tools/jump` on the Mac** | development | the bench. Deep diagnostics, flashing, analysis, experiments. |

The first draft of this file dropped web-app work as low value. That was
wrong: the web app is not a lesser copy of the CLI, it is the **only admin
surface that travels with the owner.** Anything a user in the field might
need to do to a puck has to be possible there.

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

## 1b. Where the roadmap is going: the activity gets richer than jumps

The owner's stated direction: the Garmin activity should eventually capture
**time on foil** and similar riding metrics, not just jumps.

Two consequences worth writing down now:

- **The FIT developer-field path is the product's real output**, not a
  side-effect. It already carries `jumps`, `best_jump`, `best_airtime` and a
  per-record `jump_height` stream (verified on a real activity 2026-08-18 —
  *attributed 2026-08-23: that verification ran on the owner's Epix Gen 2,
  not the rider's Instinct 3 Solar*, `docs/STATUS.md:702,721-726`).
  Adding time-on-foil means adding fields to a path already proven
  **on that watch**; the Instinct's own FIT output is unconfirmed pending
  the store install (`docs/store-submission-runbook.md`).
- **It changes what the water session is for.** Beyond answering "are wing
  jumps ballistic", that day is the **first and only source of real foiling
  data** — the training set for every future metric. Time on foil is an IMU
  classification problem (a foiling ride is smooth; a non-foiling one slaps
  chop) and it cannot be developed from hand tosses or pocket walks. The
  50 Hz trace, which §3 option (4) was tempted to dismiss as a developer
  luxury, is exactly the raw material that work will need.

So: keep the trace, and treat the water day as data collection for a roadmap,
not just a single yes/no experiment.

## 2. The gap that actually bites: storage has no lifecycle

The trace region holds roughly **5 hours of recording**. Jumps hold 2048
records. Neither is a ring buffer — both are append-only, and
`trace_is_full()` stops trace writes permanently once reached.

Measured, and confirmed benign in the short term (2026-08-18 soak): when the
trace fills, **jump detection and storage continue at full rate**. The device
does not break; it stops keeping raw samples.

The watch cannot clear. **The web app can** — which is what makes this
manageable rather than fatal, provided the owner has a phone in the field.
The timeline if nobody clears:

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

**DECIDED 2026-08-19 (owner: "a go as long as we're smart about it"), option
(2), implemented with a deliberately narrow rule — see below.
Leaning-as-written, kept for the trail:** Auto-clear at
session start means the common case needs nobody; the phone covers the rest.
Option (4) — "declare the trace a developer luxury" — is now **rejected**:
§1b makes the trace the training data for time-on-foil and every metric after
it.

### 3a. What was actually built (2026-08-20)

`jh_store::trace_clear()` — erases the trace region **only**, leaving every
stored jump intact, through the same watchdog-fed erase path that `clear()`
needed. Jumps are the user's history and the watch's reconnect source; wiping
them to make room for trace would trade the user's data for ours.

The policy in `main.cpp` fires only when **all three** hold:

1. **the trace is already FULL** — so it is recording nothing, and clearing it
   cannot make the present worse. This is the safety keystone: a trace that is
   still doing its job is never touched.
2. **the board has been still for ≥1 h** — a session boundary, not a pause.
   Far longer than sitting on the board between runs; far shorter than the gap
   between outings.
3. **motion has just resumed** — a session is actually starting, so the space
   is about to be needed.

The trade, stated plainly in the code: old raw data is sacrificed so the
current session records. **Losing the session you are actually riding beats
losing one you already had a chance to sync.** Sync first — phone or laptop —
if an old trace matters.

Deliberately NOT done: clearing on a timer, on boot, or whenever the trace is
merely getting full. Each of those can delete a trace that is still working.

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

**Also up — corrected:**
- **The web app as field admin.** It must cover what the owner needs *without
  a laptop*: sync with the same integrity gate the CLI now has (**done
  2026-08-20**), clear, and self-test. Anything only `tools/jump` can do is
  unavailable at a beach.

  **But NOT the advertised battery — that one is impossible in a browser.**
  Web Bluetooth has no way to read advertisement data without connecting:
  `watchAdvertisements()` is not shipped in stable Chrome, and advertisement
  scanning (`requestLEScan()`) is listed as future work on Chrome's own
  capabilities page. `requestDevice()` can *filter* on manufacturer data
  (Chrome 92+) but never hands you its contents.

  So there is a real asymmetry worth knowing: **the watch glance can show
  puck battery before connecting and the phone cannot.** Garmin's BLE API
  permits scanning; the browser's does not. The web app's battery readout
  stays a post-connect number, which is fine — connecting from the phone is a
  deliberate act anyway.

**Down:**
- **CLI ergonomics** for anything a field user would need. `tools/jump` stays
  the development bench and should be documented as one — its job is depth,
  not portability.

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

And the gap for the OWNER in the field is narrower still: the web app needs
the advertised battery and the download-integrity gate that landed in the CLI
today. Neither is hard; both are now known to be needed.
