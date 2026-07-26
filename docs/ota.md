# Over-the-air firmware updates — scope & build spec

**Status:** scoped, not started. Build AFTER water validation (same gate as
everything; nothing here affects measurement). Companion to Phase 3.5 in
[roadmap.md](roadmap.md) — the two share a partition-map change and should
ship as one "connectivity epoch" so storage is wiped exactly once.

## 1. The one-sentence product

Firmware updates happen from the phone in the beach parking lot — Bluefy,
one button, two minutes, no Mac, no cable — and a failed update can never
brick the board.

## 2. User stories

- **US1 — the button.** As the owner, when the app says a newer firmware
  exists, I tap "Update over Bluetooth", watch a progress bar, the device
  reboots itself, and the app reconnects showing the new version. My
  stored sessions and calibration are untouched.
- **US2 — unbrickable.** As the owner, if the transfer dies mid-way (walked
  off, phone slept) or the new build is bad, the device keeps/returns to
  the old firmware and the app tells me plainly what happened. The USB
  cable remains the recovery path of last resort, forever.
- **US3 — informed consent.** As the owner, before updating I see current
  vs. latest version and a one-line changelog, not a mystery box.
- **US4 — the stranger.** As a DIY builder, I cable up exactly once (the
  web flasher, first install); every update after that is wireless.
- **US5 — later, with Phase 3.5.** As any iPhone user in hotspot-sync mode,
  plain Safari on the device's own page can upload firmware too (no Bluefy
  needed). Same machinery, second doorway.

## 3. Why this is possible now

- CI already builds every push's firmware and publishes it to GitHub Pages
  next to the web app — the update *server* already exists.
- The web app already runs on the iPhone against the device (Bluefy/NUS).
- Calibration already lives in NVS (survives any app swap) — decided in
  DECISIONS #22 partly *for* this future.

## 4. Design

### 4.1 Partition epoch (the one-time breaking change)

Today: factory 1.5 MB + littlefs 2.4 MB (no OTA — DECISIONS #21). OTA
needs two app slots + otadata. New map (4 MB):

    nvs      20 KB   (calibration — untouched, survives)
    otadata   8 KB   (which slot boots; dual-bank, power-loss safe)
    phy       4 KB
    ota_0    ~1.31 MB (0x150000)
    ota_1    ~1.31 MB (0x150000)
    littlefs ~1.31 MB (≈ 3.5+ HOURS of moving-time trace — because the same
                       epoch switches trace storage to binary, §4.5; today's
                       CSV-on-flash format is what made 2.4 MB mean only
                       ~45 min)

Slot size is set by the REAL ceiling: measure the Phase 3.5 WiFi+BLE build
first (today's BLE-only app is 672 KB; WiFi typically adds 400–500 KB) and
freeze slots at measured + ~25% headroom. If it doesn't fit 1.31 MB, fall
back to 2 × 1.5 MB and accept ~20 min of trace (jump list is never at
risk — it's tiny). The "factory tiny-updater + one big slot" pattern was
considered and rejected: two firmwares to maintain for ~500 KB of storage.
**Upgrade note:** installing this epoch re-partitions → storage formats
once. `sync` first; calibration (NVS) survives.

### 4.2 Safety model (non-negotiable, in this order)

1. esp_ota writes the INACTIVE slot; otadata flips only after the full
   image passes its SHA-256 + magic checks. Power loss mid-transfer:
   old slot still boots. This is stock ESP-IDF behavior — use it, don't
   reinvent it.
2. **Rollback armed on first boot** (bootloader app-rollback enabled): the
   new image must prove itself before becoming permanent. The proof is the
   machinery we already trust: boot reaches READY and the power-on
   self-test doesn't hard-fail → firmware calls
   esp_ota_mark_app_valid_cancel_rollback(). Crash/boot-loop before that →
   bootloader reverts to the previous slot automatically.
3. The updated device announces itself: `# updated to v0.5.1 (rollback
   cleared)` — and after a rollback: `# update to v0.5.1 FAILED boot
   validation — running v0.5.0`. The app surfaces both.
4. Signed images: out of scope for v1 (hobby threat model; transfer is
   already integrity-checked). Noted for a future with strangers' boards.

### 4.3 Transfer protocol (BLE first — iPhone-complete via Bluefy)

Extends the existing line protocol; commands remain human-readable, the
payload does not (base64 would inflate 700 KB by a third):

    ota begin size=<bytes> sha256=<hex>     → OK ota | ERR ota_*
      (device allocates esp_ota handle; RX switches to BINARY mode:
       everything arriving on NUS RX is image bytes until `size` reached
       or a 10 s stall aborts)
    — binary stream, device acks progress every 16 KB:
    OTA n=<bytes_received>                   (drives the app's progress bar)
    — at size reached: device verifies sha256 + esp_ota_end:
    OTA done ok=1                            → then `ota apply` reboots
    ota abort                                → any time; discards cleanly

Resume support v1: none — an interrupted transfer restarts (2 minutes,
acceptable). The stall-abort guarantees the device always returns to
normal command mode. Sampling/detection PAUSE during transfer (update on
the beach, not mid-session; the device says so).

Throughput reality: iPhone MTU ≈185, write-without-response — expect
20–60 KB/s through Bluefy ⇒ ~700 KB in 15–40 s, worst case a couple of
minutes. Fine for a parking lot.

### 4.4 Web app UX

Connect tab "Firmware" card (appears when connected): device version (from
INFO) vs. latest (fetch `version.json` from Pages — CI stamps version +
one-line changelog; Pages serves CORS-friendly same-origin anyway) →
"Update over Bluetooth" → progress (bytes + %; sized by `ota begin`) →
"verifying… rebooting…" → auto-reconnect → verdict line (US2/US3 wording
above). Disconnect mid-transfer → honest status + "start again" (no
zombie states — same discipline as sync/bench flows).

### 4.5 Binary trace v2 (ships in the same epoch — the capacity answer)

Today the trace is stored on flash as literal text ("123.456,1.023\n",
~15 bytes/sample) — the real reason 2.4 MB only held ~45 minutes. Real
sessions are 1–2 h on the water, so the epoch fixes the format, not just
the partition:

- **Storage**: one-second blocks — u32 t0 (ms) + u8 count + count × u16
  magnitude in milli-g. ≈ 2 bytes/sample ⇒ ~360 KB per hour of MOVING
  time (idle costs nothing). 1.31 MB ⇒ ~3.6 h; a u8 variant (~31 mg
  steps, still 10× finer than any threshold) doubles that to ~7 h if a
  marathon ever demands it.
- **Wire compatibility**: `trace`/`dump` stream the SAME CSV as today —
  the device converts blocks to text on the fly (dumps already pause
  sampling, so the CPU is free). CLI sync, web app, autopsy, replay:
  zero changes. STATS keeps `trace_bytes` as stored bytes and gains
  `trace_csv_est=` so clients still size downloads honestly.
- **Fidelity**: milli-g resolution vs thresholds at 0.35 g / 2.5 g and
  Tier A classifiers that operate on 1 s windows — nothing downstream
  can tell the difference; the parity test (C++ vs Python on the same
  data) still gates it.
- Net vs today: OTA takes half the partition, the format gives back
  ~8× — long-session capacity IMPROVES ~5× in the same release.

### 4.6 The WiFi doorway (arrives free with Phase 3.5)

Hotspot mode adds a plain HTTP `/update` upload endpoint behind the same
esp_ota + rollback core. iPhone Safari on the device's own page fetches
the latest bin from Pages **via cellular** (iOS keeps internet on cellular
while joined to an internet-less hotspot; Pages sends `ACAO: *`) and posts
it locally at WiFi speed (seconds). Same safety, second doorway, zero
Bluefy dependency — this becomes the stranger-friendly default once 3.5
ships.

## 5. Milestones

- **O1 — the epoch.** New partitions + rollback config; upgrade via cable;
  prove: normal boot, self-test-gated validity mark, deliberate bad-image
  rollback (flash a build that bricks on purpose to watch it revert).
  This milestone is CABLE-ONLY by design.
- **O2 — firmware receive path.** `ota begin/abort` + binary RX mode +
  esp_ota plumbing; fake-device mirror for the command surface (not the
  binary blast); bench: update over BLE from desktop Chrome first.
- **O3 — app updater UI + CI version manifest.** version.json + changelog
  stamping in the Pages workflow; the Firmware card; Playwright tests
  against the mock (command framing + UX states; binary path is
  hardware-tested).
- **O4 — abuse soak.** ≥20 consecutive OTA cycles; kill power at 25/50/
  90%/during-verify; kill Bluefy mid-transfer; confirm every path lands on
  a booting, self-testing board. THE milestone that earns trust.
- **O5 — ship.** BUILD.md + web copy: cable's remaining jobs shrink to
  first-flash, recovery, charging, bulk sync. DECISIONS entry recording
  the storage trade.
- **O6 (with Phase 3.5) — HTTP upload doorway.**

## 6. Risks, stated

- **Storage capacity actually rises** (~45 min → ~3.5 h of moving time)
  because binary trace (§4.5) ships in the same epoch — but that adds a
  storage-format change to the epoch's blast radius; the CSV-on-the-wire
  compatibility layer and the C++/Python parity suite are what contain it.
  Jump list unaffected in every scenario.
- **A bad release now reaches the board wirelessly.** Rollback (O1) and
  the soak (O4) exist precisely for this; the user's board is also the
  only test fleet, so O4 is not skippable.
- **Bluefy long-transfer flakiness** — mitigated by restart-not-resume,
  stall-abort, and (later) the WiFi doorway.
- **One-time storage wipe at the epoch** — sync first; announced loudly in
  release notes and by the flasher.

## 7. Explicitly out of scope

- Signed/encrypted images, fleet management, delta updates.
- Updating FROM the Garmin watch (phone owns updates).
- Removing the cable path — the web-serial flasher remains the first-flash
  and disaster-recovery tool forever.
