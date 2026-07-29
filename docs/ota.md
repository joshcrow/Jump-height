# Over-the-air firmware updates — RETIRED SPEC *(tombstone, 2026-07-29)*

**Status: retired unbuilt, superseded by the Sense pivot.** This file was
the full ESP32 OTA design (dual app slots + otadata, bootloader rollback
gated on the boot self-test, a BLE binary transfer protocol driven from
Bluefy, CI's Pages build as the update server, and a WiFi upload doorway
tied to the also-retired Phase 3.5). None of it was built, and none of it
applies to the v2 board: the XIAO nRF52840 Sense updates via **Nordic
DFU** through the stock Adafruit bootloader + the free nRF Connect app,
with UF2 drag-drop as the cable path — see [sense.md](sense.md) §3.3,
including why browser-based DFU is off the table (Web Bluetooth
blocklists the legacy DFU service).

**What survived and shipped** — §4.5 of the old spec, the binary trace
v2 format: one-second blocks of u32 t0_ms + u8 count + count × u16
milli-g + CRC-8, ~2 bytes/sample, CSV-identical on the wire. It is live
on the Sense today (`firmware/include/trace_codec.h`,
`firmware/src/platform/nrf52/jh_store.cpp`, Python mirror
`sim/trace_codec.py`, parity- and torture-tested; DECISIONS #24/#26),
turning 2 MB of QSPI into ~5 hours of moving-time trace.

**One idea worth keeping in mind for the DFU era**: CI already publishes
versioned firmware to Pages; a small `version.json` (version + one-line
changelog) would let the web app say "an update exists" even though the
install itself happens in nRF Connect. Unscoped, deliberately.

The complete original specification (design, milestones O1–O6, risks,
storage math) is preserved in git history — any commit up to `bf4d2aa`,
e.g. `git show bf4d2aa:docs/ota.md`.

**If an ESP32-class board ever returns to the roadmap, start from the
historical spec, not from scratch — it was decision-complete.**
