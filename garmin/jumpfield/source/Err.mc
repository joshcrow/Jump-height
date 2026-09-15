// Err.mc
//
// One number that says how the app was hurting, carried out of the watch in
// the only channel that ever reaches us.
//
// WHY THIS FILE EXISTS (2026-09-15)
// --------------------------------
// The watch's own crash/diagnostic log (GARMIN/APPS/LOGS/CIQ_LOG.YML) never
// syncs to Garmin Connect — reading it needs the watch on a USB cable, and
// the rider is not the developer. So the ONLY diagnostics that reach this
// repo are the bytes this app itself writes into the FIT file the rider
// saves and syncs. Measured the same day on the rider's real activities:
// on 2026-09-14 the record definition lost every developer field at
// 20:58:31 Z and 1,688 of 2,877 records (59 min) carried none, and NOTHING
// in the file says why (docs/garmin-corpus-2026-09-15.md, finding 3).
//
// Every catch in this codebase is a deliberate BARE `catch (ex)` — see the
// header of JumpFieldView.mc and PuckLink.mc for the two silicon incidents
// that made that rule. A bare catch keeps the field alive, which is right,
// and throws the evidence away, which is not. These codes are the evidence:
// each existing catch site gets a distinct small number, and Model.State
// keeps the STICKY MAXIMUM for the RUN OF THE APP. No new catches were
// added to manufacture codes — every code below labels a catch that was
// already there (the two exceptions are the health write and the object-
// store access this change itself introduces, both of which really can
// throw).
//
// SEMANTICS: err_code is a MAXIMUM, not a log. A value of 14 means "class
// 14 happened, and nothing numbered higher did" — classes below 14 may also
// have happened and are not recoverable from the number.
//
// SCOPE: per RUN OF THE APP, not per activity — stated exactly, because the
// first draft said "activity" in three places while the code cleared it in
// none (adversarial review, 2026-09-15). Nothing in beginActivity() or
// endActivity() touches it, and that is deliberate: this version overrides
// onTimerReset precisely because one app instance CAN span two activities,
// and a failure in the first activity of such a run is still a fact about
// the field that is writing the second one. So an err_code of 14 in a file
// means "somewhere in the app run that produced this file, class 14 was
// caught" — possibly before this activity's timer ever started. prev_err
// carries the same number one run further, into the next file.
//
// Codes are grouped by subsystem and, within the file as a whole, ordered
// roughly by how much of the product each failure costs, so the max names
// the worst class seen.
//
//   0        nothing was caught in this run
//   1 -  5   settings / UI niceties        (a degraded glance at worst)
//   6 - 14   BLE plumbing                  (link slower, stuck, or dead)
//  15 - 16   wire decode                   (lines lost; data missing)
//  17 - 20   FIT contributor               (evidence lost, permanently)
//  21 - 23   object store / health write   (this diagnostic channel itself)
//
// The table is duplicated in docs/watch.md; CLAUDE.md §4 — an identifier
// without a lookup entry is a rediscovery waiting to happen. Change one,
// change both, in the same commit.
//
// Never renumber a code that has shipped: a saved FIT file on the rider's
// watch is a permanent record whose meaning is this table.
module Err {

    // ---- 1-5: settings and UI. The field still works. ----

    // Application.Properties.getValue("puckName") threw.
    const E_PROP_PUCKNAME = 1;
    // Application.Properties.getValue("unitOverride") threw.
    const E_PROP_UNIT = 2;
    // Application.Properties.getValue("vibrateOnJump") threw.
    const E_PROP_VIBE = 3;
    // Attention.vibrate() threw (device/app-type refuses it).
    const E_VIBRATE = 4;
    // DataField.getObscurityFlags() threw; insets fall back to zero.
    const E_OBSCURITY = 5;

    // ---- 6-14: BLE. The link is degraded, stuck, or absent. ----

    // Ble.Device.getName() threw — the header's puck id goes blank.
    const E_BLE_NAME = 6;
    // setScanState(OFF) threw while scheduling a backed-off rescan.
    const E_BLE_SCAN_OFF = 7;
    // setScanState(OFF) threw in stop() (the activity is ending anyway).
    const E_BLE_STOP = 8;
    // unpairDevice() threw while abandoning a stalled connect (F-12).
    const E_BLE_UNPAIR = 9;
    // The one `stats\n` write threw — no reseed until the next connect.
    const E_BLE_STATS_WRITE = 10;
    // setScanState(SCANNING) threw — no scan is running, so no puck will
    // ever be found until the next backoff tick tries again.
    const E_BLE_SCAN_ON = 11;
    // pairDevice() threw — this candidate puck cannot be connected to.
    const E_BLE_CONNECT = 12;
    // The CCCD requestWrite threw — notifications were never enabled, so
    // the link reaches SUBSCRIBING and no data ever arrives.
    const E_BLE_SUBSCRIBE = 13;
    // setDelegate/registerProfile threw in start(): this device or app type
    // has no BLE central at all. The link goes DEAD and stays there; the
    // glass shows NO BLE. link_state (field 10) reads 6 for the same reason.
    const E_BLE_REGISTER = 14;

    // ---- 15-16: the wire. Lines are being lost. ----

    // StringUtil.convertEncodedString threw on a notification chunk. This is
    // the exact call that killed the whole field on silicon on 2026-08-11
    // (PuckLink._ingest's comment); seeing it again means the decode is
    // failing but the bare catch is holding.
    const E_DECODE = 15;
    // LineReader.feed / Protocol.parseKV / Model.onLine threw on a line.
    const E_PARSE = 16;

    // ---- 17-20: FitContributor. Evidence is being lost permanently. ----

    // recordBaro()/updateBaroSrc() threw during a tick.
    const E_FIT_BARO = 17;
    // createField() refused one of the baro fields (ids 4-6) at startup.
    const E_FIT_BARO_FIELDS = 18;
    // createField() refused one of the health fields (ids 9-13) at startup.
    // If this fires, err_code itself has nowhere to be written: it reaches
    // us only as prev_err in the next activity.
    const E_FIT_HEALTH_FIELDS = 19;
    // The whole FitOut constructor threw: there are NO developer fields in
    // this activity at all — not jump_height, not the health block. Like
    // code 19, this can never appear in err_code in the file it happened in.
    // It reaches us only as prev_err in the NEXT activity, which is the
    // entire reason prev_err exists.
    const E_FIT_INIT = 20;

    // ---- 21-23: the diagnostic channel itself. ----

    // Application.Storage.getValue threw at startup — prev_err is unknown,
    // and is written as 0 rather than guessed at.
    const E_STORE_READ = 21;
    // Application.Storage.setValue threw — the next run cannot be told what
    // happened in this one, so ITS prev_err is stale and this code is the
    // only warning of that. The first throw also disables the store for the
    // rest of the run (JumpFieldView._errStoreDown): one failed write, never
    // a per-tick retry, and never a silent pretence that it succeeded.
    const E_STORE_WRITE = 22;
    // The per-tick health write itself threw (getSystemStats, or a setData
    // on one of ids 9-13). The diagnostics are the thing that is broken.
    const E_HEALTH = 23;
}
