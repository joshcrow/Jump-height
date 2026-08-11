#!/usr/bin/env python3
"""otadfu — flash the puck OVER THE AIR from the laptop (no cable, no phone).

The sealed-box story (docs/sense.md §3.3): once the capsule is closed, the
ONLY firmware path is the Adafruit bootloader's Nordic *legacy* OTA DFU.
The web app can never do this — browsers blocklist the Nordic DFU service
UUID in Web Bluetooth — and macOS has no working legacy-DFU CLI (Nordic's
own nrfutil speaks BLE only through their dongle hardware; adafruit-nrfutil
is serial-only). nRF Connect on a phone works, but for the bench the phone
is one more hand you don't have.

So this speaks the legacy DFU protocol directly over CoreBluetooth via
bleak: the same stack blecmd.py already uses. ~150 lines against a
protocol frozen since nRF SDK 11.

Flow (one command):

    python3 tools/otadfu.py                 # flash the current pio build
    python3 tools/otadfu.py path/to.zip     # flash a specific package

  1. If a device named `JumpHeight` is advertising, send it `dfu\n` over
     NUS first (the app's own command, firmware >= this commit) — it
     reboots into the bootloader. If `AdaDFU` is already advertising
     (e.g. a previous transfer died), skip straight to it: this doubles
     as the RECOVERY tool, which matters because in OTA DFU mode the
     board exposes NO USB at all (verified on silicon 2026-08-11 —
     no serial port, no UF2 drive; the radio is the only way in).
  2. Connect to `AdaDFU`, run the legacy DFU procedure:
     start(app) -> sizes -> init packet (.dat) -> stream .bin in 20-byte
     writes with packet-receipt flow control -> validate -> activate.
  3. The bootloader reboots into the new app; we rescan and confirm
     `JumpHeight` is back on the air.

Single-bank caveat, unchanged from §3.3: a transfer that dies mid-way
leaves the board waiting in the bootloader. That is exactly the state this
tool starts from (step 1's AdaDFU branch), so the recovery from a failed
run of this tool is... running it again.

The .zip is adafruit-nrfutil's own package format (manifest.json + .bin +
.dat), which the normal `pio run` build already produces.

SPDX-License-Identifier: MIT
"""

import asyncio
import json
import os
import struct
import sys
import time
import zipfile

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("bleak not installed — pip3 install bleak")

# Nordic legacy DFU service (nRF SDK 11 era; what the Adafruit bootloader runs).
DFU_SERVICE = "00001530-1212-efde-1523-785feabcd123"
DFU_CONTROL = "00001531-1212-efde-1523-785feabcd123"   # write + notify
DFU_PACKET  = "00001532-1212-efde-1523-785feabcd123"   # write-without-response

# NUS, for the app-side `dfu` trigger.
NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX      = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"

# Control-point opcodes (legacy protocol).
OP_START_DFU          = 0x01
OP_INITIALIZE         = 0x02
OP_RECEIVE_IMAGE      = 0x03
OP_VALIDATE           = 0x04
OP_ACTIVATE_RESET     = 0x05
OP_SYS_RESET          = 0x06
OP_PKT_RCPT_NOTIF_REQ = 0x08
OP_RESPONSE           = 0x10
OP_PKT_RCPT_NOTIF     = 0x11
IMAGE_APPLICATION     = 0x04
RESP_SUCCESS          = 0x01

PKT_RCPT_INTERVAL = 10   # bootloader confirms every N packets (flow control)
CHUNK = 20               # legacy DFU streams the image in 20-byte writes

DEFAULT_ZIP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                           "firmware", ".pio", "build",
                           "xiaoblesense_adafruit", "firmware.zip")


def load_package(path):
    """Pull app .bin + init .dat out of an adafruit-nrfutil package."""
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read("manifest.json"))
        app = manifest["manifest"]["application"]
        return z.read(app["bin_file"]), z.read(app["dat_file"])


async def find(name, seconds=10.0):
    return await BleakScanner.find_device_by_filter(
        lambda d, adv: (adv.local_name or d.name or "").lower() == name.lower(),
        timeout=seconds)


async def trigger_app_dfu():
    """If the app is running, ask it to reboot into the bootloader."""
    dev = await find("JumpHeight", 6.0)
    if dev is None:
        return False
    print(f"app is up ({dev.address}) — sending `dfu`")
    try:
        async with BleakClient(dev, timeout=15.0) as c:
            await c.write_gatt_char(NUS_RX, b"dfu\n", response=True)
            await asyncio.sleep(1.0)   # farewell + reboot
    except Exception:
        pass  # the reboot kills the connection mid-write; that's success
    return True


async def dfu(zip_path):
    image, init_packet = load_package(zip_path)
    print(f"package: {os.path.basename(zip_path)}  app image {len(image)} bytes")

    # Step 1 — get the bootloader on the air (or find it already there).
    boot = await find("AdaDFU", 5.0)
    if boot is None:
        if not await trigger_app_dfu():
            sys.exit("neither AdaDFU nor JumpHeight is advertising — is the puck awake?")
        for _ in range(6):
            boot = await find("AdaDFU", 5.0)
            if boot:
                break
    if boot is None:
        sys.exit("app accepted `dfu` but AdaDFU never appeared — tap reset and retry")
    print(f"bootloader: AdaDFU ({boot.address})")

    responses = asyncio.Queue()
    acked = 0

    def on_notify(_, data: bytearray):
        nonlocal acked
        if data and data[0] == OP_PKT_RCPT_NOTIF:
            acked = struct.unpack("<I", data[1:5])[0]
        else:
            responses.put_nowait(bytes(data))

    async def expect(op):
        r = await asyncio.wait_for(responses.get(), 30.0)
        if len(r) < 3 or r[0] != OP_RESPONSE or r[1] != op or r[2] != RESP_SUCCESS:
            raise RuntimeError(f"bootloader refused op {op:#x}: {r.hex()}")

    async def clean_reset(dev):
        # A bootloader mid-way through a dead transfer answers START_DFU with
        # INVALID_STATE (0x02). Opcode 0x06 is a plain system reset — with the
        # app image invalid it boots straight back into DFU, state cleared.
        # (If USB happens to be attached, a plain reset lands in UF2/serial
        # mode instead and AdaDFU never reappears — the error below says so.)
        print("bootloader in a stale DFU state — resetting it clean")
        try:
            async with BleakClient(dev, timeout=15.0) as rc:
                await rc.write_gatt_char(DFU_CONTROL, bytes([OP_SYS_RESET]), response=True)
        except Exception:
            pass  # reset kills the link; expected
        await asyncio.sleep(3.0)
        for _ in range(6):
            d = await find("AdaDFU", 5.0)
            if d:
                return d
        raise RuntimeError(
            "bootloader did not come back as AdaDFU — if USB is plugged in it "
            "reset into UF2/serial mode instead; recover with "
            "`pio run -d firmware -e xiaoblesense_adafruit -t upload`")

    async def session(dev):
        """One connection, the whole legacy-DFU procedure. Raises on refusal."""
        nonlocal acked
        acked = 0
        async with BleakClient(dev, timeout=20.0) as c:
            await c.start_notify(DFU_CONTROL, on_notify)

            # start(app) + image sizes (softdevice, bootloader, app)
            await c.write_gatt_char(DFU_CONTROL, bytes([OP_START_DFU, IMAGE_APPLICATION]), response=True)
            await c.write_gatt_char(DFU_PACKET, struct.pack("<III", 0, 0, len(image)), response=False)
            await expect(OP_START_DFU)
            print("bootloader accepted image size")

            # init packet (the .dat: device/app ids + CRC)
            await c.write_gatt_char(DFU_CONTROL, bytes([OP_INITIALIZE, 0x00]), response=True)
            await c.write_gatt_char(DFU_PACKET, init_packet, response=False)
            await c.write_gatt_char(DFU_CONTROL, bytes([OP_INITIALIZE, 0x01]), response=True)
            await expect(OP_INITIALIZE)
            print("init packet accepted")

            # flow control: a receipt every PKT_RCPT_INTERVAL packets
            await c.write_gatt_char(DFU_CONTROL,
                bytes([OP_PKT_RCPT_NOTIF_REQ]) + struct.pack("<H", PKT_RCPT_INTERVAL), response=True)
            await c.write_gatt_char(DFU_CONTROL, bytes([OP_RECEIVE_IMAGE]), response=True)

            t0 = time.time()
            sent = 0
            window = CHUNK * PKT_RCPT_INTERVAL   # bytes in flight per receipt
            for off in range(0, len(image), CHUNK):
                await c.write_gatt_char(DFU_PACKET, image[off:off + CHUNK], response=False)
                sent += 1
                # HARD flow control. `acked` is the byte count straight from
                # the bootloader's receipt notification — it IS bytes, not
                # packets. (First attempt compared `acked * CHUNK`, which made
                # the window look ~20x emptier than it was: the wait never
                # engaged, 157 KB firehosed at "88 KB/s" into a bootloader
                # that flash-writes per packet, and it errored 0x06 at 98%.
                # The receipts are the ONLY thing enforcing real pace.)
                if sent % PKT_RCPT_INTERVAL == 0:
                    deadline = time.time() + 30.0
                    while acked < off + CHUNK - 2 * window:
                        if time.time() > deadline:
                            raise RuntimeError(f"receipts stalled at {acked}/{off} bytes")
                        await asyncio.sleep(0.005)
                if sent % 500 == 0:
                    pct = 100.0 * off / len(image)
                    rate = acked / max(time.time() - t0, 1e-9) / 1024
                    print(f"  {pct:5.1f}%  {rate:5.1f} KB/s (acked {acked})", flush=True)

            await expect(OP_RECEIVE_IMAGE)
            print(f"image transferred in {time.time()-t0:.0f}s")

            await c.write_gatt_char(DFU_CONTROL, bytes([OP_VALIDATE]), response=True)
            await expect(OP_VALIDATE)
            print("image validated")

            try:
                await c.write_gatt_char(DFU_CONTROL, bytes([OP_ACTIVATE_RESET]), response=True)
            except Exception:
                pass  # activation resets the link mid-write; that's the success path

    try:
        await session(boot)
    except RuntimeError as e:
        if "refused op 0x1" not in str(e):
            raise
        boot = await clean_reset(boot)
        await session(boot)

    print("activated — bootloader is flashing and rebooting")

    # Step 3 — confirm the app came back.
    for _ in range(8):
        dev = await find("JumpHeight", 5.0)
        if dev:
            print("JumpHeight is back on the air ✅  OTA DFU complete")
            return
    print("WARNING: JumpHeight not seen yet — give it a few seconds and rescan "
          "(python3 tools/blecmd.py --scan)")


if __name__ == "__main__":
    zp = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ZIP
    if not os.path.exists(zp):
        sys.exit(f"no package at {zp} — run `pio run -d firmware -e xiaoblesense_adafruit` first")
    asyncio.run(dfu(zp))
