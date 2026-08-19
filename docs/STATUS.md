# STATUS — the single source of truth

Generated 2026-08-14 from an evidence-first audit of the code,
tests, commits and bench logs — not from other documents.

## Why this file exists

This project repeatedly **rediscovered work it had already finished**. The watch
corruption gate was listed as TODO while fully built and tested. Two healthy boards
were declared dead hardware. A completed drop calibration was described as never
done. Each time, the cost was hours or days.

The cause is not carelessness, it is architecture: status lived in prose, in a dozen
documents, each written at a moment and never revisited. **Prose drifts. Artifacts
do not.**

### The rules that make this file trustworthy

1. **This file wins.** If any other document disagrees about status, this one is
   right and that one is stale. Fix the other one.
2. **Every state cites evidence** — a `file:line`, a test name, a commit, or a
   measurement. *Never* another document. Docs are the thing under suspicion.
3. **No plan may list work without checking here first.** That single step would
   have prevented every rediscovery listed above.
4. **`./tools/jump status` machine-checks what it can** — which commands are really
   in the built binary, whether the suites really pass, what a connected board
   really reports — and warns when this file is older than the newest code change.
5. **A state is a claim about the past, not a promise.** `proven-on-hardware` means
   it worked on a date, on a build. It does not mean it works now.

### What the states mean

- **PROVEN ON HARDWARE** — observed working on the real device, with a date or commit.
- **PARTIAL** — works for some cases only — the limit is stated.
- **BUILT, UNVERIFIED** — code exists; no test and no hardware run. Assume nothing.
- **TESTED (sim/host only)** — automated tests pass; has NEVER run on real hardware.
- **NOT BUILT** — does not exist, whatever any other document implies.

---

## CHANGED AFTER THE AUDIT — grouped by date, newest first (2026-08-14 → 2026-08-18)

## 2026-08-19

### SESSION-BLOCKING BUG FOUND AND REPRODUCED: `clear` bricks storage on a full region
- **State:** reproduced on hardware, fix built (`src` pending flash to the spare)
- **The bug:** `jh_store::clear()` erases the superblock and then every used
  sector of both regions in a tight loop with **no watchdog feed**. A 4 KB
  sector erase is ~40 ms; a full trace region is 495 sectors ≈ **20 s against
  a 3.5 s watchdog**. The board resets around sector ~87 — and because the
  **superblock is erased FIRST**, it comes back **unmountable**. The new 30 s
  auto-remount cannot rescue it either: `try_mount` deliberately refuses to
  format a virgin chip.
- **REPRODUCED 2026-08-19** on the spare with a 13.4 MB region: the CDC port
  dropped mid-command (the classic reset signature) and the board returned
  with **`uptime_s=0.001`**. Every previous `clear` in this project's life ran
  on a near-empty region (~37 sectors, ~1.5 s), which is why it was never seen.
- **Why it is session-blocking:** `docs/session-card.md` puts `clear` on the
  water-day path **twice** — before the session and after the download — over
  BLE, in a sealed capsule that the card itself says not to open.
- **The galling part:** this is the SAME failure already found and fixed for
  `format()` in `bd0334d`. The fixed loop (`eraseChipFed`) sits ~30 lines
  above `clear()` and its own comment reads *"4 KB sector erases run ~40 ms
  each (same call clear() already uses)"* — the fix was applied to one caller
  and not the other.
- **Fix:** every `eraseSector` in `clear()` now goes through a fed wrapper
  (feed before and after — the erase itself is the long part). Built clean;
  awaiting delivery to the spare, then a repeat of the reproduction as proof.
- **Credit:** found by the 2026-08-19 open-threads sweep, not by use.


### Download integrity at 13.4 MB — byte-exact, twice; and the OG's data survived its death
- **State:** proven-on-hardware (2026-08-19 ~11:35)
- **The big one:** the spare's filled region (**13,434,228 bytes**) downloaded
  **twice over USB, byte-for-byte identical** — matching sha256
  (`a037bb62…`), **874,203 well-formed data rows**, ~3.5 min per pull. That is
  **36 % larger than the previous best verification** (9.87 MB, 2026-08-14)
  and far beyond any real session.
- **Why this path deserves the paranoia:** `docs/plan.md` §3.3 calls a session
  that records perfectly and downloads incompletely "the cruellest possible
  failure", and this exact path *had* a silent-drop bug — `emitBytes`
  discarding whole blocks whenever the serial buffer ran short, reproduced
  and fixed on 2026-08-14 with `emitBytesReliable`. Re-verifying it at 13.4 MB
  says the fix scales well past anything the water day can produce.
- **Separately: the OG's data survived a total battery death.** After the 34 h
  run to collapse and the unmounted-flash scare, `jump sync` pulled all 3
  jumps back cleanly (best 1.285 m — the toss the watch reconciled at M2).
  Session `data/sessions/20260819-112656/`.


### TWO-CENTRAL concurrent export: PASS — the original corruption bug's own configuration
- **State:** proven-on-hardware (OG, `ef37e568`, 2026-08-19 ~10:55)
- **The test `ble-dependability.md` §5 asked for and nobody had run.** Two
  simultaneous centrals on one puck: A pulled the full trace export while B
  polled `stats` 20 times throughout.
- **Result:** both centrals received **150,034 bytes, byte-identical
  (matching sha256)**, 9,315 well-formed data rows each, **`tx_drops` absent
  before and after — zero**, uptime unbroken. Link evidence confirms two real
  peripheral connections (A saw 2 READY greets, B saw 1).
- **Why this specific configuration matters:** two subscribers double the
  demand on one shared SoftDevice TX buffer pool, which is what made the
  original watch-corruption bug visible — bytes vanished mid-line, the other
  central saw clean lines at the same moment, and `LineReader` glued the
  survivors into a `JUMP` that still parsed but carried wrong numbers. That
  bug spent three days without a root cause. The layer-1 fix (honor
  `write()`'s return, bounded per-connection retry, latched chunk length)
  shipped 2026-08-14 `built-unverified`, was verified with ONE central under
  240 KB on 08-18, and is now verified in the configuration that broke.
- **Harness:** `tools/dualcentral.py` — compares the centrals' RAW byte
  streams rather than decoded lines, deliberately: decoding first lets a lost
  chunk re-glue into a plausible line, which is exactly how the original bug
  survived three days of looking.


### Boot scan against a NEARLY-FULL trace region — the plan's §3.4 risk, closed
- **State:** proven-on-hardware (spare, 2026-08-19)
- **The risk, verbatim from `docs/plan.md` §3.4:** the boot-time append-point
  scan walks the trace region block by block, *"~19k blocks on a full trace
  region. A well-used puck would reset there, latch StoreGuard, and run the
  whole session storage-less behind a `flash FAIL` row nobody reads at a
  beach. Never seen only because no region has ever been filled — which this
  session would do first."* Watchdog feeds were added 2026-08-14 and had
  never been exercised against a full region.
- **How it was finally testable:** a `fillstore <kb>` bench command writes
  synthetic-but-valid trace through the REAL `trace_append()` path — same
  encoder, same blocks, same CRCs — so the region ends up exactly as a long
  session would leave it. Filling it honestly would take ~5 h of recording;
  this took 6 minutes for 12 MB.
- **Result:** region filled to **13,434,228 bytes with 220,716 free** (from
  ~2 MB free). The board then **cold-booted at 08:30 against that region and
  came up clean** — `SELFTEST flash PASS detail=220716B_free`, `result=PASS`,
  no watchdog reset, no StoreGuard latch, and the free-space figure proves
  the scan found the correct append point rather than giving up.
- **The host build could never have caught this** — `platform/host/
  jh_store.cpp` keeps CSV files and resumes from an O(1) file size. Only
  silicon walks blocks. This is the item that needed real hardware most.


### Fast charge: the PIN is proven driven; the CURRENT gain is NOT cleanly proven
> **AMENDED 2026-08-19 ~12:00 — I overclaimed.** The heading below originally
> read "FAST CHARGE WORKS — verified end to end, saga closed" on the strength
> of ONE span. Three more spans completed since and they disagree:
>
> | span | 50 mA baseline | today | ratio |
> |---|---|---|---|
> | 3600→3700 mV | 51 min | 32 min | **0.63** |
> | 3700→3800 mV | 28 min | 27 min | **0.96** |
> | 3800→4000 mV | 36 min | 30 min | 0.84 |
> | 3600→3800 mV | 79 min | 59 min | 0.74 |
>
> A working 100 mA setting should give ~0.44 net of the board's own draw. We
> got 0.74 overall and one span at 0.96 — no better than the old rate.
>
> **What IS proven:** `hichg=1` under charge where the pre-fix build read
> `hichg=0` on the same board minutes earlier. The firmware drives the pin.
> That half is unambiguous.
>
> **What is NOT proven:** that the extra current reaches the cell. Known
> confounds, none controlled: today's run started at ~2945 mV (deep-discharge
> precharge phase) versus the baseline's 3612 mV; and **I ran BLE load tests
> against this very board during the 3700→3800 window** (the dual-central
> export at ~10:51), which draws current away from charging in the exact span
> that came out worst.
>
> **To settle it:** one clean charge with the board left alone — no syncs, no
> polls beyond the logger — from a comparable starting voltage. Cheap, and it
> is the only honest way to close this.

### Fast charge — the original entry (claims amended above)
- **State:** proven-on-hardware (OG, `ef37e568`, 2026-08-19 ~10:30)
- **Cause side:** `hichg=1` while `chg=1` — the firmware is driving P0.13 for
  the first time ever. The same readback said `hichg=0` on the pre-fix build
  minutes earlier, on the same board, charging: a clean A/B on the instrument
  itself.
- **Effect side, against the archived 50 mA baseline** (`data/soaks/
  20260810-charge-and-stability-soak.csv`, same board, same cell):

  | span | 50 mA baseline | with fix | ratio |
  |---|---|---|---|
  | 3600 → 3700 mV | 51 min | **32 min** | **0.63** |

  Not the textbook 0.50 — expected, since the board's own ~10 mA draw is
  subtracted from charge current at both settings (≈40 net vs ≈90 net, ratio
  0.44 on current, blunted by the CV taper starting inside this span). The
  direction and magnitude are unambiguous, and four prior measurements of the
  broken build all sat at ratio ~1.0.
- **The whole chain, for the record:** shipped 08-16 → measured not working
  four independent ways → `hichg=` readback built 08-17 → readback said the
  pin was never driven 08-18 → root cause found the same day (a `#define` in
  `main.cpp` consumed by `#if` in `jh_power.cpp`, a different translation
  unit, so the feature compiled to nothing) → fix flashed and proven 08-19.
- **Practical:** a quarter-to-full top-up drops from ~4 h to ~2 h, and the
  cell's own 250 mA ceiling means the charger — not the battery — remains the
  limit (task #20).



### DATA SURVIVES A TOTAL BATTERY DEATH — and storage now self-heals
- **State:** proven-on-hardware (OG, 2026-08-19 ~09:00)
- **The question the death run existed to answer: answered.** After a 34 h
  run to genuine collapse (final board-read ~2.35 V, cell PCM latched off —
  the owner's multimeter read 0 V at the pads, which is the protection
  working), the OG rebooted with its flash unmounted and reported
  `stored_jumps=0 trace_bytes=0`. One `mount` command once the supply
  recovered restored **all 3 jumps, best 1.285 m, 150,034 trace bytes.
  Nothing was lost.** A dead battery does not cost the session's data.
- **But nobody types `mount` at a beach.** So the retry is now AUTOMATIC:
  while storage is down the main loop retries `try_mount()` every 30 s inside
  the same StoreGuard bracket as boot, silently, and announces
  `# storage RECOVERED automatically` when it succeeds.
- **And the watch now says so.** `fs=down` (already emitted by STATS) is
  consumed by the field, and the header shows **`NO REC`** in place of the
  jump count — deliberately outranking everything else, because a puck in
  this state looks perfectly healthy and saves nothing. Not sticky: the
  firmware can self-heal, and a stale alarm is its own kind of lie.
  46/46 watch tests on epix2 and instinct3solar45mm.
- **Correction to the entry below:** it called this "session-critical" and
  feared data loss. The data was never at risk — the *recording window* was.

### DEATH-RUN AFTERMATH: a brownout reboot leaves the flash UNMOUNTED — session-critical
- **State:** observed on hardware (OG, `0c09863c`, 2026-08-19 ~08:20), recovery
  test pending.
- **What happened:** the OG ran its battery to genuine collapse (34 h, final
  board-read ~2.35 V), was plugged in, and rebooted while the cell was still
  around **2.9 V**. It came up, radio fine, sensor fine — and
  **`SELFTEST flash FAIL detail=mount_failed`**, with `stats` reporting
  `stored_jumps=0 trace_bytes=0` where 3 jumps and 150,374 bytes had been.
- **This is almost certainly NOT data loss.** The QSPI flash chip has a
  minimum operating voltage (~2.7 V class); at ~2.9 V under load the mount can
  fail while the MCU and radio, which run much lower, are perfectly happy. The
  store then reports zeros because it is not mounted — not because it is
  empty. Recovery test: charge, reboot, re-read.
- **WHY IT MATTERS FOR THE WATER SESSION — this is the exact failure
  `docs/plan.md` §3.4 predicted from a different cause:** a puck that browns
  out and restarts mid-session comes back looking *healthy* — BLE up, sensor
  up, watch connected — while silently recording nothing, behind a
  `flash FAIL` row nobody reads at a beach. The plan feared a watchdog reset
  in the boot scan; the real trigger turns out to be simpler and more likely:
  **any restart at low battery.**
- **Mitigations to consider (none built yet):** retry the flash mount once the
  supply recovers rather than only at boot; surface "STORAGE DOWN" on the
  watch and in `STATS` as a first-class alarm instead of a self-test row; and
  refuse to start a session silently when the store is unmounted.


### Advertised battery + state: "puck 78%" WITHOUT connecting — PROVEN
- **State:** proven-on-hardware (spare `55d41d67`, 2026-08-18 ~23:25)
- **The layer-5 UX win from `ble-dependability.md`**, built and verified: the
  puck now broadcasts manufacturer data `[FF FF][batt_pct][flags]` in its scan
  response. A passive scan reads **`JumpHeight-45ED → battery 97 %,
  charging=false`** with **no connection at all**; the OG beside it on old
  firmware shows `mfr=NONE` — a perfect same-air control.
- **Refresh verified:** after a connect/disconnect cycle the payload re-read
  the battery (97 → 99 %, the expected float on a cell-less board), proving
  the re-arm path republishes rather than serving a stale boot value.
- **Design choices worth keeping:** company ID `0xFFFF` is the SIG's
  reserved-for-development ID (inventing a real one would be squatting); the
  payload lives in the SCAN RESPONSE because the primary packet is already 24
  of 31 bytes and a failed advertising start is a dead puck; refresh happens
  at advertising (re)start rather than on a timer, since live updates would
  mean stop/clear/restart and could disturb a connecting client.
- **What it unlocks:** the watch can distinguish *asleep / out of range /
  flat* instead of one ambiguous "no BLE", and show puck battery before
  pairing. Watch-side consumption is the remaining half.

### Flash delivery: the UF2 recipe that actually works, and two traps
- `cp` onto the mounted `XIAO-SENSE` volume fails **permission denied**; `cat >`
  works but silently failed once behind a `;`-chained `echo`; **`dd` is the
  reliable writer** (`dd if=fw.uf2 of=/Volumes/XIAO-SENSE/x.uf2 bs=64k`).
- The software `uf2` command mounts MSC **minutes late or not at all**; the
  physical double-tap mounts it in ~6 s. Gold path: tap → `dd` → board
  self-reboots → verify `src=`.


### Endurance, corrected upward: ≥25.7 h idle, not ~15 h
- Every "~15 h" figure in this project traces to one `batt_pct` extrapolation
  through a region nobody had measured. The death run measured it: **25.7 h
  and still alive**, on a cell whose datasheet capacity (250 mAh) independently
  bounds idle draw at ≤9.7 mA.
- **Session margin restated honestly:** ~2 h session against ≥25.7 h idle and
  18.55 h *including 19 % recording* → **≥9× on the recording-inclusive
  number, ~13× on idle**. Power is not a session risk, now by a wider margin
  and with no retracted figures in the argument.
- **Consequence for the DC/DC prize:** the win is real but smaller than the
  16 mA framing implied. If idle is ~9-10 mA and the MCU is most of it, the
  ~40 % MCU cut lands nearer **7 mA → ~35 h**. Still the largest available
  win; no longer a rescue.
- **Gauge curve bottom is now known conservative:** our table calls 3300 mV
  "0 %" while the cell's own cut-off is 3.0 V ± 0.1 and the PCM disconnects
  there. We reserve ~300 mV of real capacity below our zero — deliberate
  headroom, and worth re-anchoring when the calibration lands.


### BLE bulk export VERIFIED: 240 KB, zero drops, first real queue pressure
- **State:** proven-on-hardware (spare, `fed76059`, 2026-08-18 ~23:00)
- **The induced-failure test from `ble-dependability.md` §5**, which had never
  run: a full `trace` export over BLE — **240,506 bytes, 17,031 valid data
  rows, 61 s at ~3.9 KB/s — with `tx_drops` ABSENT afterwards (i.e. zero)**
  and uptime unbroken.
- **Why it matters:** the layer-1 silent-drop fix (honor `write()`'s return,
  bounded per-connection retry, latched chunk length) shipped as
  `built-unverified` on 2026-08-14 and had never faced real queue pressure.
  A 240 KB dump IS that pressure — the exact condition under which the old
  code discarded chunks silently. Zero drops, and every row well-formed.
- **Also proven incidentally:** BLE is a viable session-download path at
  ~4 KB/s (a 2 h session's trace would take a few minutes), which is the
  sealed-puck fallback if USB ever fails at the beach.

### The cell, from its own listing — and it overturns the idle-current estimate
- **Cell: LP502030 with PCM, 250 mAh typ, JST-PHR-02 (2 mm) pigtail**
  (owner's Amazon listing, 2026-08-18; the Jauch LP502030JH datasheet is the
  same form factor and mostly agrees, but **where they differ the listing
  wins — it is the part actually bought**).
  Nominal 3.7 V · BMS overcharge 4.28 ± 0.05 V · **BMS over-discharge
  3.0 ± 0.1 V** · **max charge current 250 mA (1.0C)** · max discharge
  250 mA constant / 500 mA peak · charge 0–45 °C · 20.5 × 5.3 × 32.0 mm · 5 g.
- **Correction to the Jauch-derived entry:** capacity is **250 mAh typ, not
  260**, and the charge ceiling is **250 mA, not the 0.5C/130 mA** Jauch
  specifies. Both fixes are in the safe direction for us.
- **The capacity cross-check beats the gauge.** The death run has passed
  **25.7 h** and the cell is NOT empty (3493 mV board-read ≈ 3.55 V true,
  well above the 3.0 V cut-off). Even assuming full depletion *now*,
  250 mAh / 25.7 h = **9.7 mA** — an upper bound, since charge remains.
  **The gauge-derived 16.3 mA is therefore impossible**; the retracted 11.6 mA
  walk figure is much closer. Idle draw is **≤10 mA by conservation of
  charge**, the first draw number owing nothing to the percentage curve.
- **Three margins, all comfortable:**
  1. **Deep discharge was safe** — 3.55 V true against a 3.0 V cut-off, with
     a real PCM as backstop. The "protection threshold unverified" caveat is
     retired: it is specified (3.0 ± 0.1 V) and it is fitted.
  2. **Charging is charger-limited, not cell-limited** — 100 mA is 0.40C
     against a 1.0C ceiling. The BQ25101's 50/100 mA選択 is the binding
     constraint; the cell would accept 2.5× more.
  3. **Discharge is trivial** — ~10 mA is 0.04C against 250 mA continuous.
- **For the carrier board (destination state):** the pigtail is **JST-PHR-02,
  2 mm pitch** — the connector the carrier must land, and the reason the
  battery-side plug can be reused rather than resoldered.
### DC/DC regulator: the inductors ARE fitted — the last power lever is real
- **State:** proven-on-hardware (spare, `fed76059`, 2026-08-18 ~22:45)
- **The experiment the gated command was built for.** `dcdc` on the
  sacrificial board: it printed its own verdict — `# dcdc: still alive — the
  hardware supports it` — and **uptime ran straight through** (960 → 974 →
  998 s, no reset). `power-optimisation.md` §2 could only say the inductors
  were "NOT established"; they are established now, on this board family, at
  zero risk to the session board.
- **Stable under DC/DC, not merely alive:** selftest 6/6 PASS rows including
  I2C, sensor, BLE and flash; a full `revive` rail-cycle also clean; uptime
  continuous throughout. The two subsystems most likely to mind a regulator
  change (the sensor rail and the radio) both fine.
- **What it's worth:** the nRF52840's internal DC/DC typically cuts MCU
  current ~40 %, and the MCU is the dominant consumer in our ~16 mA idle.
  This is now the largest *available* power win, and unlike the standby tier
  it is a one-line change to code that already exists.
- **NOT enabled at boot, deliberately.** `DCDCEN` clears on reset, so runtime
  is the safe place to prove it; boot enablement turns a brownout into a boot
  loop. Sequencing unchanged: it earns a place in boot only after the free
  A/B (two matched-window discharge nights) measures the actual saving —
  and that A/B needs the OG, which is currently busy dying.


### 22:31 — the revive-over-BLE reset: CAUSE PROVEN, FIXED, task #18 closed
- **State:** proven-on-hardware by intervention. WDT feeds inside
  `jh_imu::revive()`'s deliberate delays (the 600 ms discharge split into fed
  300s, plus a settle feed; the sequencing physics untouched). Before: 3/3
  resets across two days. After (`fed76059`): **3/3 survived** — uptime ran
  46→92→164 s continuous across three BLE revives, and the full narration
  reached the BLE client for the first time ever.
- **Root cause:** watchdog starvation, the same mechanism as selftest's —
  feed-less handler time plus BLE emit pacing crossing the 3.5 s window,
  while USB stayed just under. The long-BLE-command crash class is now
  closed with both known members fixed the same way.
- **Instrumentation postscript:** both breadcrumb designs (RESETREAS,
  GPREGRET2) proved bootloader-cleared and ended up unnecessary —
  **intervention beat instrumentation.** The persist-based third design is
  cancelled; the SD-aware register code stays as good hygiene.
- **Delivery lessons banked:** serial-DFU wedges under rapid flash cycling
  and reports success without activation; physical double-tap→UF2 is the
  gold path; `cp` fails silently onto the MSC volume where `cat >` works;
  the software `uf2` command mounts MSC minutes late or not at all.


### Late night: the flash-roulette RCA, the crash reproduced, the instrument blind again
- **The evening's dark flashes were DELIVERY, not code.** Unconfounded trial
  at 21:09: UF2-via-double-tap delivered `dac58553` and it booted immediately
  — after the same image had gone "dark" through serial-DFU three times. The
  serial-DFU path wedges under rapid flash cycling and then produces
  transfer-success-without-activation; every boot-guilt verdict issued
  through it tonight was confounded. **UF2 via double-tap is the gold
  delivery path; the drive appearing is the proof the tap was real** (all
  earlier "taps" were single resets — the button is hard to press).
- **revive-over-BLE reproduced on the full-fix build:** BLE revive at 21:12 →
  reset (uptime 98 s → 37 s). The selftest WDT feeds do not cover it;
  `jh_imu::revive()`'s ~1 s feed-less section remains the prime suspect,
  unproven. Bench rule stands: revive over USB only. No session path calls it.
- **The breadcrumb instrument is blind: GPREGRET2 is bootloader-cleared**,
  exactly like RESETREAS. `crumb=` read absent seconds after a confirmed
  crash. Third instrument design queued: a jh_persist key (InternalFS
  survives the bootloader). Same lesson, second register: on this platform,
  nothing in POWER's retained registers survives to the app.
- **Secondary observation:** post-crash the app's USB CDC went silent (three
  probes) while BLE answered perfectly — filed with task #18.
- **Board states at close:** spare healthy on `dac58553` (full batch:
  identity + fast-charge fix + airtime + breadcrumbs) — which UPGRADES the
  OG's endgame target back to the full batch, now silicon-proven. OG at
  3517 mV, hour 24+, still dying.


### Evening: crash-breadcrumb instrument shipped; spare wedged in the dark bootloader
- **State:** instrument built and committed (`7e34154`, build `de849fbb`,
  simtest + both watch targets green); diagnosis of the revive-over-BLE reset
  blocked on a physical power cycle of the spare.
- **The wedge, and the acquittal:** three consecutive flashes to the spare
  came up dark — including a re-flash of the KNOWN-GOOD image that had booted
  on the same board two hours earlier. That acquits the new code and convicts
  the flash/boot path: consistent with the characterized dark-bootloader
  state (retained-RAM double-reset magic) going sticky under repeated rapid
  flashing. The spare has no battery, so unplug/replug is a guaranteed clean
  clear. **Recurrence of the dark bootloader under repeated flashing is
  itself a finding for OTA/flash reliability.**
- **Also in the same batch:** `session_best_airtime_s` on STATS + watch
  reseed (closes the M2 FIT-parse gap; new watch test, 45/45 on epix2 AND
  instinct3solar45mm), and the revive handler stage-stamped for the
  breadcrumb (crumb=1 revive itself / 2 selftest / 3 emitting).


### WATCH M2 CLOSED: jumps rendered on a real wrist (2026-08-18 ~17:00)
- **State:** proven-on-hardware — the milestone the watch effort existed for.
  "The field has never displayed a correct jump on any wrist" is retired.
- **The run:** field sideloaded to the owner's Epix Gen 2 over headless MTP
  (124,252 bytes, size-verified), activity started, watch found and paired a
  puck on its own, showed the puck's battery, **reconciled the 3 stored desk
  tosses on connect** (the layer-3 dropout design working on first contact),
  then rendered **10 live fakejumps one by one** — owner calling out heights
  from his wrist (1.6 ft, 1.8 ft, 2 ft, 1 ft, 1.1 ft...), in feet, per the
  unit preference.
- **The books balance exactly:** session_jumps 13 = 3 real + 10 fake;
  stored_jumps stayed 3 (fakes are display-path only, correctly never
  persisted); session_best stayed 1.285 m (a real toss — no fake beat it).
- **The supporting cast:** the puck serving the watch was the OG at 4 %
  battery, ten hours into its own deliberate death run, over the same link.
  The identity system ran its first live multi-puck session simultaneously:
  `JumpHeight-45ED` (spare) and bare `JumpHeight` (OG) on the air at once,
  the death logger address-pinned to the OG throughout, zero cross-talk.
- **Remaining watch work:** field sizes on the Instinct (task #6), the §9.9
  background-page test, watch self-health surfacing. None gate the session
  the way M2 did.

### FIT recording path VERIFIED on the same activity
- **State:** proven-on-hardware. The activity FIT pulled off the Epix over MTP
  and parsed: developer fields present and correct — `jumps=13` (exact),
  `best_jump=4.216 ft` (exactly the 1.285 m real toss, in the owner's units),
  and a 95-sample `jump_height` record stream. Sessions now land in Garmin's
  own history with jump data embedded.
- **Gap found by the parse:** `best_airtime=0.70 s` (best among live-seen
  fakes) sits beside the reconciled `best_jump=1.285 m` — an inconsistent
  pair. Root cause: STATS carries `stored_best_m` but no best-airtime, so
  reconcile-on-connect can restore one and not the other. Post-water fix:
  add best-airtime to STATS (adder key) and reconcile both.

### HARDWARE DEPRECATION: the ESP32 v1 platform is retired (owner decision, 2026-08-18)
- **What went:** `firmware/src/platform/esp32/` (all six seams + the MPU-6050
  driver), the `firebeetle32` pio envs, the 4 MB partition map, the browser
  flasher (esp-web-tools button, `web/firmware/` binaries, flash manifest).
  Git history keeps everything.
- **What it fixed for free:** the Sense env is now pio's DEFAULT, which
  retargets `./tools/jump flash` — the tool that esptool'd the Sense on
  2026-08-17. `cmd_flash` also gained the two bench lessons of this week:
  a `uf2`-command bootloader-drop retry when the 1200-baud touch misses, and
  a 20 s activation-settle before the post-flash selftest.
- **Also caught in the same sweep:** `jh_power.h` used `uint32_t` without
  `<stdint.h>` — the esp32 env had been silently unbuildable since the
  reset_reason change (nothing in simtest builds that env). Fixed before the
  platform was removed, so the deprecation was a decision, not a surrender.
- **Product surface now:** one board family (XIAO nRF52840 Sense), one flash
  path (USB via `jump flash`, OTA via `otadfu.py`), one BLE stack.

### Spare board soak-gated on the fast-charge-fix build (2026-08-18 afternoon)
- **State:** proven-on-hardware. A spare (owner plugged in one of the two;
  which one is irrelevant to the gate) now runs `src=66b5137b` — the
  fast-charge-fix build — and passed **selftest 5/5 (all rows) and revive
  5/5** over USB. Task #14 closed.
- **Why it matters tonight:** `66b5137b` had never booted on silicon (the OG
  runs its predecessor). The spare pathfound it, so the OG's post-death-run
  flash tonight is a known-booting image, not a first flight.
- **Flash note for the record:** the first upload looked dead — the
  "Activating new firmware" reset raced the port-open probe. A plain retry
  booted clean. Distinct from the stale-zip trap (build was clean); lesson:
  give a fresh flash ~20 s before declaring it dark.
- **Off/wake soak slice deferred:** this spare has no battery, so
  unplug/replug is a cold boot, not a System OFF wake. That slice only means
  something on the battery board, which has ≥20 lifetime off/wake cycles.
- **No-battery fingerprint:** floating divider read "3745 mV/24 %" then
  "4136 mV/97 %" twenty minutes apart — a useful signature for recognising a
  cell-less board from its own telemetry.

### Fast charge — ROOT CAUSE FOUND: the feature was never in any binary
- **State:** cause proven (hichg readback + preprocessor); fix built as
  `src=66b5137b`, clean-built, simtest-clean, **flashes at tonight's recharge**.
- **The smoking gun (08-18 08:56):** first-ever `hichg=` reading during an
  active charge: **`hichg=0 chg=1`** — charging, pin not driven.
- **The bug:** `JH_FAST_CHARGE_ENABLED` was `#define`d in `main.cpp` but
  consumed by `#if JH_FAST_CHARGE_ENABLED` in `jh_power.cpp` — a different
  translation unit that never saw it. Undefined macro in `#if` is 0, so
  `update_charge_current()`'s entire body compiled out of the only file that
  implements it. The function we code-read as "present and called at 1 Hz"
  was present, called — and empty.
- **Why every measurement was right:** all four charge-rate comparisons said
  50 mA because the binary genuinely contained no other behaviour. The
  hardware was never the suspect it appeared to be; Seeed's documentation was
  never contradicted.
- **The fix:** the define moved into `platform/jh_power.h` — the consumer's
  own header — with the lesson written at the definition site: a macro
  consumed across translation units must live in a header both sides include,
  or it is a lie that compiles. (Same family as the watchdog-stub namespace
  shadow: code that compiles cleanly and does nothing.)
- **Verification pending:** after tonight's flash, `hichg=1` during charge +
  a charge-span at ~half the 50 mA baseline closes this for good.


### BLE batch: on silicon since 08-17, effect still unmeasured — two entries in this file disagree
- **State:** bookkeeping entry. Raised because the *BLE silent-drop fix:
  per-connection retry + tx_drops counter* and *BLE per-connection retry —
  chunk length latched* entries below still read `built-unverified` /
  "never flashed — no bench log after 2026-08-14 10:29", and that is no longer
  literally true.
- **What is measurable:** commits `216f75f` (2026-08-14 10:29) and `9277821`
  (2026-08-14 10:46) both precede `src=0c09863c` (flashed and running 08-17)
  and `src=66b5137b` (flashed on the spare 08-18). `src=` is a hash of the
  sources the compiler read, so the retry path and the chunk latch are in both
  binaries. **The code has booted; "never on silicon" is stale.**
- **What is NOT established:** that the fix *works*. Nothing has read back
  `tx_drops` after a loaded transfer, and no bulk export has been run over two
  concurrent centrals. The 08-18 M2 session ran two pucks and one watch with
  "zero cross-talk", but that observation is about advertising identity and
  address pinning, not about the transmit queue.
- **What closes it:** `tx_drops` sampled before/after a full `jumps.csv` +
  `trace.csv` dump over BLE with a second central subscribed. Until that
  measurement exists neither entry should be promoted — this note exists so the
  contradiction is visible rather than resolved by guesswork.

## 2026-08-17

### 2026-08-17 background session: BLE-selftest reset ROOT-CAUSED AND FIXED; two traps found on the way
- **State:** proven-on-hardware (board runs `src=0c09863c`, verified over BLE)
- **The headline:** `selftest` over BLE — 0/2 survival on 08-16 — now passes
  **2/2 with all rows**, on the same board over the same transport. Only
  relevant change: watchdog feeds inside `runSelfTest`'s sampling loops.
  **Root cause confirmed by intervention: watchdog starvation** — the loops
  ran feed-less inside one handler pass, and BLE emit pacing stretched the
  handler past the 3.5 s window; USB stayed just under, which is why the
  transport looked like the variable.
- **Trap 1 — the first instrumented image killed the boot.** Direct
  `NRF_POWER->RESETREAS` access at init: POWER is a SoftDevice-owned
  peripheral. Died before its own banner; board parked in the bootloader.
  Recovered **hands-free** (the bootloader accepts uploads after a bad app —
  a valuable recovery fact in its own right). Fix: SD-aware access.
- **Trap 2 — stale DFU zip.** An incremental build regenerated the ELF but
  not `firmware.zip`, so the "fixed" upload re-flashed the previous dead
  image and the fix looked ineffective. **Rule: clean build before flash, and
  verify `src=` after every flash** — the second half of which is exactly
  what build identity exists for.
- **Open, spun to task #18:** `revive` over BLE still resets (reproduced
  deliberately). Bench rule: revive over USB only. No session path calls it.
- **Instrument finding:** RESETREAS is consumed by the bootloader before the
  app runs — `reas=` reads 0 seconds after a real reset. App-level
  reset-cause telemetry needs GPREGRET breadcrumbs instead.
- **Also live now:** `hichg=` readback (reads 0/released while not charging —
  correct; the informative reading comes on the next real charge cycle).

### Idle endurance — now MEASURED from full, and the method is proven repeatable
- **State:** proven-on-hardware, two independent runs (2026-08-15/16 and 16/17)
- **The repeat, matched window 3961→3751 mV, idle on a desk, same board:**
  night 1 **7.26 h**, night 2 **7.18 h** — **1.1 % agreement.** Span timing
  between fixed voltages is reproducible at the ~1 % level, which converts it
  from a bounds check into a usable instrument.
- **First reference curve from a RESTED-FULL cell (night 2):**

  | to (mV) | hours from full |
  |---|---|
  | 4000 | 2.33 |
  | 3900 | 6.12 |
  | 3800 | 9.22 |
  | 3751 | **10.56** |

  Read: **10.6 h measured from full to ~quarter charge, idle**, ~2 h session
  = **≥5× margin on the measured portion alone.** (The old "≈15.3 h to empty" — now known low, ≥25.7 h measured"
  extrapolation is superseded by this direct measurement; true-empty remains
  unmeasured by design — the 3600 mV floor rule.)
- **The plateau, in our own data:** segment rates ran 35.8 → 30.6 → **18.9**
  → 30.8 → 38.3 mV/h — fast off the top, flat through the middle, steepening
  into the knee. This is the BU-903 lithium curve exactly, measured on our
  cell: the flat 19 mV/h middle is *why* voltage-based percent was retracted.
- **Stability:** zero resets across both runs; night 2 closed a **23.8 h
  continuous run** on the freeze-candidate build (`src=87b0ecaf`).
- **What the 1 % noise floor unlocks:** the free A/B method is real. The DC/DC
  experiment's claimed ~40 % MCU saving would move a matched-window time by
  far more than 1 % — measurable in two nights, no instrument purchase.

## 2026-08-16

### INCIDENT 2026-08-16 ~07:49-08:06: pincensus poisoned the sensor; BLE selftest resets the board
- **State:** board recovered and healthy (USB selftest all-PASS, accel 1.021 g,
  noise 0.0014 g); one root cause measured, one OPEN with instrumentation built.
- **What happened, from the logs:** `pincensus` was run casually over BLE at
  07:48:56 on the live system. It toggles weak pulls across the ACTIVE I2C bus
  and the sensor's own supply pin. Within that minute the motion gate latched
  open and the board recorded **~460 KB of garbage trace while sitting still**
  (flat 142,984 bytes for hours before; growing ~766 B/s after). It kept
  recording for ~11 minutes until the first reboot cleared it.
- **Then three reboots:** two during BLE `selftest` (07:59, 08:02), one during
  BLE `revive` (08:00). The same `selftest` over USB passes 3/3 the same
  morning. Code read ruled out the queue-drain path (feeds WDT), `free_bytes`
  (cached), and the ble row (boolean). **Cause OPEN — do not run `selftest` or
  `revive` over BLE on this board until task #16's instrumented repro.**
- **Why diagnosis was blind:** nobody reads RESETREAS. The nRF52840 records
  watchdog/lockup/soft/pin reset causes in a register; three reboots were
  diagnosed by uptime arithmetic instead.
- **Shipped in src=1c72f10f (built, simtest-clean, NOT yet flashed):**
  `reas=` on INFO (RESETREAS captured+cleared at init), `hichg=` drive
  readback (the firmware half of the fast-charge question), watchdog feeds in
  the selftest sampling loops, and `pincensus` now ends with the audited
  `revive` — a diagnostic that silently poisons the instrument is worse than
  none.
- **Cost:** ~460 KB of the trace region holds noise (region has multi-hour
  headroom; clear per protocol after download). The 3 stored verification
  tosses are intact. Charging was unaffected throughout (BQ25101 is
  independent of the MCU).

### Fast charge — verification FAILING on first measurement (2026-08-16)
- **State:** partial — code confirmed on the board (`src=87b0ecaf`), **effect
  contradicted by measurement**.
- **Evidence:** voltage-span timing under charge, against the 50 mA baseline
  in `data/soaks/20260810-charge-and-stability-soak.csv` (same board, same
  cell): 3890→3970 mV took 29 min at 50 mA; the same span today, with the
  fast-charge build driving HICHG, took **30 min**. In the CC region span
  duration scales inversely with current, so 100 mA would read ~15 min.
  **Ratio ≈ 1.0: the cell is charging at the 50 mA rate.**
- **Third confirmation, full-cycle (09:14):** charge terminated (`chg` 1→0
  between 08:48 and 08:58). Time from the 3775 mV anchor to termination:
  **~122-132 min today vs 140 min in the 50 mA baseline** from the same
  anchor — ratio ~0.9, against ~0.5-0.6 for a working 100 mA CC phase.
  Same verdict by span timing, by matched sub-span, and by full cycle.
- **Free calibration datum from the post-charge tail (10:16):** after >1 h of
  rest off-charge (USB powering the board, cell unloaded), the device's own
  ADC reads a steady **4094-4097 mV — which its gauge maps to 92-93 %.** So a
  genuinely full, rested cell reads ~93 % on this board: the {4160 mV = 100 %}
  top anchor does not match this unit's ADC at full (4095/4160 ≈ 0.984 —
  plausibly the documented ADC gain error). Consequence for every protocol
  that says "charge to 100 %": on this board, full IS ~93 %, and waiting for
  100 % waits forever. Do not retune the curve mid-campaign — note it, and
  fold into the per-unit vbat_scale work if that ever lands.
- **Cause unknown**: firmware drive unconfirmed (no HICHG readback exists;
  `pincensus` releases the drive before reading, so it cannot see it) vs
  board HICHG topology not responding to OUTPUT-LOW as documented. Next:
  `hichg=` STATS readback on next routine flash; USB inline power meter is
  the from-outside arbiter (~70 vs ~120 mA at the port).
- Full reasoning: docs/battery-measurement.md §4 (revised after the
  12-agent adversarial review, workflow wf_6677eb1e-e3d).

### Idle-floor power — RETRACTED as a current figure; see battery-measurement.md
> **2026-08-16: the mA numbers below are not reliable and should not be
> quoted.** They derive from `batt_pct`, a voltage lookup, and the run lived
> entirely in the flat middle of the discharge curve where that method is
> documented not to work. What survives is the *timing*: 3961 mV → 3748 mV in
> 7.51 h, idle. Plan: [battery-measurement.md](battery-measurement.md).

### Idle-floor power — measured (figures retracted, timing retained)
- **State:** proven-on-hardware (OG board, 2026-08-15/16)
- **Method:** `tools/battlog.py` sampled the puck over BLE for 7.5 h. This does
  NOT use the documented "unplug, wait, read `stats`" protocol, because that
  protocol divides by `uptime_s` — time since BOOT, not since unplug — so any
  time on USB inflates the denominator and understates the current. Slopes
  between our own timestamped samples need no such assumption.
- **Result: 71 % → 22 % in 7.51 h, idle, never recording** (`stored_jumps` and
  `trace_bytes` both unchanged throughout; uptime monotonic, no resets).
  ~~**≈15.3 h of idle endurance from full**~~ — **SUPERSEDED 2026-08-18: the
  real figure is ≥25.7 h.** That extrapolation ran through the never-measured
  22 %→0 % region on a discredited curve; the death run walked the region
  instead and was still alive at 25.7 h. Endurance is ~70 % better than this
  entry claimed. Against a 250 mAh nameplate that implies ~16.3 mA.
- **This is WITH the sleep optimisation already on the board** (see the build
  identification below). `docs/power-optimisation.md` predicted 6-7 mA. It is
  not there. The previously-quoted 11.6 mA baseline carries the `uptime_s`
  flaw, so the before/after pair was never valid — the *after* number is the
  trustworthy one, and it is well short of prediction.
- **BLE sampling did not contaminate it.** The schedule included a 3.49 h
  unsampled control gap, which drew 17.21 mA against 13.66 and 17.37 mA in the
  sampled phases — the quiet window was not cheaper, so the cost of sampling is
  below the noise.
- **The gauge cannot measure CHARGE current.** While charging, terminal voltage
  reads high and the percentage curve is calibrated for a rested cell (see the
  4160 mV anchor comment in `jh_power.cpp`): observed 28 % → 39 % in under
  10 minutes, which would imply ~170 mA on a ≤100 mA charger. Use the
  `chg` 1→0 transition and total elapsed time instead.

### Which build the OG is actually running — established by forensics, not by asking
- **State:** proven-on-hardware
- **Method:** the board's `uptime_s` (58,141 s at 06:46:45 on 08-16) dates its
  last flash to **2026-08-15 14:37:44**. Cross-referenced against commit times:
  - `dfdf4cf` sleep between samples — 08-15 **12:35** → **IS on the board**
  - `68f32d1` fast charge 100 mA — 08-15 **14:38** → **is NOT** (by ~1 minute)
- **Consequences:** the OG charges at the 50 mA default, and the idle figure
  above is a post-sleep-optimisation number.
- **This is exactly the work `src=` exists to abolish.** The board reports
  `fw=0.4.3` like every build ever made here, and has no `src=` because it
  predates the change that added one. After the next flash,
  `./tools/jump selftest` answers this in one line.

### Trace cap behaviour past full — first observation, on the host
- **State:** tested-in-sim-or-host (host CSV store, NOT the nRF52 region)
- **Evidence, 400-cycle overnight soak (2026-08-16):** **400/400 boots
  succeeded, zero failures**, 1,600 jumps stored. The trace filled naturally at
  cycle 110 and the run continued for **290 more cycles past full**:
  `trace_bytes` froze at exactly 2,000,333 and never moved again (no overrun,
  no wraparound), while the jump rate stayed at **4.00 per cycle before AND
  after** the cap. **Losing the raw trace does not cost the jump records**,
  which is what the primary deliverable needs.
- **Boot time did not degrade:** 0.3181 s mean over cycles 1-50 versus 0.3189 s
  over cycles 301-400, with jumps.csv grown to 1,600 records. **This does NOT
  predict the nRF52.** The host store resumes from file size — an O(1) stat —
  whereas the nRF52 walks the region block by block, so flat boot time here is
  expected by construction and says nothing about the real scan.
- **Explicit limit:** `platform/host/jh_store.cpp` is CSV, deliberately not the
  nRF52 binary region, so the block-walking append-point scan is still
  unexercised. Only silicon closes that.

## 2026-08-15

### Detector thresholds vs REAL motion (E7/E8) — a recommendation, not yet a change
- **State:** tested-in-sim-or-host. `config/params.json` is UNCHANGED.
- **The shipped configuration keeps a false positive.** Across all 638,852
  samples of the 2026-08-15 recording it returns 10 events: the 9 real ones,
  plus one at t=4650.087 whose median airborne |a| is **1.393 g**. Free fall
  reads ~0 g by definition, so that is not a jump. 3,035 of 6,174 swept
  combinations do strictly better.
- **A robust alternative exists:** `freefall_enter_g` 0.35 → **0.26**,
  `min_airtime_s` 0.25 → **0.30** (confirm unchanged at 0.08). Correct in
  **12 of 12** perturbed worlds — noise to 0.05 g, ±5 % gain, ±0.02 g offset,
  ±0.5 % clock, and two combined worst cases — where the shipped point is
  correct in **0 of 12**. 63 of 120 tested points survive all 12, so this is a
  broad region, not a needle.
- **This is a PRECISION problem, not a sensitivity one.** The shipped settings
  never miss a real jump: 9/9 in every world, including 0.05 g of added noise.
  The change buys false-positive rejection and rescues nothing.
- **Why it has not been applied:** the evidence is one recording, on land, in a
  pocket. E8 perturbs the *sensor*, not the *motion* — it cannot speak to a
  rigid mount, or to a foil jump being longer and smoother than a hand toss.
  Changing detector gates before a freeze, on land-only evidence, is the
  owner's call.

### Silent-corruption rate of the trace codec — measured
- **State:** tested-in-sim-or-host (7 tests, `tools/tests/test_codec_fuzz.py`)
- **Numbers:** single-bit corruption slips through **0 / 4000** (guaranteed by
  CRC algebra, so this is arithmetic rather than evidence); **random
  multi-byte corruption slips through 8 / 6000 = 0.13 %**, i.e. roughly **1
  corrupted block in 770** would decode as plausible data. Better than the
  ~0.39 % textbook figure for an 8-bit check, because most random damage also
  breaks the block's count field and is rejected structurally first.
- Also asserted: `decode_region()` never raises on 3,000 random blobs,
  truncation never increases the sample count, and a lost chunk never
  retroactively alters earlier samples.


### Circular ground truth — FIXED, and enforced in code
- **State:** tested-in-sim-or-host (2 new regression tests, 111 passing)
- **What was wrong:** `docs/data-pipeline.md` derived "true height" from counted
  airborne frames via `h = g·T²/8` — the formula the firmware uses. Scoring our
  `g·T²/8` against a label built from `g·T²/8` measures timing agreement and
  nothing else. It would have produced a small, confident RMSE **whether or not
  wings are ballistic**, which is the entire question the water session exists
  to answer. This was P0 item 4 in `docs/plan.md`.
- **The fix is mechanical, not just editorial.** `labels.csv` gains a
  `height_src` column and `sim/evaluate.py` refuses to compute RMSE from
  anything that is not independent (`INDEPENDENT_SRC = {ruler, sim}`).
  Detection is still scored; only the *height* is barred. Blank provenance
  defaults to inadmissible — assuming the friendly reading is exactly how a
  circular number becomes a published accuracy claim. When it excludes rows it
  prints why, so a missing RMSE cannot be misread as "you forgot to label".
- **The procedure it replaces it with:** measure apex against **rider height in
  gear** (~2× the mast, high-contrast against sky, vertical at apex), with the
  **board's own position at takeoff as zero — not the horizon**. A camera 0.8 m
  above the water sees the water plane 0.8 m below its level line at *every*
  distance, so a horizon zero adds **+53 % on a 1.5 m jump**, as a fixed bias
  that never shows up as scatter. Film **1080p/120, not 4K/30**: 30 fps
  quantises a 1 s flight to ±8 cm ≈ 6.7 %, the size of the effect under test.
- **The strongest version, now written down:** with a known ruler and known
  frame rate, fit the flight and recover `g_eff` directly from video — the same
  quantity the accelerometer measures, from an independent instrument. Sim says
  wings are 1.0–1.07× ballistic; a kite is 2.3×. Those are not close.
- **Insurance:** the primary result (median airborne |a| and |ω| per jump) comes
  from the trace alone. If the filming goes badly the session still answers its
  question.

### Build identity on INFO — the freeze protocol is now checkable
- **State:** built-unverified on the nRF52 board (not yet flashed); **proven
  on the host build**, which compiles the same `src/main.cpp`.
- **Evidence:** the natively-compiled core prints
  `# JumpHeight fw v0.4.3 src=03189592` and `INFO … src=03189592`, and
  `python3 tools/gen_build.py --print` on the same tree returns `03189592`.
  `./tools/jump selftest` against the fake device correctly reports the
  mismatch case.
- **What it replaces:** `FW_VERSION` has read `0.4.3` through every fix this
  project has ever shipped, including the build whose GPIO drive strength made
  healthy sensors look dead for four days. There was no way to ask a board
  which firmware it was running.
- **Why a source hash and not a git sha** (the design decision worth keeping):
  a sha fails twice. It is *self-invalidating* — writing HEAD into a tracked
  header changes the tree, producing a new commit whose sha the header no
  longer holds — and it *lies on a dirty tree*, because the compiler reads the
  working tree while the sha names a commit. Catching exactly that is the
  freeze protocol's job. So the identity is a hash of the bytes the compiler
  reads: deterministic, order-independent, CRLF-normalised, and it excludes
  its own output file so a fixed point exists.
- **Where it shows up:** boot banner, BLE subscribe banner, `INFO src=`,
  `session-info.txt` (`build_src=`) so recorded data carries the build that
  produced it, and a `simtest` row that fails if the header goes stale.
  `./tools/jump flash` regenerates it via `cmd_gen` before building.

### Offline detector vs the device, on REAL recorded motion — first ever
- **State:** proven-on-hardware
- **Evidence:** 2026-08-15. The 638,852-sample walk trace replayed through
  `sim/detector.py` found **exactly the same 10 jumps**, same order, same
  times, as the device found live. Agreement: **max airtime difference
  14.7 ms** (one 50 Hz sample is 20 ms), **mean height difference 1.82 %**,
  max 5.07 %.
- **Why it matters:** C++/Python parity had only ever been checked on
  synthetic data. This is the first proof on real motion, and it validates
  the premise the whole trace exists for — that a session can be re-analysed
  and re-tuned offline. The residual difference is explained: the device
  detects at 200 Hz, the stored trace is 4:1 decimated to 50 Hz, so offline
  takeoff/landing quantise to 20 ms instead of 5 ms.
- **Consequence for the water session:** the DEVICE's numbers are primary;
  offline re-analysis is for tuning, and carries ~2 % height spread.

### Labelling: `tools/label.py`, and an honest limit on it
- **State:** tested-in-sim-or-host
- **Evidence:** converts human wall-clock notes into the `labels.csv` schema
  `sim/evaluate.py` has always expected and never had, using the
  `trace_epoch_utc` anchor. Verified round-trip on a real session.
- **The limit, measured:** the scorer matches within `MATCH_WINDOW_S = 1.0 s`.
  A time written down by hand is typically tens of seconds out (the demo was
  28 s off). So **`none` regions are the valuable output** — they give a
  false-positive rate, and coarse timing is fine for that. **`jump` rows will
  not reliably match**; per-jump accuracy timing has to come from video. The
  tool now prints this rather than emitting labels that silently never match.

### Sleep-between-samples — shipped, jitter falsifier PASSED
- **State:** proven-on-hardware
- **Evidence:** 2026-08-15, commit dfdf4cf. Post-change desk test: 3/3 tosses
  detected and stored with physics columns. Sample cadence measured from the
  trace: median/mean/p99 all **20.000 ms**, max 21.000, **zero** deltas more
  than 2 ms off cadence.
- **Honest caveat:** that run is 3,900 samples against the 638,655-sample
  baseline. The baseline's outlier rate was 0.004%, so ~0.15 outliers would be
  expected at this size — seeing zero is **no degradation detected**, not
  evidence of improvement. The walk is the real comparison.
- **Gap:** the actual power saving is UNMEASURED. That is the idle-floor run.

### Wall-clock anchor — round-tripped on real data
- **State:** proven-on-hardware
- **Evidence:** `session.json` from the jitter-check sync gave
  `trace_epoch_utc = 18:37:40`; the first toss at trace t=15647.230 converts
  to **18:58:28 local**, which is when the tosses were actually thrown, ~2
  minutes before the 19:00 sync. A recorded jump now has a real-world
  timestamp — the thing that made video alignment and labelling impossible.

### Fast charge (100 mA while charging)
- **State:** built-unverified
- **Evidence:** commit 68f32d1, selftest PASS with it live and `chg=1`.
- **Gap:** the effect is unmeasured — it went live near the top of a charge.
  The honest test is the next charge from a low starting point.

### Session-scale USB download — PROVEN at 2x a real water session
- **State:** proven-on-hardware
- **Evidence:** 2026-08-15, THREE-WAY agreement. Device reported
  `trace_bytes=9872675`; two independent downloads each produced
  **9,872,675 bytes** and are **byte-identical to each other**. 638,853
  lines, clean final line, **zero** `INCOMPLETE` warnings. That is ~2x the
  ~5 MB a real session produces and **49x** the largest download this project
  had ever done (201 kB). Yesterday the same two-read test on a 36 kB file
  produced two DIFFERENT files.
  (Bonus: pull-b matched pull-a in size, i.e. the motion gate correctly
  recorded nothing while the board sat still between reads.)
- **Gap:** none for USB. BLE bulk export at this scale is still unmeasured.

### Battery endurance — MEASURED, and the plan's figure was 4x optimistic
- **State:** proven-on-hardware
- **Evidence:** 2026-08-15 accidental overnight run. 93% -> 7% over 18.55 h
  of wall clock (span of the recorded trace) = 215 mAh, i.e. **11.6 mA
  average, ~21.6 h from full**. `docs/plan.md` had assumed ~4 mA / ~60 h from
  a paper estimate.
- **Consequence:** a 2 h session is still comfortable (~10% of the cell), but
  the margin is 8x, not 30x. Charging the night before is mandatory, and
  leaving it running overnight flattens it.

### 18.5 h continuous run with ZERO resets
- **State:** proven-on-hardware
- **Evidence:** the 638k-sample trace contains **0 timebase restarts** —
  trace time never went backwards, so the board did not reset once in 18.5 h
  of untethered running, including 3.55 h of active recording.
- **Why it matters:** the watchdog, the storage append path and the bounded
  I2C driver all ran unattended for a day without a single reboot.

### Motion gate duty cycle
- **State:** proven-on-hardware
- **Evidence:** 3.55 h of recorded motion inside an 18.55 h window = **19%
  duty cycle** — a pocket/desk day. Storage sizing should use recorded time,
  not elapsed time.

### Per-jump flight physics — first real values, and a false-positive discriminator
- **State:** proven-on-hardware
- **Evidence:** 10 jumps recorded with the new columns. Nine read
  `med_a` 0.039-0.154 g (median 0.079 g) against the sim's predicted
  0-0.070 g ballistic band. Jump 7 reads **1.393 g** over 44 samples with a
  plausible-looking 0.33 s / 0.13 m — i.e. a false positive that NOTHING
  else in the record distinguishes.
- **Why it matters:** `med_a` is a physical discriminator between a real
  flight (weightless) and a jostle (not). It answers "will chop trigger false
  jumps?" offline, and could later filter in firmware.
- **Gap:** these are hand tosses, not foil jumps, and there is **no zero
  calibration** — the instrument's own free-fall floor is unmeasured, so
  0.079 g cannot yet be split into real signal vs sensor offset. A 10-minute
  drop calibration fixes that and is now the highest-value pre-water task.

## 2026-08-14

These landed after the audit above was generated. They are listed here rather
than merged in, so the provenance stays honest: the audit is a snapshot, this
is the delta since.

### Watch: error-boundary hardening, FIT summary guard, STATS completeness
- **State:** tested-in-sim-or-host
- **Evidence:** commit f2861ee. Both device targets BUILD SUCCESSFUL;
  **44/44 unit tests PASS** in the Connect IQ simulator (epix2), including a
  new `testCorrupt_truncatedStatsIsRejected`.
- **Gap:** not yet sideloaded. The point of the catch change is behaviour on
  an unproven device, which by definition the simulator cannot show.

### Garmin toolchain runnable on this Mac
- **State:** proven-on-hardware (of the toolchain)
- **Evidence:** `brew install openjdk` — the SDK tools could not run at all
  before ("Unable to locate a Java Runtime"). Full recipe in
  `garmin/README.md`, key at `~/.garmin-ciq/developer_key.der`.
- **Gap:** none. This unblocks every future watch change being compiled and
  tested rather than eyeballed.

### END-TO-END: a jump detected on real motion and read back from flash — GATE CLOSED
- **State:** proven-on-hardware
- **Evidence:** `./tools/jump desktest` PASS on the OG board, 2026-08-14,
  three untethered tosses with the cable OUT, running on battery alone.
  Read back off the device afterwards:
  ```
  n,takeoff_s,airtime_raw_s,airtime_s,height_m
  1,13307.017,0.250,0.276,0.093
  2,13314.731,0.415,0.441,0.238
  3,13318.231,0.271,0.296,0.108
  ```
  `stats` then reported `stored_jumps=3 stored_best_m=0.238`.
- **Why it matters:** this is the FIRST jump detected on silicon on any
  build since 2026-08-11, and the first proof of end-to-end persistence
  since the storage path, the IMU rail drive and `begin()` all changed. It
  simultaneously proves battery-only operation, the detector on real motion,
  and that a jump survives to flash — the three things the self-test cannot
  show. The `airtime_offset_s = 0.0257` calibration is visibly applied
  (raw 0.250 → corrected 0.276).
- **Gap:** flight 1 landed at 0.250 s, exactly the `min_airtime_s` floor —
  these were gentle tosses, so the detector's LOW edge is now exercised but
  its behaviour on 1 s+ airtimes is still only simulated.

### USB session download — WAS LOSSY, now fixed and verified
- **State:** proven-on-hardware
- **Evidence:** commit b7c3644. BEFORE: two downloads of the same stored trace
  diverged at line 1653 — read A lost 0.58 s of samples, read B lost 0.42 s
  elsewhere, both truncated the final line. AFTER: two consecutive downloads
  byte-identical over the shared prefix, 6027 lines each, clean tail, no
  warning. Root cause was `emitBytes` dropping a whole block when
  `Serial.availableForWrite()` was momentarily short.
- **Gap:** verified at ~36 KB / 6k lines. A full session is ~5 MB, so the
  2 h walk-with-the-puck test is still owed before the water.

### OG board on the current build
- **State:** proven-on-hardware
- **Evidence:** 2026-08-14 — `SELFTEST END result=PASS`, all rows: i2c 0x68,
  whoami PASS 0x6A, accel 1.000 g, noise 0.0012 g, ble advertising, flash
  2080004B free. Running on battery + USB.
- **Gap:** no jump has yet been detected on this build — the desk test.

### Boot-scan watchdog feeds (jumps + trace append-point scans)
- **State:** built-unverified
- **Evidence:** `jh_store.cpp:311` and `:398`, commit 0ab78d3
- **Gap:** never exercised at a realistic fill level. The failure it prevents
  (boot reset → StoreGuard latch → a whole session recorded storage-less)
  only appears on a well-used region, which no board has ever had. The 2 h
  walk-with-the-puck test would create one.

### Self-arming spin correction — GATED OFF
- **State:** not-built (deliberately disabled)
- **Evidence:** `main.cpp` `JH_SPIN_SELFARM_ENABLED 0`, commit 0ab78d3
- **Gap:** re-enable only with a persistence key, a wire field, and water data
  to validate against. It alters which jumps are *detected*, not just their
  reported height, so it is not reversible offline.

### `jump status` staleness gate
- **State:** proven-on-hardware (of a sort — it caught this very commit)
- **Evidence:** `tools/jump` cmd_status; fired correctly against commit 0ab78d3
  with "STATUS.md is 0.4 h OLDER than the newest code change"
- **Gap:** USB-only, so unusable at the beach on a sealed case; does not print
  STATS (`batt_pct`/`stored_jumps`/`trace_bytes`), which are the pre-launch
  numbers that matter.

### `jump sync` trace-cap warning — DISABLED
- **State:** not-built (deliberately)
- **Evidence:** `tools/jump` `trace_capped = False`, commit 0ab78d3
- **Gap:** the honest check needs the device's own `trace_bytes` carried
  through the download. Until then it claims nothing rather than crying wolf.

### BLE per-connection retry — chunk length latched
- **State:** built-unverified
- **Evidence:** `jh_link.cpp` `s_chunk_n`, commit 9277821
- **Gap:** never on silicon. The latch fixes a regression the first version
  introduced (recomputing the chunk while a retry was pending could drop bytes
  on a second connection).

---

## PROVEN ON HARDWARE  (44)

### BLE ByteArray→String ingest decode
- **Evidence:** PuckLink.mc:376-379 (convertEncodedString). Pre-fix failure captured in the watch's own log: scratchpad CIQ_LOG.YML, 2026-08-11T01:58:27Z, 'Unexpected Type Error' at PuckLink.mc:357 _ingest. Post-fix the DIAG build read real wire text off the link ("height_m=1.623m=1.658", commit d5d6a26).
- **Gap:** None.

### BLE link bring-up on the watch: profile register → scan → match → pair → discover → subscribe → notify
- **Evidence:** Epix Gen 2 (part 006-B3943-00, CIQ 6.0.2), 2026-08-11, commit 4121619; /Users/joshcrow/Jump-height/garmin/FIRST_COMPILE.md:66-108. Independently corroborated by the watch's own crash log (scratchpad CIQ_LOG_2.YML) whose stack frame is PuckLink.mc:222 onCharacteristicChanged — notifications were genuinely being delivered to the field.
- **Gap:** Never run on Instinct 3 Solar (any state). No soak, no puck power-cycle, no walk-out-of-range: /Users/joshcrow/Jump-height/garmin/README.md:186-204 M2 checklist is entirely unticked.

### BLE link: NUS advertising, connect, command round-trip
- **Evidence:** firmware/src/platform/nrf52/jh_link.cpp:403 begin(), :446-472 advertising setup; Bluefy connected and ran selftest over BLE 2026-07-31 (SENSE_FIRST_BOOT.md:466-472); Garmin Epix Gen 2 + tools/blecmd.py subscribed concurrently for >1 h 2026-08-11

### Battery telemetry (vbat_mv / batt_pct / chg)
- **Evidence:** firmware/src/platform/nrf52/jh_power.cpp:109 vbat_mv(), :117 vbat_mv_tacq(), :222 batt_pct(), :237 charging(); TACQ sweep on silicon 2026-08-11 (3us 4044 / 5us 4056 / 10us 4077 / 15us 4082 / 20us 4082 / 40us 4085 mV), production read 4035-4044 -> 4079-4082 mV, batt_pct 86-88 -> 91 (SENSE_FIRST_BOOT.md:1337-1343); chg=1 observed while charging 2026-08-10
- **Gap:** A 1.88% per-unit gain residual vs the meter is uncorrected on both boards; `set vbat_scale` has never been applied to any unit; batt_pct has never been tracked over a full discharge.

### Battery telemetry + SAADC acquisition-time calibration
- **Evidence:** firmware/src/platform/nrf52/jh_power.cpp; meter points 3490/3390mV and 4160/4050mV; TACQ sweep 3us=4044 -> 15us=4082mV (SENSE_FIRST_BOOT.md:1370-1377); fix commit 10d26a5
- **Gap:** ~75mV / 1.8% per-unit gain error uncorrected and unconfirmed on a second unit

### Bench commands `gyro`, `vbatscan`, `fakejump`
- **Evidence:** firmware/src/main.cpp:773 gyro (produced the 3.1 -> 0.5 dps and 257.8 dps peak readings 2026-08-11), :811 vbatscan (produced the six-point TACQ sweep 2026-08-11), :757 fakejump (drove the whole watch pipeline against a sensor-dead puck 2026-08-12, docs/rca-sense-imu-2026-08-11.md:125)

### Bench diagnostics: `pincensus` and `i2cdiag`
- **Evidence:** firmware/src/main.cpp:512/531, firmware/src/platform/nrf52/jh_imu.cpp:315 pin_census()/:251 bus_rail_sweep()/:361 bus_diag_twim()/:376 bus_diag_wire(); their output is the measurement that closed the drive-strength RCA on 2026-08-14 (SENSE_FIRST_BOOT.md:962-965, 983-987)
- **Gap:** `pincensus` is missing from printHelp (main.cpp:424-427), so the project's designated first diagnostic is undiscoverable from the device.

### Binary trace v2 codec (encode on device, CSV on the wire)
- **Evidence:** firmware/include/trace_codec.h; tools/tests/test_trace_codec.py + firmware/test/trace_codec_harness.cpp parity (65 tests passed with siblings 2026-08-14); byte-identical readback on silicon 2026-07-31
- **Gap:** Multi-hour capacity (~5 h claim) and boot-scan time at high fill never measured on the chip (items 11/12).

### Bounded I2C driver (TwimBounded) replacing unbounded Wire
- **Evidence:** firmware/src/platform/nrf52/twim_bounded.h:194 await(); commit 3607811 (2026-08-12) 'PROVEN on the mule's held bus 3/3'; the post-review hardening (7a2061b: error decode after STOPPED, AMOUNT cross-checks, iteration caps, quiesce on every exit) was the driver running in the 2026-08-14 5/5 selftest soaks
- **Gap:** No bench reproduction of a mid-session bus wedge under the hardened driver; the raced-ERROR-after-STOPPED path has never been observed firing.

### Bounded TWIM driver (a held I2C bus is a 2ms timeout, not a watchdog reset)
- **Evidence:** firmware/src/platform/nrf52/twim_bounded.h; commit 3607811 "PROVEN on the mule's held bus 3/3"; hardening commit 7a2061b
- **Gap:** none noted

### Cable flashing: UF2 drag-drop and serial DFU
- **Evidence:** 2026-07-31: 1200-baud stty touch -> XIAO-SENSE drive -> cp .uf2 -> reboot; second attempt's automount failed and `pio run -t upload` (adafruit-nrfutil) programmed it in ~23 s (SENSE_FIRST_BOOT.md:491-501)
- **Gap:** macOS automount is unreliable on repeat; serial DFU is the scripted path.

### Crash guards: ProbeGuard (sensor) and StoreGuard (QSPI mount)
- **Evidence:** firmware/src/platform/nrf52/jh_imu.cpp:461-476 and firmware/src/main.cpp:878-885; commit 3a4b145 'proven on the wedged board itself'; StoreGuard build booted and advertised 60 s continuously on the mule with no self-reboot 2026-08-12 (SENSE_FIRST_BOOT.md:692-693)
- **Gap:** Guard bits live in the same jh_persist record as calibration; a corrupt record loses both.

### Display-unit resolution on device (properties read + auto/statute)
- **Evidence:** Both FIT files written by the watch carry units string 'ft' on jump_height/best_jump. That string comes from UnitsFmt.unitLabel(UnitsFmt.isFeet(_readUnitOverride())) at JumpFieldView.mc:59-63, so Application.Properties.getValue and System.getDeviceSettings both resolved correctly on the real Epix.
- **Gap:** UNIT_FT/UNIT_M explicit overrides untested on device (unreachable on a sideload — no settings channel).

### Drop calibration measured on silicon (airtime_offset_s)
- **Evidence:** commit a6e477d (2026-08-11): 10 drops from 123.19 cm, median error -25.7 ms, stdev 14.5 ms → airtime_offset_s = 0.0257, now in config/params.json:10 and firmware/include/params.gen.h. Measured through the web app's phone bench flow, on the sealed OG board.
- **Gap:** None for airtime_offset_s. height_scale is still 1.0 and needs on-water video.

### Drop calibration on the Sense (airtime_offset_s)
- **Evidence:** commit a6e477d 2026-08-11; 10 drops from 123.19cm, median error -25.7ms, stdev 14.5ms; config/params.json:11 airtime_offset_s=0.0257
- **Gap:** measured on the OG board only; not re-run per-unit on board #3 (power-states.md:288 flags this)

### FIT developer fields declared and SESSION values written into a real saved activity
- **Evidence:** Two FIT files pulled off the Epix Gen 2 on 2026-08-11 (/private/tmp/claude-501/-Users-joshcrow-Jump-height/a57fec5c-c76c-440f-8536-9f8067f25d7e/scratchpad/2026-08-11-17-43-13.fit and 2026-08-11-18-02-00.fit). I decoded them: field_description records jump_height(RECORD, units 'ft'), jumps(SESSION,'count'), best_jump(SESSION,'ft'), best_airtime(SESSION,'s') — exactly FitOut.mc:46-60's table — and the 18:02 activity carries one SESSION message with jumps=0, best_jump=0.0, best_airtime=0.0, in a Windsurf activity.
- **Gap:** Every RECORD jump_height in both files is NaN — no jump value has ever reached a FIT. Garmin Connect rendering (M4 AC, docs/garmin-datafield.md:294-296) never checked. Note the 17:43 file declares only jump_height and no session fields: that is the activity the field crashed in 58 s after start (CIQ_LOG_2.YML, 2026-08-11T21:44:11Z).

### Gyro read on silicon (LSM6DS3TR-C, +/-2000 dps)
- **Evidence:** firmware/src/platform/nrf52/lsm6ds3_min.h:186 readGyroDps(); measured 2026-08-11 via the `gyro` command: rest |w| = 3.1 dps, hand-rotation peak 257.8 dps, return to rest (firmware/SENSE_FIRST_BOOT.md:1565-1573)
- **Gap:** rot_g vs w^2*r at a known radius never measured; whether real spins rail the +/-16 g accel is unknown (item 26 step 2).

### IMU begin() one bounded retry
- **Evidence:** firmware/src/platform/nrf52/jh_imu.cpp:479-491 (commit 0e2345b, 2026-08-14); mule went from 2 `config FAIL write_error` in ~14 revives to 12/12 clean (SENSE_FIRST_BOOT.md:1062-1070)

### Jump detection state machine (airtime method)
- **Evidence:** firmware/include/jump_detector.h:138 update(); 61 session jumps recorded on the Sense 2026-08-11, best 1.495 m, firmware a6e477d (docs/rca-sense-imu-2026-08-11.md:15); C++/Python parity `./tools/jump simtest` PASS re-run 2026-08-14 (C++ 4 jumps vs Python 4)
- **Gap:** No jump has been detected on silicon on ANY build since 2026-08-11 — i.e. never on the current build, which changed the IMU rail drive, the store watchdog feed and begin() retry. The 3-toss desk test (docs/plan.md:121) is the unrun gate.

### Model corruption gate (_jumpIsCorrupt + STATS mirror)
- **Evidence:** Model.mc:180-256; 6 dedicated tests testCorrupt_* in ModelTest.mc, all PASS in today's 43/43 run. Field-validated on Epix Gen 2 2026-08-11 with the DIAG build: 3 corrupt lines rejected, count held at 0 while the puck counted 12 (commit d5d6a26). Independently corroborated by the saved FIT: SESSION jumps=0 (scratchpad 2026-08-11-18-02-00.fit).
- **Gap:** It has never had to pass a GOOD line on hardware — every line it has seen on a wrist was corrupt. Zero true positives on-device. Its first version crashed the field on-device (nested-scope helper call, CIQ_LOG_2.YML at PuckLink.mc:357, fixed in d5d6a26).

### Motion gate (record only while moving)
- **Evidence:** firmware/src/main.cpp:951-962; 2026-07-31 bench: gate enter/exit verified exact, idle fired at trip +20.0 s; a quiet minute reads trace_bytes=0 (SENSE_FIRST_BOOT.md:1296-1312)

### OTA DFU (BLEDfu service + `dfu` command + tools/otadfu.py)
- **Evidence:** firmware/src/platform/nrf52/jh_link.cpp:441 BLEDfu, :586 reboot_to_dfu() (GPREGRET 0xA8 via sd_power_gpregret_set); gate passed 2026-08-12 ~15:00 — two back-to-back loops, 113 s + 114 s, every 10 KB checkpoint verified, validate+activate clean, calibration intact after both (SENSE_FIRST_BOOT.md:583-592); bootloader 0.6.1 -> 0.11.0 flashed over the air (commit ec4f403)
- **Gap:** Still open: one USB-out human-unplug run, one nRF Connect phone run, and the dark-bootloader-after-failed-transfer timeout. `dfu` is unauthenticated — anyone in radio range can reboot the puck into a USB-less bootloader (docs/plan.md:219-222).

### On-glass render, full tier, Epix Gen 2 (chord math, transparent text cells, digits/unit split)
- **Evidence:** Epix Gen 2 2026-08-11: the field rendered '0.0 ft' and then real digits on the wrist (FIRST_COMPILE.md:66-70, commit 4121619). Simulator epix2 renders of the same fixes are in the scratchpad (crop-final.png = SEARCHING full tier with header, dot and intact 'g' descender; crop-ab.png = the drawText background-erasure bug; crop-c.png = the three-ways-in-one-frame experiment).
- **Gap:** CONNECTED has only ever rendered untrustworthy numbers on the wrist. No render at all on Instinct.

### Power-on self-test (i2c/whoami/accel/noise/ble/flash)
- **Evidence:** firmware/src/main.cpp:267 runSelfTest(); `SELFTEST END result=PASS` on BOTH boards 2026-08-14 (SENSE_FIRST_BOOT.md:1087); cold-boot first-conversion discard (commit dca2985, main.cpp:323-328) held on board #3's first cold boot: noise PASS 0.0028 g
- **Gap:** The `ble` row reports only whether jh_link::begin() returned true at boot — it is never re-evaluated, so a radio that dies later still reads PASS.

### QSPI store mount ladder (begin -> DPD wake -> JEDEC 66/99)
- **Evidence:** firmware/src/platform/nrf52/jh_store.cpp:612 mountLadder()/655 init(); `flash PASS 2093056B_free` on both boards 2026-08-14 (docs/bench-playbook.md:13-14); the DPD wake-retry path proven on silicon 2026-07-31 (SENSE_FIRST_BOOT.md:228-254)

### Radio cold-start from compute() (not onStart/getInitialView)
- **Evidence:** /Users/joshcrow/Jump-height/garmin/jumpfield/source/JumpFieldView.mc:89-94 + JumpFieldApp.mc:33-51; Epix Gen 2 2026-08-10 — onStart() runs before getInitialView() (proven by println), and starting BLE inside getInitialView() hung the field on the CIQ splash (FIRST_COMPILE.md:81-91, commit 4121619).
- **Gap:** None outstanding on Epix; unverified on Instinct.

### Sensor power rail at high GPIO drive (H0H1)
- **Evidence:** firmware/src/platform/nrf52/jh_imu.cpp:106-111 nrf_gpio_cfg(..., NRF_GPIO_PIN_H0H1, ...); same-board sweep 2026-08-14: `en=HIGH pin=0 ... twim: TIMEOUT` vs `en=HIGH-HIDRIVE pin=1 ... twim6A: OK` (SENSE_FIRST_BOOT.md:962-965); board #3 accel PASS 1.022 g, mule accel PASS 1.029 g after the change
- **Gap:** None outstanding for the mechanism; corroborated by Seeed schematic v1.1 sheet 2 (DECISIONS #37).

### Sensor rail high-drive fix (root cause of the 4-day "dead hardware" crisis)
- **Evidence:** commit 859ad42; sweep en=HIGH pin=0 twim TIMEOUT vs en=HIGH-HIDRIVE pin=1 twim6A OK (SENSE_FIRST_BOOT.md:962-965); board #3 accel PASS 1.022g, mule accel PASS 1.029g; 5/5 selftest + 12/12 revive (SENSE_FIRST_BOOT.md:1059-1070)
- **Gap:** none for the fix; three docs still publish the refuted dead-hardware verdict

### Sideload/install to the watch (headless MTP)
- **Evidence:** Commit 370080a, 2026-08-10: JumpField.prg, 116588 bytes, verified in GARMIN/Apps on the Epix Gen 2. mtp-sendfile fails on Garmin; a ~30-line libmtp sender was required (scratchpad mtpsend.c + built binary; GarminDevice.xml pulled from the device is there too).
- **Gap:** Never performed for the Instinct 3 Solar. Adding the field to a sport's data screens is still a manual step that silently sinks US1.

### Two BLE centrals (watch + Mac) served simultaneously
- **Evidence:** Commit b278047, 2026-08-10: the Garmin field and tools/blecmd.py subscribed together for over an hour, both served, killing one left the other up.
- **Gap:** This is also the configuration that produced corrupt values on the wrist — and per docs/ble-dependability.md:36-39 a single central under load reaches the same path, so 'use one central' is a workaround, not a fix.

### Watchdog (nRF52840 WDT, ~3.5 s, watchdog-first boot)
- **Evidence:** firmware/src/platform/nrf52/jh_link.cpp:378-394 wdtInit/wdtFeed, armed first in setup() (main.cpp:858); observed firing at ~3.5 s on a hung selftest, deterministically 5/5 (SENSE_FIRST_BOOT.md:730-733) and resetting a format at sector ~96 before bd0334d
- **Gap:** Never watched across a long normal session on the current build to confirm it does NOT fire spuriously; jh_store::init() still runs partly outside a fresh budget.

### Web app — BLE/Serial connect + live stats
- **Evidence:** firmware/SENSE_FIRST_BOOT.md:466-472 — 2026-07-31, Bluefy on iPhone connected and 'the web app rendered the full INFO readout — firmware version, 200 Hz, params, calibration'. Sim coverage: tools/tests/test_web.py::test_live_jumps_update_the_dom + test_selftest_block_renders_result_rows (Playwright vs MockTransport, 10/10 pass in 1.99 s).
- **Gap:** None for single-central connect + live lines.

### Web app — bench drop calibration (phone-only)
- **Evidence:** commit a6e477d — the project's only real calibration was taken with this flow, 10 drops on the sealed OG board, result written to device NVS ('CAL airtime_offset_s=0.0257 source=device'). Sim coverage: tools/tests/test_web.py::test_bench_drop_calibration_saves_offset_to_device.
- **Gap:** None. Note this, not `jump drop`, is the calibration path that actually works.

### Wireless OTA DFU (the sealed box's only firmware path)
- **Evidence:** commit c4306d3; two loops 113s + 114s, every 10KB checkpoint byte-verified (SENSE_FIRST_BOOT.md:583-592); bootloader 0.6.1->0.11.0 flashed OTA, commit ec4f403
- **Gap:** one USB-out human-unplug run and one nRF Connect phone run still unrun; bootloader dark-state timeout uncharacterized

### Word-aligned QSPI addressing (align4)
- **Evidence:** firmware/src/platform/nrf52/jh_store.cpp:234 align4(); found on silicon 2026-07-31 (a 10-min session read back as 274 of ~14,000 samples), fixed same day, then 14,402 samples read back complete with zero loss (SENSE_FIRST_BOOT.md:1264-1294); the hardware round-down is modeled in firmware/test/store_host/mock_flash.cpp roundDownUnaligned()

### `format` (hard_format, chunked+fed chip erase)
- **Evidence:** firmware/src/platform/nrf52/jh_store.cpp:697; both boards formatted successfully 2026-08-14 only after commit bd0334d removed the `jh_store::jh_link::watchdog_feed` namespace-shadow stub — before that a format reset the board right after sector 96 of 512, measured (SENSE_FIRST_BOOT.md:1078-1087)
- **Gap:** Destructive by design; it is what erased the 61-jump history.

### `revive` — audited sensor rail power-cycle
- **Evidence:** firmware/src/platform/nrf52/jh_imu.cpp:160 revive() (bus_release -> rail LOW 600 ms -> rail HIGH 120 ms); 5/5 PASS on board #3, 12/12 on the mule after the begin() retry (SENSE_FIRST_BOOT.md:1059-1070)
- **Gap:** 45 ms settle was measured to fail 1-in-6 and is now 120 ms; docs/hardware-protection.md still documents 45 ms.

### `set` command + calibration persistence (jh_persist on InternalFS)
- **Evidence:** firmware/src/platform/nrf52/jh_persist.cpp:173 writeRecord() (tmp-file + atomic rename); airtime_offset_s=+0.0257 measured from 10 drops and saved over BLE 2026-08-11 (commit a6e477d; median error -25.7 ms, stdev 14.5 ms) and read back at boot after reflash and after both OTA loops
- **Gap:** The v1->v2->v3 record migration (jh_persist.cpp:117-153) has NO test of any kind, host or device. A migration bug silently reverts calibration to compiled defaults — the exact failure the code comment says must not happen.

### jump selftest (CLI renderer + device self-test)
- **Evidence:** data/logs/20260731-100312-selftest.log — real port /dev/cu.usbmodem101, no --fake; SELFTEST END result=PASS with i2c 0x68, whoami 0x6A (LSM6DS3TR-C = the Sense), flash 2092524B_free. Sense durability table firmware/SENSE_FIRST_BOOT.md:1059 — 5/5 PASS on board #3 and the OG. Tested in sim by tools/tests/test_cli.py::TestSelftest (2 tests).
- **Gap:** The only non---fake selftest logs are from 2026-07-31; every one of the 177 selftest logs since is --fake. Later Sense selftests (§16i, durability table) were driven outside ./tools/jump, so the CLI's renderer path is not re-proven against the current firmware.

### jump web (local dev server + firmware staging)
- **Evidence:** 4 non---fake web logs: data/logs/20260804-193034-web.log, 20260804-193046, 20260804-193101 (`web --port 8766`), 20260804-214955. Serves web/ on localhost so Web Bluetooth/Serial get a secure context.
- **Gap:** No automated test for stage_firmware (tools/jump:2500-2525). **(moot 2026-08-18: `stage_firmware` was removed with the browser flasher; this row is historical.)**

### tools/blecmd.py (BLE bench console)
- **Evidence:** firmware/SENSE_FIRST_BOOT.md:414-421 — 2026-08-11, `blecmd.py --watch` held a persistent central alongside the Garmin Epix Gen 2 for over an hour; 'the Mac's stats round-tripped correctly the whole time'.
- **Gap:** No automated test at all.

### tools/chargelog.py (multi-hour battery telemetry)
- **Evidence:** data/soaks/20260810-charge-and-stability-soak.csv — 548 readings at 60 s over 9.91 h (2026-08-10 22:54 → 08-11 08:49), 'zero NO REPLY and zero port errors'. Committed 8a5c26d.
- **Gap:** No automated test. Voltages in that file are ~125 mV low (pre-10d26a5), documented in data/soaks/README.md.

### tools/otadfu.py (wireless firmware flash)
- **Evidence:** firmware/SENSE_FIRST_BOOT.md:583-589 'GATE PASSED 2026-08-12 ~15:00: two complete OTA loops back-to-back — 113 s + 114 s, every 10 KB checkpoint verified against the bootloader's own byte count, validate + activate clean, app back with calibration intact after both.' Commits 06b393d, c4306d3.
- **Gap:** No automated test. Still open by the doc's own words: one USB-out human-unplug run and one nRF Connect phone run; dark-state timeout uncharacterized.

### tools/uf2conv.py + CI .uf2 publishing
- **Evidence:** CI run 31811825117: 'Converted to uf2, output size: 351232, start address: 0x27000' → web/firmware/jumpheight-sense-0.4.3.uf2, uploaded to Pages. Device side confirmed firmware/SENSE_FIRST_BOOT.md:490-495: 2026-07-31, 1200-baud touch → XIAO-SENSE drive → cp the .uf2 → boots into the app.
- **Gap:** The published .uf2 is not linked anywhere in the web app — grep -i 'uf2' over web/app.js and web/index.html returns nothing. A rider has to know the Pages URL by hand.

---

## PARTIAL  (17)

### A correct jump height displayed on a real wrist (US1 — the product)
- **Evidence:** Exactly one on-wrist frame has ever carried a value matching the puck: FIRST_COMPILE.md:146-151, 2026-08-11 — last=0.5 ft against the puck's 0.164 m — and in that SAME frame count=64 (puck said 1), best=0.3 ft (puck said 0.5) and airtime=0.00 s were all fabricated by byte loss. After the gate shipped: 0 jumps on the wrist while the puck counted 12 (commit ad51a24; commit d5d6a26), corroborated by the saved activity's SESSION jumps=0/best_jump=0.0.
- **Gap:** There has never been a session in which the watch displayed a jump that was verified correct. Needs: the puck-side BLE fixes on silicon, then a bench run where N emitted jump lines equal N rendered on the wrist with rejectedCount at 0.

### BLE pacing to the negotiated connection interval
- **Evidence:** firmware/src/platform/nrf52/jh_link.cpp:236-251 (MTU-23 measurement) and :288-298 (pace to slowest subscriber); the pacing fix was flashed 2026-08-11 ~18:25 (ad51a24 content, docs/rca-sense-imu-2026-08-11.md:17)
- **Gap:** Ran on silicon but was never re-validated on the wrist — README.md:49-53 records on-wrist validation as the next watch session.

### Gyro bias estimator (planing baseline)
- **Evidence:** firmware/include/gyro_bias.h:65 update(); converged on silicon 2026-08-11 to (1.2,-2.5,1.1) dps and pulled 3.1 -> 0.5 dps; freeze-while-airborne and the >=1900 dps rail guard covered only by tools/tests/test_gyro_bias.py
- **Gap:** The load-bearing behaviours (freeze in flight, rail guard, seed plausibility) have never run on hardware — only the at-rest convergence has.

### Gyro spin correction (omega^2 r) + self-arming lever arm
- **Evidence:** jump_detector.h:108 correct_for_spin, wired main.cpp:1000-1016; gyro on at lsm6ds3_min.h:146 (CTRL2_G=0x5C); silicon: rest bias 3.1dps -> 0.5dps, hand rotation peak 257.8dps (SENSE_FIRST_BOOT.md:1565-1573); host tests test_spin_correction.py / test_lever_arm.py / test_gyro_bias.py in the 163-pass suite
- **Gap:** gyro READS on silicon; the correction has never run on a real spun jump. lever_arm.h self-arms after the first one, so it goes live unattended.

### Jump written to flash and read back (end-to-end persistence)
- **Evidence:** firmware/src/main.cpp:1027 logJump -> firmware/src/platform/nrf52/jh_store.cpp:749 jumps_append; tools/tests/test_store_host.py 18/18 pass (re-run 2026-08-14); on-silicon trace readback of 14,402 samples at 20.0 ms cadence 2026-07-31 (firmware/SENSE_FIRST_BOOT.md:1264-1294)
- **Gap:** The only on-silicon jump history (61 jumps) was erased by `format` on 2026-08-14 (docs/bench-playbook.md:20-23). No jump has been written AND read back on the current build; that is exactly what the desk test would prove.

### Settings/properties (US7)
- **Evidence:** properties.xml defaults are what actually runs — sideload installs receive no settings (docs/garmin-datafield.md:380-387). Defaults proven live: units (above) and puckName 'JumpHeight' matching the firmware's advertised name (firmware/src/main.cpp:889 jh_link::begin("JumpHeight"), jh_link.cpp:418 Bluefruit.setName).
- **Gap:** resources/settings/settings.xml (the Garmin Connect UI) has never been exercised at all — it requires the Connect IQ Store channel, which does not exist yet (M5 not started).

### Two concurrent BLE centrals served by the puck
- **Evidence:** Epix Gen 2 + tools/blecmd.py subscribed >1h, both served, advertising continued, Mac stats round-tripped clean (SENSE_FIRST_BOOT.md:415-430)
- **Gap:** concurrency works; the watch's DISPLAYED values were corrupt (count 64 vs session_jumps=1, FIRST_COMPILE.md:145-152). Single-central control run never done.

### Two-central concurrent service
- **Evidence:** firmware/src/platform/nrf52/jh_link.cpp:267 subscribedHandles()/:277 sendOneChunk(); 2026-08-11: both centrals served, `stats` round-tripped all session, killing the Mac's central left the watch connected — but the watch rendered corrupt values (count 64 / best 0.3 ft while the puck reported 1 / 0.164 m) (SENSE_FIRST_BOOT.md:415-450)
- **Gap:** The identified cause (silently discarded chunk per connection) is fixed in code only; the two-central configuration has not been re-run since.

### USB bulk export (`dump` / `jumps` / `trace` over serial)
- **Evidence:** firmware/src/main.cpp:233 printFileFramed(); proven at 14,402 samples on silicon 2026-07-31 — but the bounded serial emit added 2026-08-12 (commit afba536, main.cpp:150-152) DROPS an entire block when Serial.availableForWrite() < len, and that dropping was observed on silicon 2026-08-14 swallowing a long pincensus line (main.cpp:520-522 comment)
- **Gap:** Never exercised at session scale since afba536. This is the path the water-session data is meant to come home through (docs/plan.md:112-115) and it can silently truncate.

### USB session download at session scale
- **Evidence:** main.cpp:150 — emitBytes drops the serial copy whenever Serial.availableForWrite() < len
- **Gap:** silent block loss on the exact path the water-session data comes home through; changed 2026-08-13, never exercised at session scale (plan.md:113-116)

### Web app — in-browser flasher (ESP Web Tools)
- **Evidence:** web/manifest.json declares exactly one build, `"chipFamily": "ESP32"`, pointing at firmware/bootloader.bin + partitions.bin + firmware.bin. web/app.js:1381-1382 mounts <esp-web-install-button manifest="manifest.json">. web/firmware/*.bin on this machine are dated Aug 4 (ESP32-era) and web/firmware/ is gitignored.
- **Gap:** Cannot flash the nRF52840 Sense — the board README.md:38 calls the product. The Install tab's fallback text (web/app.js:1372) also tells users to run `./tools/jump flash`, which fails on that board.

### `off` / System OFF
- **Evidence:** firmware/src/platform/nrf52/jh_power.cpp:239 system_off() (bus_release -> rail down -> sd_power_system_off); entry proven 2026-08-04; measured 2026-08-14: with USB attached it enters System OFF and stays there — wake needs a VBUS rising edge or the reset button (SENSE_FIRST_BOOT.md:1091-1116)
- **Gap:** The version proven on silicon used pinMode for the rail; the current H0H1 rail drop (216f75f) is unflashed. Off-current has never been measured — the overnight voltage-delta procedure (item 25c) is unrun, so 'months of standby' is arithmetic, not data.

### jump flash (build + upload)
- **Evidence:** firmware/platformio.ini:15 `default_envs = firebeetle32`; tools/jump:2420 runs `pio run -d firmware -t upload` with NO `-e`, so it always builds ESP32. The one real attempt against the current board failed: data/logs/20260813-075419-flash.log — 'Serial port /dev/cu.usbmodem1101 ... A fatal error occurred: Failed to connect to ESP32' → 'firebeetle32 FAILED'.
- **Gap:** Works for the frozen ESP32 v1; cannot flash the nRF52840 Sense at all. The Sense paths are .uf2 drag-drop, `pio run -e xiaoblesense_adafruit -t upload`, or tools/otadfu.py — none reachable from `jump flash`.

### jump simtest (the local 'software is good' gate)
- **Evidence:** Runs and passes: 'RESULT: PASS ✅ — software is good'. But step 4 shells `python -m unittest discover -s tools/tests` (tools/jump:2264), which collects only unittest.TestCase classes. Measured: unittest discover = 109 tests; pytest collects 163. Per file: test_gyro_bias unittest=0/pytest=13, test_lever_arm unittest=0/pytest=22, test_spin_correction unittest=0/pytest=19.
- **Gap:** 54 tests — the entire gyro-bias, lever-arm and spin-correction suites — are silently invisible to simtest while it prints PASS. Fix: run pytest, or convert those three files to TestCase. CI is unaffected (it runs pytest separately).

### jump status (machine-checked status command)
- **Evidence:** Added today, commit b5d2c06 (2026-08-14 10:54). Ran it: build/commands section works ('commands in binary (16): help stats jumps trace dump clear selftest revive i2cdiag info off dfu uf2 fakejump mount format'), Garmin SDK detection works. Exits rc=1 because tools/jump:2171-2174 requires docs/STATUS.md, which does not exist (`ls docs/` has no STATUS.md).
- **Gap:** Create docs/STATUS.md, or the doc-freshness gate is a permanent red. The hardware branch (tools/jump:2146-2167) has never run with a board attached — both status logs in data/logs show 'no board connected'.

### jump sync (USB download → session dir + report.md)
- **Evidence:** data/logs/20260731-094641-sync.log (no --fake, /dev/cu.usbmodem101) → data/sessions/20260731-094650/trace.csv, 201634 bytes of real device trace. But that session's jumps.csv is header-only (0 rows) — cat data/sessions/*/jumps.csv shows only 'n,takeoff_s,airtime_raw_s,airtime_s,height_m' in all 3 sessions. Sim coverage: tools/tests/test_cli.py::TestSync (2 tests).
- **Gap:** The trace half is proven; the jumps half has NEVER moved a single real row over the wire. No non---fake sync since 2026-07-31 (all 175 later sync logs are --fake). One real session with real jumps would close it.

### jump wizard (guided end-to-end setup)
- **Evidence:** tools/tests/test_cli.py::test_wizard_fake_end_to_end_and_resume passes (all 5 steps + resume + calibration written). But the real path is unexercised, and tools/jump:2717-2718 prints ESP32 wiring instructions — 'VCC→3V3 GND→GND SDA→SDA(IO21) SCL→SCL(IO22)' — for a board (XIAO nRF52840 Sense) that has an on-board LSM6DS3TR-C and no wires.
- **Gap:** Its flash step calls cmd_flash (ESP32-only) and its desktest/drop steps call the untethered flows that have never run. On the current board the wizard would fail at step 3.

---

## BUILT, UNVERIFIED  (13)

### Advertising restart while one central remains; slow idle advertising; connection LED off
- **Evidence:** firmware/src/platform/nrf52/jh_link.cpp:177-196 (restart on connect/disconnect), :465-471 (autoConnLed(false), 20 ms fast / 1 s idle — commit 216f75f)
- **Gap:** The 216f75f half is unflashed; the restart-with-one-central-remaining path has no bench record.

### BLE TX queue: honor write() return, per-connection retry, tx_drops counter
- **Evidence:** firmware/src/platform/nrf52/jh_link.cpp:323-359, surfaced at firmware/src/main.cpp:473-477; commit 216f75f body states 'Not yet on silicon: both boards are unplugged'; a chunk-length regression inside it was caught by review before it ever ran (commit 9277821)
- **Gap:** Never flashed. This is the fix for the watch-corruption signature, so its verification is the watch session's real gate.

### BLE bulk export (queue-full inline paced drain)
- **Evidence:** firmware/src/platform/nrf52/jh_link.cpp:531-549 — inline paced drain with wdtFeed() every iteration, the one sanctioned exception to 'write() only queues'
- **Gap:** No record of a full jumps.csv + trace.csv dump over BLE on silicon; SENSE_FIRST_BOOT item 2's 'no multi-sample trace gaps during a dump with two centrals' check is still open.

### BLE silent-drop fix: per-connection retry + tx_drops counter
- **Evidence:** firmware/src/platform/nrf52/jh_link.cpp:336-359 and :401; surfaced main.cpp:474; commit 216f75f, regression fix 9277821
- **Gap:** never flashed — no bench log after 2026-08-14 10:29; plan.md:122 says "DONE in code, awaiting a board"

### UI states NO_BLE and RECONNECTING
- **Evidence:** JumpFieldView.mc:302-326 (_uiState/_subText) and :385-396 (dot glyphs). No commit, log, screenshot or doc records either state ever appearing on a device or in the simulator.
- **Gap:** Also a known ambiguity, not a bug to be found later: every state except LIVE and DEAD renders identically as 'finding puck' (FIRST_COMPILE.md:103-108).

### Vibration on new jump (US3)
- **Evidence:** JumpFieldView.mc:568-591 — property read, `has :vibrate` guard, try/catch, all three degrade silently. No commit, log, image or doc records it ever firing.
- **Gap:** It can only fire behind Model.consumeNewJump(), which has never returned true on hardware. docs/garmin-datafield.md:364 §9 item 3 (is Attention.vibrate permitted from a data field) is still genuinely open on both watch models.

### Web app — export/import/share-image/delete-session/console drawer
- **Evidence:** Implemented at web/app.js:744 (deleteSession), :1024 (drawShareCanvas), :1419 (console form), :1711-1719 (export-all, import-file, clear-device). None of the 10 Playwright test names in tools/tests/test_web.py touch export-all, import, share, delete or the console.
- **Gap:** Add Playwright coverage; iOS share-sheet paths (commit 58fe5f3) are entirely unverified since that commit.

### `uf2` command (reboot into the bootloader's MSC drive)
- **Evidence:** firmware/src/platform/nrf52/jh_link.cpp:618-624, magic 0x57; docs/bench-playbook.md:107 cites the magic value, not a run
- **Gap:** No bench record of the command itself ever being issued.

### compute() keeps running while another data page is on-glass
- **Evidence:** The whole design depends on it — PuckLink.poll(), the vibrate trigger and the FIT writes are all driven from compute() (JumpFieldView.mc:102-124). Called out as an untested bet in FIRST_COMPILE.md:414-428 item 12 and docs/garmin-datafield.md:377-378 §9 item 9.
- **Gap:** Two-screen test: put the field on data screen 2, leave screen 1 showing, toss the puck, confirm the jump was captured.

### jump desktest — untethered flow (the path real hardware takes)
- **Evidence:** tools/jump:881-882 `if not getattr(args, 'fake', False): return _desktest_untethered(dev)` — the --fake branch is a completely different code path. Zero non---fake desktest logs among 86 desktest logs in data/logs (all `desktest --fake --fast`). docs/plan.md:153 still lists '`./tools/jump desktest` on the OG board — 3 untethered tosses | owner | 10 min' as an OPEN action as of 2026-08-14.
- **Gap:** One 10-minute run on the OG board. Helpers _stored_rows / _wait_for_port_return / _is_plausible_toss have unit tests (tools/tests/test_cli.py:134-182); _desktest_untethered, _reopen_after_replug and _autopsy_trace have none.

### jump drop — untethered flow (real hardware path)
- **Evidence:** tools/jump:1002-1003 gates _drop_untethered on `not args.fake`. Zero non---fake drop logs among 178 drop logs. The one real calibration that exists was NOT taken with this command — commit a6e477d says 'Ten drops from 48.5 in ... via the web app's phone flow, in the sealed case'.
- **Gap:** Never executed. The math it feeds (_drop_analyze) is well covered; the collection path is not.

### jump monitor / jump setup
- **Evidence:** tools/jump:2440 and tools/jump:2330. No test references either; zero *-monitor.log in data/logs (grep over 950 files).
- **Gap:** No coverage of any kind.

### sim/selfdiag.py (non-ballistic jump flag)
- **Evidence:** No test file imports it — grep over tools/tests/*.py for 'selfdiag' returns nothing. Only sim/experiments/e3_selfdiag_roc.py and verify_e3.py use it. Experiment result recorded in sim/experiments/RESULTS.md (AUC 1.000 no-spin, 0.258 under spin).
- **Gap:** No regression test. An experiment result is not a guard against future edits.

---

## TESTED (sim/host only)  (26)

### Both device targets compile (shipping and test builds)
- **Evidence:** MEASURED TODAY 2026-08-14 with SDK 9.2.0: monkeyc -d epix2 -t → BUILD SUCCESSFUL; -d instinct3solar45mm both with and without -t → BUILD SUCCESSFUL. Warnings only (container-type and unreachable-statement).
- **Gap:** None.

### CI (GitHub Actions build.yml)
- **Evidence:** gh run 31811825117 (2026-08-14T14:54Z, main): 'test' job → '154 passed, 9 skipped in 61.16s' and 'RESULT: PASS ✅'; firmware job → firebeetle32 SUCCESS, Sense build SUCCESS, 'Wrote 351232 bytes to web/firmware/jumpheight-sense-0.4.3.uf2'; pages job success. Last 8 runs on main all green.
- **Gap:** The 9 skips are all of tools/tests/test_hostdev.py — the CI test job installs only `pytest pyserial playwright` (build.yml), never platformio, so the only tests that drive the REAL C++ firmware core never run in CI.

### Detection algorithm (C++/Python parity, wing-ballistic validation)
- **Evidence:** 163 tests pass (python -m pytest tools/tests); sim/experiments/e2_montecarlo.py at N=200,000: overshoot mean 1.0128x, p99 1.0622x, RMSE 4.6cm, 5 silent misses; firmware/include/jump_detector.h shared core
- **Gap:** zero on-water data; every accuracy number is bench or simulation

### Firmware builds clean for the Sense target at HEAD
- **Evidence:** `pio run -d firmware -e xiaoblesense_adafruit` SUCCESS at HEAD 9277821 (RAM 10.1% / Flash 21.7%, 175584 B); .github/workflows/build.yml:85 builds the same env in CI and :113 converts to .uf2
- **Gap:** Building is not running: nothing after commit 0e2345b (2026-08-14 00:11) has been on a board.

### Layout geometry for both device targets (Layout.mc)
- **Evidence:** 13 LayoutTest cases, PASS in today's 43/43 run on both targets. I re-derived the ground truth independently from the SDK's own device files (~/Library/Application Support/Garmin/ConnectIQ/Devices/{epix2,instinct3solar45mm}/simulator.json → layouts[0].datafields): epix2 slots 416/207/132/146/103 and instinct 176x176, 99x72, 176x104, 110x27 all match LayoutTest.mc:79-92,201-211.
- **Gap:** Only the FULL tier has ever been looked at on a screen. Half and small tiers have never been eyeballed on device or simulator (commit dcba0f9: 'the visual sweep of half/small tiers is still owed').

### Line reassembly + key=value parse (Protocol.mc)
- **Evidence:** 13 ProtocolTest cases. MEASURED TODAY 2026-08-14: monkeydo -t → 'PASSED (passed=43, failed=0, errors=0)' on BOTH epix2 and instinct3solar45mm, SDK 9.2.0.
- **Gap:** Only indirectly exercised on hardware (via the ingest path).

### Reconnect / STATS reseed on a real link (US6)
- **Evidence:** PuckLink.mc:340-353 sends 'stats\n' once per subscribe; ModelTest testStats_seedsAfterReconnectPreservingArrivalOrder PASSES (today's run).
- **Gap:** Never exercised on a watch: the M2 checklist item 'power-cycle the puck → count and best correct after reconnect' (garmin/README.md:189-194) is unticked and no run is recorded anywhere.

### Self-calibrating lever arm (mount calibration)
- **Evidence:** firmware/include/lever_arm.h:125 observe()/153 commit(); tools/tests/test_lever_arm.py::test_uncalibrated_device_fixes_itself_over_jumps, ::test_no_deliberate_shave
- **Gap:** Two shipped holes: (1) no persistence key exists for it (firmware/include/platform/jh_persist.h:42 has only AirtimeOffsetS/HeightScale/VbatScale/ProbeGuard/StoreGuard), so a converged lever arm dies at every reboot; (2) it is emitted on no protocol line — INFO/CAL/PARAMS carry only the compiled spin_lever_m=0 (main.cpp:664-676), so a self-armed correction is invisible to every client.

### Spin correction (omega^2*r) on the detector hot path
- **Evidence:** firmware/include/jump_detector.h:108 correct_for_spin(); tools/tests/test_spin_correction.py + test_lever_arm.py + test_gyro_bias.py + test_trace_codec.py = 65 passed 2026-08-14
- **Gap:** Zero silicon time. Inert (identity) until lever_arm arms it, then live with no hardware validation — SENSE_FIRST_BOOT.md item 26 steps 2-4 unrun.

### Torn-write / power-cut storage recovery
- **Evidence:** firmware/src/platform/nrf52/jh_store.cpp:270 skipPastTornWrite(); tools/tests/test_store_host.py::test_power_cut_recovers_next_append_lands_cleanly_and_is_readable, ::test_multiple_torn_writes_all_recover_and_stay_readable, ::test_power_cut_trace_block_recovers_and_is_readable, ::test_failed_jump_write_same_cycle_count_and_dump_agree
- **Gap:** Interrupted ERASE across a real power cut is explicitly out of the harness's scope (firmware/test/store_host/mock_flash.h:35-36) and the bench test in item 21 has never been run.

### Watch-side corrupt-line rejection gate
- **Evidence:** garmin/jumpfield/source/Model.mc:180 _jumpIsCorrupt(), :213 reject+count; 17 (:test) functions in garmin/jumpfield/tests/ModelTest.mc; commits eb87382 + d5d6a26 (2026-08-11)
- **Gap:** never exercised on the watch against a genuinely corrupt line; simulator tests only

### Web app — bench toss test (phone-only)
- **Evidence:** tools/tests/test_web.py::test_bench_toss_flow_passes_on_three_clean_tosses (mock device). Implementation web/app.js:1117-1220.
- **Gap:** No hardware record. It is the web twin of the untethered desktest and could substitute for it.

### Web app — session sync + localStorage history
- **Evidence:** tools/tests/test_web.py::test_dump_flow_creates_a_session_row and ::test_sync_banner_flow_syncs_session_and_offers_clear — both against MockTransport.
- **Gap:** No primary evidence of a web-app dump on real hardware. firmware/SENSE_FIRST_BOOT.md:104-109 still lists BLE dump throughput as 'Still to measure on the bench'. docs/roadmap.md:67 asserts sync was hardware-validated; I found nothing backing it.

### `clear` (non-destructive-to-storage reset of stored data)
- **Evidence:** firmware/src/platform/nrf52/jh_store.cpp:1011; tools/tests/test_store_host.py::test_clear_then_reuse, ::test_failed_clear_leaves_not_ok_and_no_resurrection
- **Gap:** No record of `clear` ever running on silicon. Item 13's worst case (up to ~495 sector erases with the device unresponsive and no progress callback) is unmeasured.

### `mount` (non-destructive try_mount, never formats)
- **Evidence:** firmware/src/platform/nrf52/jh_store.cpp:673 try_mount(); tools/tests/test_store_host.py::test_try_mount_corrupt_superblock_never_touches_data, ::test_try_mount_valid_store_resumes_everything, ::test_try_mount_virgin_chip_refuses_to_format
- **Gap:** The single hardware attempt (2026-08-12) hung and cost a watchdog reset (SENSE_FIRST_BOOT.md:717-720). It has never succeeded on silicon, and the storage fault it was built for turned out to be the watchdog-stub bug instead.

### jump desktest — scripted --fake flow
- **Evidence:** tools/tests/test_cli.py::TestDesktest::test_full_desktest_flow passes; data/logs/20260812-151714-desktest.log (`desktest --fake --fast`).
- **Gap:** This branch cannot run on real hardware by construction (tools/jump:878-882), so passing it proves nothing about the device.

### jump eval + sim/evaluate.py (labeled-corpus scorer)
- **Evidence:** tools/tests/test_evaluate.py — 5 tests pass on synthetic labeled sessions. Running `./tools/jump eval --verbose` today prints 'No labeled sessions found (need a labels.csv beside trace.csv).' `find . -name labels.csv` → zero results; `find . -name session.json` → zero results.
- **Gap:** The machinery works; the corpus is empty. Needs the first labeled session.

### jump gen / gen_params --check (params single source of truth)
- **Evidence:** `python3 tools/gen_params.py --check` → 'params.gen.h is up to date', rc=0. Enforced in CI (build.yml 'Check generated params header is fresh') and in simtest step 2.
- **Gap:** None.

### jump report (diagnostic bundle)
- **Evidence:** tools/tests/test_cli.py::test_report_fake passes, asserting the '== system / == config/params.json / == live device / SELFTEST END result=PASS / == END REPORT' markers.
- **Gap:** Zero *-report.log in data/logs — never run outside the test suite.

### jump validate (video ground-truth calibration)
- **Evidence:** 30 tests in tools/tests/test_cli.py (TestValidateMath, TestValidatePairsFile, TestValidateEndToEnd, TestValidateNonTTY, TestValidateDeviceIdentityGuard, TestValidateSessionMode) — all pass. 284 validate logs in data/logs, every single one `validate --fake --fast`.
- **Gap:** Never run against real filmed jumps — there are none. It is blocked on the same water session as eval.

### sim experiments E1–E6, g4, g5
- **Evidence:** Reproducible today: `python3 sim/experiments/e5_verify.py` reprints the failure map (DANGER-BLIND at a_v 0.09–0.10, HARD-BLIND ≥0.34) matching sim/experiments/RESULTS.md to the digit. Outputs present in sim/experiments/out/ (gitignored, regenerated).
- **Gap:** Not run by CI or simtest — nothing detects a regression in them. And their digest is stale (see staleDocs).

### sim/detector.py ↔ firmware C++ detector parity
- **Evidence:** `./tools/jump simtest` → '✅ detector vs synthetic ground truth (5 seeds)', '✅ C++ firmware detector compiles', '✅ C++ and Python detectors agree exactly (C++ 4 jumps vs Python 4)'. Runs in CI.
- **Gap:** Parity is checked on one file, data/example_session.csv (synthetic, header t_s,ax,ay,az).

### sim/lever_arm.py + spin correction (ω²r)
- **Evidence:** tools/tests/test_lever_arm.py (22 tests) + tools/tests/test_spin_correction.py (19) pass under pytest. Shipped code: firmware/include/jump_detector.h:106-132 correct_for_spin, firmware/src/main.cpp:1014 `if (lever_arm.commit()) detector.set_spin_lever_m(...)`. Default off: config/params.json spin_lever_m = 0.0.
- **Gap:** Zero silicon. And both suites are pytest-only, so `./tools/jump simtest` never runs them (measured above) — the local gate is blind to all 41 tests.

### sim/windows.py, sim/seastate.py, sim/trace_codec.py
- **Evidence:** tools/tests/test_windows.py (16), test_seastate.py (8), test_trace_codec.py (11 — g++-built C++/Python codec parity), test_store_host.py (18 — g++ flash-store harness). All pass locally and in CI.
- **Gap:** windows.split_segments is duplicated verbatim at tools/jump:1161; only the sim/ copy is tested (tools/tests/test_windows.py:75 even calls it 'the same splitting rule the CLI autopsy uses').

### tools/fake_device.py (protocol emulator)
- **Evidence:** Drives 32 CLI tests in tools/tests/test_cli.py, all passing. FW_VERSION 0.4.3 matches firmware/src/main.cpp:61.
- **Gap:** Implements 10 of the firmware's 16 commands (help/stats/jumps/trace/dump/clear/selftest/set/info/_sim). Missing: revive, i2cdiag, off, dfu, uf2, fakejump, mount, format. `off` is covered instead by test_hostdev; the rest are covered nowhere at CLI level.

### tools/hostdev.py + host-platform integration tests
- **Evidence:** tools/tests/test_hostdev.py — 9 tests pass locally (boot sequence, info keys, `set` persistence across process restart, scripted jump detected+stored, selftest over the bridged pty, battery telemetry present/absent, `off` unsupported → ERR, `off` on battery platform). Drives firmware/src/main.cpp built as env:host.
- **Gap:** Skipped in CI (no pio). A green CI badge does not mean these ran.

---

## NOT BUILT  (11)

### Connect IQ Store distribution (M5)
- **Evidence:** No .iq bundle, no store artifacts, no developer-account trace in the repo; garmin/README.md documents only the sideload path.
- **Gap:** Blocks settings.xml being reachable at all, and therefore US7.

### Corrupt-line rejection made visible to the rider
- **Evidence:** Model.rejectedCount() (Model.mc:173) has ZERO callers in /Users/joshcrow/Jump-height/garmin/jumpfield/source (grep: only ModelTest.mc references it). The 'x3' readout came from bin/JumpField-DIAG-epix2.prg (built 2026-08-11 18:06), whose source is not in the tree.
- **Gap:** In the shipping build a rider sees a stale number with no indication that lines are being dropped — the exact silent-failure class the gate exists to prevent, moved one layer up.

### Download integrity verification (CLI + web)
- **Evidence:** firmware/src/main.cpp:141-153 emitBytes silently DROPS the whole write when Serial.availableForWrite() < len; main.cpp:243 printFileFramed pushes 240-byte blocks through it with no retry. tools/jump:1215-1225 writes the dump straight to disk with no byte-count check, even though STATS already reports trace_bytes (main.cpp:479-486) explicitly flushed at main.cpp:438 'so trace_bytes matches what a dump would actually deliver'. web/app.js:920 captures expected=lastTraceBytes for the progress bar only; onSyncDone (web/app.js:929-983) never compares it.
- **Gap:** Compare received bytes against STATS trace_bytes on both paths and refuse the 'clear device' offer on a short read. docs/plan.md:114 already names this as a top water-session risk.

### Jump survives to storage on the current build (desk test gate)
- **Evidence:** every data/logs/*desktest*.log carries --fake (0 non-fake of 30+); storage path changed 2026-08-13 (bd0334d, watchdog-feed stub in a nested namespace) and both boards were format-ed after
- **Gap:** 10 minutes of owner time; plan.md:121 names it the one hard pre-water gate

### Labeled session corpus (the data-pipeline loop's input)
- **Evidence:** data/sessions/ contains 3 dirs, all dated 2026-07-31, all with header-only jumps.csv and no labels.csv/session.json. No baselines/ directory exists. The only real jump data ever recorded on a Sense — the OG's 61 jumps — was erased: docs/bench-playbook.md:19-22 'both boards were format-ed ... the OG's 61-jump history is gone for good'.
- **Gap:** One filmed water session, or the land dress rehearsal docs/plan.md:124 already schedules.

### Labeled water data / eval corpus
- **Evidence:** data/sessions/*/ contain only trace.csv, jumps.csv, report.md, session-info.txt — no labels.csv, no session.json; all three report.md files read "0 jumps"
- **Gap:** one filmed water session; and per plan.md:63-73 the label schema itself must change (current video truth is circular)

### Memory/peak budget under the data-field limit (spec §5.6, <28 KB)
- **Evidence:** No measurement exists anywhere in the repo; the simulator memory view has never been run. docs/garmin-datafield.md:265-272 defers it to M3, which has not started.
- **Gap:** One simulator session with the memory view open.

### Non-ballistic self-diagnosis flag (median airborne |a| > 0.12 g)
- **Evidence:** sim/selfdiag.py exists; zero matches for selfdiag/median-|a| gating in firmware/include/jump_detector.h or firmware/src/main.cpp
- **Gap:** RESULTS.md:86-88 and wing-ballistic-sim.md:128 call it a firmware requirement; sense.md:199 describes it as already "riding along"

### Standby / motion-wake power tier
- **Evidence:** INT1 only ever floated (jh_imu.cpp:206); no LED code in src/platform/nrf52 (grep empty); advertisement carries no battery or armed state
- **Gap:** main-loop restructure; power-states.md's own banner is accurate here

### Standby tier: motion wake, auto-off, low-voltage cutoff, LED language, battery in the advertisement
- **Evidence:** Verified by code read: INT1 is only ever floated (firmware/src/platform/nrf52/jh_imu.cpp:206); no LED, PDM, BLEBas or advertisement-payload code exists anywhere in firmware/src/platform/nrf52 (grep); the 20 s idle timeout gates recording only (main.cpp:956). docs/power-states.md:2-20 states the same after its own code read.
- **Gap:** Deliberately deferred past the water session (docs/plan.md:138-143).

### `railcheck` diagnostic
- **Evidence:** Absent from main — exists only on branch mule-railcheck (git branch -a); no railcheck symbol in firmware/src/platform/nrf52 on HEAD
- **Gap:** Its verdict is still quoted as current fact in docs/hardware-protection.md:78, and that verdict was retracted (SENSE_FIRST_BOOT.md:1027-1034).

---

## Documentation known to be STALE

Found by the same audit. Until each is fixed, treat the claim as false.
The worst offenders carry a SUPERSEDED banner pointing here.

> **Amendments 2026-08-18 — two rows below have themselves gone stale.** This
> table is a 2026-08-14 snapshot; the delta section at the top of this file is
> newer than it, and where they disagree the delta section wins.
>
> - **The browser-flasher rows** (`README.md:37 and docs/roadmap.md:75-76`,
>   `README.md:37`) cite `web/manifest.json` and `web/firmware/` as their
>   evidence. Those files no longer exist: the ESP32 deprecation deleted the
>   flasher outright on 2026-08-18 (see *HARDWARE DEPRECATION* under
>   `## 2026-08-18` above). The corrected claim stands — there is no browser
>   flash path — but its evidence must now be read from git history.
> - **The "no jump has ever been correctly displayed on a watch" rows**
>   (`README.md:53-57`, and the M2-unsigned framing in `garmin/README.md`,
>   `docs/garmin-datafield.md`, `docs/roadmap.md`) are superseded by *WATCH M2
>   CLOSED* under `## 2026-08-18` above: 3 reconciled real tosses plus 10 live
>   fakejumps rendered on the owner's Epix Gen 2. Those rows' corrective text
>   was accurate on 2026-08-14 and is now itself out of date.

| File | Claims | Actually |
|---|---|---|
| `docs/rca-sense-imu-2026-08-11.md` | Top-line verdict, lines 3-4: 'hardware failure of the LSM6DS3TR-C (or its power path)... Firmware is exonerated by direct experiment.' The 2026-08-12  | Overturned on 2026-08-14. The cause was firmware: pinMode() selected standard GPIO drive on P1.08, which sources the sensor's VDD (SENSE_FIRST_BOOT.md:938-1004, DECISIONS #37). That same boa |
| `docs/rca-sense-imu-2026-08-11.md` | Lines 174-179: 'The fix that shipped (fifth design)... crash-loop detection — a magic+flag pair in .noinit RAM set before the first Wire touch.' | That design was measured NOT to work: this core's linker scripts define no .noinit region, so the flags landed in zero-initialised RAM and the skip never fired (firmware/src/platform/nrf52/j |
| `docs/hardware-protection.md` | Title and §1 (lines 1-25): 'how we never cook a board again... Two XIAO nRF52840 Sense IMUs went unresponsive... it was this project's own firmware el | Both premises are refuted. Seeed schematic v1.1 sheet 2: there is no regulator — the GPIO pad IS the sensor supply, and the 10k bus pull-ups (R14/R15) hang off it (docs/xiao-hardware-truth.m |
| `docs/hardware-protection.md` | §4, lines 78-81: 'Mule verdict: en=1 pin=0 — the EN net itself is stuck at ground... the fault is at P1.08 or the net it drives.' | Explicitly retracted 2026-08-14: the pull-down-based instrument returns the same answer for healthy and broken hardware, so 'the railcheck verdict built on it was wrong' (SENSE_FIRST_BOOT.md |
| `docs/hardware-protection.md` | §2 item 2, lines 42-44: the safe power-up pair is 'detach -> rail LOW -> 600 ms discharge -> rail HIGH -> 45 ms settle'. | The shipped sequence is 600 ms discharge then 120 ms settle (firmware/src/platform/nrf52/jh_imu.cpp:185-194). 45 ms was measured to fail first contact 1-in-6 revives on the mule; 120 ms neve |
| `docs/hardware-protection.md` | §5 item 4c, line 112: the unseal gate requires 'off->wake x5 tonight, >=20 lifetime — the System OFF transition, selftest after every wake.' | That soak cannot be run on this bench and has not been: `off` only wakes on a VBUS edge or the reset button, so every cycle needs a human replug (SENSE_FIRST_BOOT.md:1091-1113). Steps 4a (se |
| `docs/hardware-protection.md` | §3 rule 4, line 66: 'Electrical experiments run on the sacrificial board only (currently: the mule).' | Directly contradicted by the current board registry: the 'mule' and the 'OG' are the same board, that name is retired, and it is now the product board with the soldered pigtail and the drop  |
| `docs/ble-dependability.md` | §2 table (line ~36): 'Layer 1 Byte integrity — BROKEN', and §1 quotes jh_link.cpp:298 advancing the queue tail unconditionally. | Fixed in code the same day, 79 minutes after the doc was committed: firmware/src/platform/nrf52/jh_link.cpp:323-359 now retries per-connection, bounded at 8 passes, and counts forced drops a |
| `docs/plan.md` | P1 item 1, lines 170-172: '`system_off()` stops cutting the rail — removes the one shipped contradiction of the rail-static rule.' | The shipped code still cuts the rail; only the drive strength changed (firmware/src/platform/nrf52/jh_power.cpp:280-284, documented there as 'the ONE sanctioned rail transition'). The same d |
| `docs/sense.md` | §3.4, lines 133-135: battery telemetry is 'Bench-pending: vbat-vs-multimeter check (SENSE_FIRST_BOOT item 24 — SAADC acquisition time vs the ~340 kOhm | Done and closed 2026-08-11: the TACQ sweep ran on silicon, the acquisition-time half was fixed (15 us via raw SAADC registers) and verified 4035-4044 -> 4079-4082 mV; the remaining 1.88% is  |
| `docs/sense.md` | §7 'VERIFY at bring-up (answer on the bench, then edit this doc)', lines 324-345 — all ten items presented as open; header line 3 says the board is 'o | Items 1, 2, 3 and 9 were answered on 2026-07-31 / 2026-08-11 and the doc was never edited as its own instruction requires. The checklist now runs to item 26 plus lettered sub-items. The inst |
| `firmware/SENSE_FIRST_BOOT.md` | 'Explicitly out of scope' section, lines 1629-1631: 'Nordic DFU / BLEDfu (docs/sense.md §3.3) — this port's CI publishes a .uf2 for cable/drag-drop re | All three are built and on silicon: BLEDfu starts at jh_link.cpp:441, the `dfu` command works, and the OTA gate passed twice on 2026-08-12 (same file, lines 583-592); battery telemetry and ` |
| `firmware/src/main.cpp` | File header lines 1-41: 'Built today for the FireBeetle 2 ESP32-E field board (platform/esp32)... The BLE stack lives behind the jh_link seam (ESP32 i | The primary target is the XIAO nRF52840 Sense; the ESP32 build is feature-frozen (DECISIONS #27). handleCommand (main.cpp:433) implements 20 commands: help, stats, jumps, trace, dump, clear, |
| `firmware/src/main.cpp` | FW_VERSION "0.4.3" (line 61) — reported to every client as INFO fw=0.4.3 and in the BLE greet banner. | Unchanged since the seam split in late July, across the bounded-TWIM driver, the drive-strength fix, the storage watchdog fix, the begin() retry and the BLE transmit fix. No client — and no  |
| `README.md` | Status table line 35: 'the original became the bench mule after an IMU-bus fault'; lines 41-47: the watch-numbers bug is 'Fixed on both ends (puck pac | The original board never had an IMU-bus fault; it is healthy and is the chosen product board (docs/bench-playbook.md:13). The puck-side pacing change did ship and run, but the transmit fix t |
| `/Users/joshcrow/Jump-height/garmin/README.md:16-22` | "Status: compiles clean and all 24 unit tests PASS in the simulator (2026-08-04)... You are M2: sideload to the real watch and scan for the real puck. | There are 43 tests, not 24 (17 ModelTest + 13 ProtocolTest + 13 LayoutTest). I ran them today: 43/43 PASS on epix2 AND instinct3solar45mm. The sideload happened 2026-08-10 and the live link  |
| `/Users/joshcrow/Jump-height/docs/garmin-datafield.md:28-29` | "24/24 simulator unit tests pass" | 43/43, measured 2026-08-14 on both device targets. |
| `/Users/joshcrow/Jump-height/docs/roadmap.md:177-186` | "all 24 sim unit tests PASS"; "Not yet signed off as M1"; "the single-central control run is the next thing to do"; "a real toss registering (session_ | 43 tests. It is M2 that is unsigned — M1 was met and docs/garmin-datafield.md:6 says so, so the two docs disagree on which milestone is open. The control run is no longer the open question:  |
| `/Users/joshcrow/Jump-height/garmin/FIRST_COMPILE.md:140-193` | "OPEN BUG — corrupted values on the watch, cause NOT yet found. Status 2026-08-11: open." / "Leading hypothesis (untested): Connect IQ drops BLE notif | All four are out of date, and this is the file every other doc points at. The experiment was run the same day (the DIAG build showed a line missing exactly one 20-byte MTU-23 payload). The c |
| `/Users/joshcrow/Jump-height/README.md:44-57` | "Watch-numbers bug, root-caused... Fixed on both ends (puck paces to the negotiated link; the watch rejects lines that fail protocol invariants)." | The watch end is fixed and hardware-validated. The puck end is NOT on silicon: commit ad51a24 says "Neither is silicon-verified yet", commit 216f75f says "Not yet on silicon: both boards are |
| `/Users/joshcrow/Jump-height/README.md:53-57` | Under "Running on hardware": jump height "streamed live over BLE... and displayed on the watch alongside the puck's own battery level." | No jump has ever been correctly displayed on a watch, and the puck-battery sub-line has never been observed on any watch — the battery parse is unit-tested only (ModelTest testStats_batteryA |
| `/Users/joshcrow/Jump-height/docs/ble-dependability.md:128-131` | Build order item 2: "Watch-side corruption gate. Pure Monkey C, no firmware coupling. Correct regardless of root cause." — listed as work to do, doc w | Built 2026-08-11 and hardware-validated the same day. This is the identical stale item that was found and removed from docs/plan.md on 2026-08-14 (commit 642d5a7) — the correction was never  |
| `/Users/joshcrow/Jump-height/docs/ble-dependability.md:145-148` | "The raw-line diagnostic on the watch: render received line length and tail for one sideload. That is the experiment garmin/FIRST_COMPILE.md has been  | Already performed on 2026-08-11 — bin/JumpField-DIAG-epix2.prg. Its output (a JUMP line missing exactly the 20 characters " height_ft=5.3 best_") is what produced the ATT_MTU-23 finding this |
| `/Users/joshcrow/Jump-height/docs/garmin-datafield.md:36-41` | "M2 is NOT signed off: with a second BLE central subscribed the displayed values are corrupt... the single-central control run is the next step and ha | M2 is indeed unsigned, but the two-central framing is superseded: docs/ble-dependability.md:36-39 concludes a single central under load hits the same path — "a single-central product risk, n |
| `/Users/joshcrow/Jump-height/docs/garmin-datafield.md:369-370` | §9 still-open item 8: "Whether the owner's watch mounts as USB mass storage or MTP-only on macOS." | Answered 2026-08-10: MTP-only, and mtp-sendfile fails on Garmin — a custom libmtp sender was written and the push verified (commit 370080a, garmin/README.md:92-118). |
| `/Users/joshcrow/Jump-height/docs/garmin-datafield.md:311-317` | §7: "Two concurrent BLE centrals — ✅ DONE (firmware v0.4.2)... NimBLE's own default max is 3... tested watch + phone live." | Describes the ESP32/NimBLE build. The watch's actual peer is the nRF52/Bluefruit puck (Bluefruit.begin(2,0)), and "tested watch + phone live" is the exact configuration in which corrupted va |
| `/Users/joshcrow/Jump-height/garmin/jumpfield/monkey.jungle:2-8` | "One source tree for one device family (Instinct 3 Solar) — deliberately simple. Per-family source overrides are a spec §5.1/§9 M5 concern, not now." | epix2 has been a built, sideloaded and shipping target since 2026-08-10 and is the only device the field has ever run on. |
| `/Users/joshcrow/Jump-height/garmin/jumpfield/manifest.xml:19-23` | "Instinct 3 Solar only for M0-M4... Fenix/Epix/Forerunner families are added at M5 once the simulator layout passes on this device"; and (lines 31-40) | Self-contradicted five lines below by <iq:product id="epix2"/>. The tier breakpoints are now pinned by LayoutTest against epix2's real per-slot geometry from the SDK's own simulator.json (41 |
| `/Users/joshcrow/Jump-height/garmin/README.md:164-179` | "Three things to check first in the simulator" #1: "The layout-tier breakpoints (FULL_MIN_H/HALF_MIN_H) are unverified guesses; nudge them once you se | The breakpoints are unit-tested against both devices' real slot tables (LayoutTest.mc:201-211, passing). Profile registration reached STATUS_SUCCESS and scanning found the puck on hardware 2 |
| `/Users/joshcrow/Jump-height/garmin/README.md:10-14` | "Read FIRST_COMPILE.md before your first build — this code was authored without access to the Connect IQ SDK... every API call was researched but not  | SDK 9.2.0 is installed and active; the first compile was 2026-08-04 and both targets build today. FIRST_COMPILE.md's own header block says so — the README intro still reads as pre-compile. |
| `/Users/joshcrow/Jump-height/tools/jump:846-849` | desktest advice: "A second BLE central corrupts the watch display (open bug, garmin/FIRST_COMPILE.md), and watch-only is also the control run that inv | The cause is identified and is reachable with one central. Pairing the watch alone is still prudent, but the "open bug / control run needed" framing is three days out of date and points read |
| `/Users/joshcrow/Jump-height/docs/plan.md:123` | "17 Model tests cover it [the corruption gate]." | ModelTest.mc has 17 tests in total; 6 of them (testCorrupt_*) are the gate's. The gate is real and hardware-validated — only the count is the file's, not the feature's. |
| `/Users/joshcrow/Jump-height/docs/STATUS.md` | tools/jump:2168-2178 treats docs/STATUS.md as the project's single source of truth and warns when code is newer than it. | The file does not exist. Today's run recorded "⚠️ docs/STATUS.md missing — there is no single source of truth" (data/logs/20260814-105535-status.log). The one machine-checked staleness guard |
| `docs/roadmap.md:18,25,31` | 'Phase 1 — Bench firmware ✅ COMPLETE (hardware-validated 2026-07-25)' … 'on hardware, a passed desk test on the real assembly (untethered tosses) plus | docs/plan.md:153 (2026-08-14) still lists '`./tools/jump desktest` on the OG board — 3 untethered tosses / owner / 10 min' as an open P0 action, calling it 'the ONLY proof that a jump surviv |
| `README.md:40 and docs/roadmap.md:64-67` | 'Browser app / ✅ Live BLE stats, session sync, charts, in-browser flashing' and 'A zero-install browser flasher (ESP Web Tools) adds an Install button | web/manifest.json declares one build with "chipFamily": "ESP32". The board README.md:38 names as the product is the XIAO nRF52840 Sense, which ESP Web Tools cannot flash. The Sense's .uf2 IS |
| `docs/roadmap.md:67-69` | 'On hardware: BLE validated end-to-end (Bluefy on iPhone, live jumps, sync, bench flows)'. | Connect + INFO readout is evidenced (SENSE_FIRST_BOOT.md:466-472) and the bench drop flow is evidenced (commit a6e477d). I found no primary record of a web-app *sync* (dump) on hardware, and |
| `docs/data-pipeline.md:10` | 'You already have ~70% of it.' | The capture/analysis half exists; the ground-truth half is 0%. `find . -name labels.csv` and `-name session.json` both return nothing, `./tools/jump eval --verbose` prints 'No labeled sessio |
| `docs/data-pipeline.md:141` | 'airtime_offset_s — `jump drop` (bench drop tests). Already wired.' | `jump drop` has never run on hardware — all 178 drop logs are `--fake`, and its real-hardware branch (tools/jump:1002) is unreachable in --fake mode. The one calibration that exists (commit  |
| `sim/experiments/RESULTS.md` | Dated 2026-08-04, headed 'The six experiments' (E1–E6), reporting E1 'frac_detected = 1.000 (3072/3072)' and 'All five run experiments carry an indepe | Last touched by commit 8cc9d2e (2026-08-10) and never updated since. It omits g4 (run 2026-08-05), g5 lever-tolerance (run 2026-08-10, commit 49eb900), and E2′ — the N=200,000 rerun of 2026- |
| `docs/gyro-sim-plan.md:48` | 'g5 landing attitude / SKIP' and the headline 'exactly ONE sim is worth running (g4)'. | sim/experiments/g5_lever_tolerance.py was written and run on 2026-08-10 (commit 49eb900) with a load-bearing result — a deliberate 5% lever shave 'broke 5 of 8 lever x spin cases outright'.  |
| `docs/bench-playbook.md:221` | 'Mule battery-unclip experiment still worth one clean run … `mount` verdict on the 61-jump history stands or falls there.' | Contradicted by lines 19-22 of the same file, dated 2026-08-14: 'both boards were `format`ed to repair storage, so the OG's 61-jump history is gone for good … the erase is what made it perma |
| `tools/jump:2171-2174` | cmd_status treats docs/STATUS.md as 'the single source of truth' and warns/exits 1 if code is newer than it. | docs/STATUS.md does not exist. `./tools/jump status` prints '⚠️ docs/STATUS.md missing — there is no single source of truth' and returns rc=1 on a clean checkout. The command built today (co |
| `tools/jump:2717-2718 (wizard) and web/index.html:7` | Wizard step 3 instructs 'Make sure the 4 sensor wires are connected … VCC→3V3 GND→GND SDA→SDA(IO21) SCL→SCL(IO22)'; index.html's header comment says ' | Both are ESP32 + external MPU-6050 era. The current product board has an on-board LSM6DS3TR-C and no wiring step. The wizard would then call cmd_flash, which builds firebeetle32 only (firmwa |
| `web/app.js:1372 (Install-tab fallback) and BUILD.md:302` | 'To build and flash them yourself, run ./tools/jump flash — it builds the firmware locally and uploads it over USB' / '`./tools/jump flash` / settings | tools/jump:2420 issues `pio run -t upload` with no `-e`, so it always builds the ESP32 default env. Against the Sense it fails with 'A fatal error occurred: Failed to connect to ESP32' (data |
| `README.md:38` | The puck is the "second unit" and "the original became the bench mule after an IMU-bus fault — full story in docs/rca-sense-imu-2026-08-11.md" | There was never an IMU-bus fault. DECISIONS #37 / SENSE_FIRST_BOOT.md:938-1004: GPIO drive strength; "Nothing was ever damaged." The original IS the product board again as of 2026-08-14 (ben |
| `README.md:44-57` | "Watch-numbers bug, root-caused: Connect IQ negotiates the minimum BLE packet size, so jump lines fragment five ways and under-paced sending lost frag | Superseded. The confirmed defect (code read, 2026-08-14) is the ignored BLEUart::write() return in jh_link.cpp — ble-dependability.md:15-39 states pacing was not it. The firmware half was on |
| `README.md:40` | Browser app: "in-browser flashing" listed with an unqualified green check | ESP Web Tools is ESP32-only; web/firmware/ holds only bootloader.bin/firmware.bin/partitions.bin. The Sense — the board README:218 tells you to build — has no browser flash path at all (sens |
| `docs/rca-sense-imu-2026-08-11.md:3-4` | "Verdict: hardware failure of the LSM6DS3TR-C (or its power path)... Firmware is exonerated by direct experiment." | Exactly inverted. The cause was firmware (pinMode selecting standard drive). The 08-12 addendum stops short of this; nothing marks the file superseded by the 08-14 answer, and README:35 stil |
| `docs/rca-sense-imu-2026-08-11.md:172-176` | The shipped crash-loop guard is "a magic+flag pair in .noinit RAM" | .noinit does not exist in this core's linker scripts — measured. The guard lives in jh_persist (internal LittleFS), which "survives everything" (bench-playbook.md:145-148). |
| `docs/rca-sense-imu-2026-08-11.md:191-198` | "Still open" list: old board's held bus unexplained; new board's QSPI mount fails; boot selftest 0.960g/0.0966 noise "benign-looking, unverified" | All three closed. Held bus = unpowered rail (16i). QSPI fixed 2026-08-13 (bd0334d — watchdog_feed stub in a nested namespace). The odd noise row is DECISIONS #35, fixed in dca2985. |
| `docs/hardware-protection.md:3-8` | "Two XIAO nRF52840 Sense IMUs went unresponsive in three days. The common factor... was this project's own firmware electrically mistreating the senso | No sensor was ever damaged. Both recovered with one PIN_CNF field changed (DECISIONS #37). The premise of the whole document is refuted. |
| `docs/hardware-protection.md:18` | "pin P1.08 enables a small regulator that powers BOTH the LSM6DS3TR-C's VDD AND the on-module I2C pull-up resistors" | There is no regulator, load switch, or FET on that net. The GPIO pad IS the supply — Seeed schematic v1.1 sheet 2, itemized in xiao-hardware-truth.md:14-30. |
| `docs/hardware-protection.md:78-81` | "Mule verdict: en=1 pin=0 — the EN net itself is stuck at ground... the fault is at P1.08 or the net it drives" | Retracted. The railcheck method returns the same answer for healthy and broken boards (xiao-hardware-truth.md:80-86); the mule reads accel PASS 1.029g (SENSE_FIRST_BOOT.md:972). |
| `docs/hardware-protection.md:65-66` | "Electrical experiments run on the sacrificial board only (currently: the mule)." | The mule and the OG are the same board, and it is the product board carrying the soldered pigtail and the only drop calibration (bench-playbook.md:14,17 — "Calling the product board sacrific |
| `docs/hardware-protection.md:90-116` | §5 unseal gate presented as in-flight on board #3: soak ladder a/b/c "as executed", any FAIL stops the ladder | Completed and superseded: board #3 passed 5/5 selftest + 5/5 revive and is now the BACKUP (bench-playbook.md:15). §5's stated purpose — confirming the damage mechanism — is moot; there was n |
| `docs/bench-playbook.md:179-180` | Rule 4: "Electrical experiments run on the designated sacrificial board only (currently: the mule)." | Contradicts this same file's registry 165 lines earlier (:14,:17), which retires the name and makes that board the product board. Two rules in one file point at opposite outcomes. |
| `docs/bench-playbook.md:88-92` | "an I2C slave caught mid-transaction... keeps clamping SDA through every warm reset... Both boards' 'held bus' (2026-08-12) match this signature" | Clamp theory died the night it was written (SENSE_FIRST_BOOT.md:757-787, falsifiers fired). The real mechanism is a rail that never rises, so the pull-ups are unpowered and both lines sit lo |
| `docs/bench-playbook.md:90-91` | "a 9-clock SCL bus-clear, the standard I2C unstick, not yet implemented" | Moot and not planned. Bounded TWIM (twim_bounded.h, commit 3607811) turns a held bus into a 2ms error return, proven 3/3. |
| `docs/bench-playbook.md:206-222` | §7 "Open": (1) "Meter, not code — software has nothing left to say about either IMU bus"; (2) common-mode question across two dead buses; (3) mule bat | All three closed 2026-08-13/14. Software answered it (pincensus). There were no dead buses. The 61-jump history is "gone for good" — stated in this same file at :19-22. |
| `docs/power-states.md:210-212` | §5a finding 7: "jh_power::system_off() still cuts the rail — and does it with pinMode(), the exact API DECISIONS #37 bans." | Half stale: converted to nrf_gpio_cfg(...H0H1...) in 216f75f (jh_power.cpp:265-266). It does still cut the rail, now as a documented single sanctioned transition. |
| `docs/power-states.md:183-186` | §5a finding 2: "jh_link.cpp advertises at 152.5 ms... The advertiser alone blows the STANDBY budget." | Fixed: Bluefruit.Advertising.setInterval(32, 1600) — 20ms fast / 1000ms idle (jh_link.cpp:470, commit 216f75f). |
| `docs/power-states.md:202-205` | §5a finding 5: "nothing ever calls Bluefruit.autoConnLed(false)" | It is called at jh_link.cpp:465 (commit 216f75f). |
| `docs/power-states.md:285-291` | §6 sequence: "1. Board #3 proven (soak ladder) — in progress. Re-run the drop ritual on it once healthy" | Board #3 is the backup; the OG is the product board (bench-playbook.md:14, commit 82bc30b) and already carries the 0.0257 calibration. The named next step targets the wrong board. |
| `docs/ble-dependability.md:45` | Failure-taxonomy table: "1. Byte integrity — do the bytes we queue arrive? **BROKEN**" | Fixed in code the same day this doc was written: per-connection retry, bounded at 8 passes, counted as tx_drops (jh_link.cpp:336-359, commit 216f75f). Correct status is built-unverified, not |
| `docs/ble-dependability.md:130-131` | Build order item 2: "Watch-side corruption gate. Pure Monkey C, no firmware coupling." — listed as work to do | Shipped 2026-08-11 (eb87382): Model.mc:180 _jumpIsCorrupt() rejects incomplete JUMP lines, glued JUMP+STATS, impossible values and best<height, with 17 ModelTest cases. plan.md:186 flags thi |
| `garmin/FIRST_COMPILE.md:140-142` | "OPEN BUG — corrupted values on the watch, cause NOT yet found. Status 2026-08-11: open." Leading hypothesis: Connect IQ drops notifications. | A firmware root cause with a line citation exists (ble-dependability.md:15-39, jh_link.cpp). This file was last touched 2026-08-10 and is three days behind the investigation. |
| `garmin/FIRST_COMPILE.md:189-193` | "Candidate hardening regardless of cause: the ingest path currently trusts any line that parses. A JUMP missing airtime_s/best_m, or an n that jumps b | Built the next day. Model.mc:180-215 implements exactly this, including the n-advance check, and counts rejections (rejectedCount()). The doc still proposes it as future work. |
| `garmin/README.md:16-17` | "Status: compiles clean and all 24 unit tests PASS in the simulator (2026-08-04)" | 44 (:test) functions today: ModelTest 17, ProtocolTest 14, LayoutTest 13. LayoutTest.mc was added 2026-08-11 (dcba0f9) and "test #9 found a real bug." |
| `garmin/README.md:21-22` | "You are M2: sideload to the real watch (§5 below) and scan for the real puck." | Sideloaded and live on an Epix Gen 2 since 2026-08-11: scan -> pair -> discover -> subscribe -> decode -> render all proven, real toss registered (FIRST_COMPILE.md:66-108). |
| `garmin/README.md:72-73, 150-151` | Build and test commands hardcode `-d instinct3solar45mm` as the target | The bench watch is an Epix Gen 2; garmin/jumpfield/bin/ contains JumpField-epix2.prg and the manifest carries <iq:product id="epix2"/>. Following the README as written builds a .prg that can |
| `docs/roadmap.md:184` | "Not yet signed off as M1: values on the glass are corrupt with a second BLE central subscribed" | Contradicts docs/garmin-datafield.md:6, which says "M1 (protocol core, simulator-only) met" and that M2 is the unsigned one. Two docs disagree about which milestone is open on the same evide |
| `docs/roadmap.md:215-216` | "Next on the Sense: battery solder-up, drop calibration, and the two-central test on-board." | All three done. Pigtail soldered (bench-playbook.md:14); drop cal a6e477d 2026-08-11; two centrals ran concurrently >1h on 2026-08-11 (SENSE_FIRST_BOOT.md:415-430). |
| `docs/roadmap.md:204` | "ALL-IN (owner, 2026-07-28): the XIAO nRF52840 Sense IS the v2 board — hardware + 500 mAh cell ordered." | The cell actually installed is 250 mAh (sense.md:138) — every runtime downstream of this halves. |
| `docs/roadmap.md:38-51` | Phase 2 checklist: all six boxes unchecked, including "Battery power" and "Waterproof capsule; bucket-test it empty first" | Battery is soldered on with working telemetry; the capsule was bought 2026-08-11 (Hammond 1551WHGY, BUILD.md:24). Neither box was ticked or annotated, so the roadmap reads as if nothing happ |
| `docs/sense.md:3-6` | "Status: ALL-IN (owner, 2026-07-28). Board + 500 mAh battery ordered, arriving within days. Written before first power-on" | Three boards have been on silicon since 2026-07-31. The status banner is 17 days and one entire bring-up out of date. |
| `docs/sense.md:14` | SENSE_FIRST_BOOT.md has "(21 items)" | It now runs to item 26 plus sections 16b through 16j — roughly 1,635 lines. |
| `docs/sense.md:327-348` | "§7 VERIFY at bring-up (answer on the bench, then edit this doc)" — a 10-item list, entirely unannotated | The doc's own stated convention was not followed. Items 1, 2, 3 and 9 were answered on silicon (SENSE_FIRST_BOOT items 14, 15, 4, 10); item 4 (nRF Connect DFU) was superseded by tools/otadfu |
| `docs/sense.md:291-303` | §5 power/runtime table headed "500 mAh means" — e.g. recording "~60-160 h" | 250 mAh installed. Every figure in the column is 2x optimistic. README:236 warns about this from outside; sense.md never fixed it, and the same 500 mAh appears at :36, :42, :122 and :340. |
| `firmware/SENSE_FIRST_BOOT.md:390` | Item 14 heading: "Bluefruit two-central mechanics — re-derived from source, never run against real centrals" | Its own body at :415 reads "TWO CENTRALS RAN FOR REAL, 2026-08-11." The heading is the pre-silicon claim; a skim of headings gets the wrong answer. |
| `firmware/SENSE_FIRST_BOOT.md:503` | Item 16b heading: "OTA DFU — the sealed box's only firmware path, and it is NOT yet trustworthy" | Its own body at :583 reads "GATE PASSED 2026-08-12 ~15:00" — two complete loops, checkpoint-verified, commit c4306d3. |
| `firmware/SENSE_FIRST_BOOT.md:1314` | Item 24 heading: "Battery telemetry ADC accuracy — built 2026-08-04, never checked against a meter" | Two meter points in its own body (:1361-1365): 3490mV meter vs 3390 ADC, 4160 vs 4050, plus a full TACQ sweep and a shipped fix. |
| `firmware/SENSE_FIRST_BOOT.md:1502` | Item 26 heading: "Gyro spin correction + self-calibrating lever arm — built 2026-08-10, ZERO silicon time" | Its own body at :1565 reads "STEP 1 DONE 2026-08-11 — the gyro is real": 3.1 dps rest bias driven to 0.5 dps, 257.8 dps peak on hand rotation. |
| `firmware/SENSE_FIRST_BOOT.md:1623-1624` | Out-of-scope list: "Battery telemetry... — firmware reports nothing about the battery yet." | Built 2026-08-04 and calibrated on silicon 2026-08-11; vbat_mv/batt_pct/chg ship on INFO and STATS (jh_power.cpp), the web app renders a battery pill, and the watch draws a puck-battery glyp |
| `firmware/SENSE_FIRST_BOOT.md:1629-1631` | Out-of-scope list: "Nordic DFU / BLEDfu... wireless update is unbuilt." | The same file's §16b records the OTA gate PASSED on 2026-08-12 with two verified back-to-back wireless loops, and the bootloader itself was upgraded over the air (ec4f403). |
| `docs/gyro-sim-plan.md:33-35` | "The Sense board's LSM6DS3TR-C is a 6-axis part; the gyro is on-board but UNUSED (firmware reads accel only). The plan: enable the gyro..." | The gyro has been on since 2026-08-10: CTRL2_G=0x5C at 208Hz/±2000dps (lsm6ds3_min.h:146), LPF enabled, bias EMA (gyro_bias.h), and ω²r subtraction inlined at jump_detector.h:108. |
| `docs/algorithm.md:78-79` | "overshoots by only 1.00-1.07x (vs the kite's 2.31x), with a Monte-Carlo physics-floor RMSE of ~4.2 cm" | 4.2 cm is the superseded N=5000 figure. The N=200,000 rerun gives 4.6 cm (DECISIONS #30, wing-ballistic-sim.md:57, README:125). algorithm.md is the doc README points at for "the physics." |
| `sim/experiments/RESULTS.md:27,45` | E1 "100% detected"; "frac_detected = 1.000 (3072/3072)"; E2 "RMSE 4.2 cm" | The 2026-08-11 rerun killed "100% detected" — 5 silent misses in 200,000, all at the 0.35 g gate (DECISIONS #30). wing-ballistic-sim.md:56 marks E2 "superseded, see E2′"; RESULTS.md carries  |
| `docs/data-pipeline.md:84-90` | Capture/labeling procedure: film at 240fps, count airborne frames, derive "true height from counted airtime frames" via h = g·T²/8 | docs/plan.md:63-73 (2026-08-14) rules this ground truth CIRCULAR — it scores the formula under test against a label built with the same formula, and passes whether or not wings are ballistic |
| `docs/garmin-datafield.md:312-317` | §7: "Two concurrent BLE centrals — DONE (firmware v0.4.2)... the app re-advertises after each connect while getConnectedCount() < 2... NimBLE's own de | That is the ESP32/NimBLE path. The Sense runs Bluefruit.begin(2, 0) (jh_link.cpp:417). FIRST_COMPILE.md:163-168 warns explicitly against blaming the wrong platform's link layer, which is the |
| `docs/garmin-datafield.md:248-249` | §5.5: "One developer-data UUID (constant in FitOut.mc)" | FIRST_COMPILE.md:399-412 found the confirmed createField() signature takes no UUID; DEVELOPER_DATA_ID is documentation-only. The spec still reads as if it is wired up. |
| `docs/garmin-datafield.md:154-167` | §5.1: "Primary target device: Garmin Instinct 3 Solar (the rider's watch) — API 5.1, 176x176 semi-octagon MIP", with layout, dimming and font decision | The rider's watch is an Epix Gen 2, 416x416 round AMOLED; the Instinct belongs to his brother. The layout was rebuilt with chord math for the round screen (FIRST_COMPILE.md:119-124) after th |
| `BUILD.md (whole file)` | "the hardware-day runbook" — shopping list, MPU-6050 header soldering (:114-127), FireBeetle wiring table, wizard flow, partition-upgrade warning (:28 | Every word describes the frozen FireBeetle/ESP32 build, while README:218 says "Build this one — Seeed XIAO nRF52840 Sense." No Sense build runbook exists anywhere in the repo. DECISIONS #27  |
| `docs/hardware.md:77-79` | "Accelerometer range: set ±8 g (or ±16 g). ...±8 g keeps free-fall resolution good while capturing landings." | The Sense ships ±16 g by deliberate decision (DECISIONS #25, lsm6ds3_min.h CTRL1_XL=0x54). README:240 sends readers here for "full BOM, wiring, power budget", and the whole page is ESP32-era |
| `DECISIONS.md:69-70 (#32, #33)` | #32: the off-path back-feed was "proven 'both directions' on silicon... the very day before the mule's sensor stopped ACKing". #33: "The Puck, a fresh | #37 (:74), five rows later, establishes that no board was ever damaged and drive strength explains every symptom including the intermittency. #32/#33 carry no superseded marker, so read alon |
| `firmware/src/main.cpp:424 (shipped `help` text)` | "# commands: help / stats / jumps / trace / dump / clear / selftest / revive / i2cdiag / info / off / dfu / uf2 / fakejump / mount / format" | Omits `pincensus`, which exists at main.cpp:512 and which DECISIONS #38, xiao-hardware-truth.md:91 and bench-playbook.md:129 all designate as THE first diagnostic to run before any hardware  |

