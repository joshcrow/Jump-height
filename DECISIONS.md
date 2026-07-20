# v1 design decisions

The decisions behind the v1 build, and *why* — so future-you (and anyone else
building this) knows what was chosen on purpose vs. what's just incidental.

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | **What v1 is** | A puck that **records** a session on one board; read the jumps on land afterward. Personal, one board. Open-source the files as you go. | Fastest path to the real unknown — *does the airtime method work on foil jumps?* Don't let "product for others" gate the first number. |
| 2 | **How you read it** | Review afterward over USB. No live/on-water display. | You can't usefully read anything mid-wing anyway; Woo itself just syncs on land. |
| 3 | **Accuracy target** | Within ~10%, proven **once** against slow-mo video, then bake in a correction factor. | 10% is invisible in real life; the method's own physics (drag, wave landings) limits you there regardless. Accuracy comes from the video check, not an expensive sensor. |
| 4 | **Sensor** | MPU-6050 (bought 4). Only the accelerometer is used. | Cheapest, best-documented, clears the 10% target. Using only \|acceleration\| makes mounting orientation irrelevant. |
| 5 | **The maths** | Airtime method: `height = g × airtime² ÷ 8`. Detect takeoff by free-fall (\|a\|≈0), landing by the spike. | Avoids double-integration drift, which is unusable on cheap IMUs. |
| 6 | **Board + power** | DFRobot **FireBeetle ESP32** (built-in LiPo charging) + small single-cell LiPo. Charge & download over USB when the capsule is open. | Already owned; built-in charging removes a whole class of wiring/safety mistakes; FireBeetle is low-power for the later sleep upgrade. |
| 7 | **On/off** | Motion-activated recording. Simple "record only while moving" now; real low-power deep-sleep later. | No hole needed in a sealed capsule. Simple version keeps logs clean; deep-sleep (for battery life) is a later optimization. |
| 8 | **What it saves** | Continuous **50 Hz trace of \|a\| while moving** + a per-jump list, on built-in flash. | Re-tunable offline against the same sim we already tested, and it captures **missed** jumps too (a plain per-jump clip wouldn't). Memory card is the escape hatch if flash runs short. |
| 9 | **Waterproofing** | Bought screw-top waterproof **capsule**. Bucket-test empty every time. Must float + be tethered. | Buying beats building for speed; one purchase settles waterproofing, charging, data offload, and storage at once. |
| 10 | **Mounting** | Adhesive **GoPro-style mount**, hard smooth patch of center deck. Alcohol-prep, 24 h cure, tethered. | Center = cleanest signal. Adhesive = no drilling. Tether + float because a lost puck is the classic ending. |
| 11 | **Test plan** | **Desk → dry land (filmed) → water (filmed).** | Each step fails cheaply and catches its own class of bug *before* the ocean, where mistakes are expensive. |

## Deliberately deferred to later phases

- **Real deep-sleep** power management (multi-session battery life). v1 charges after each outing.
- **Live phone app / BLE** readout. v1 reads over USB. (Firmware has a disabled BLE hook.)
- **microSD card** for unlimited logging. v1 uses built-in flash (~30 min of moving-time trace, capped).
- **Better IMU / GPS / custom PCB.** All Phase 4.
- **Air-drag / wave-landing correction** beyond a single global factor.

## Known v1 limitations (accepted on purpose)

- Simple motion-gating keeps logs clean but doesn't save power yet → **seal near launch, charge after**.
- Trace logging caps at ~30 min of *moving* time to protect flash; the jump list is always kept.
- A drop off a ledge reads as a "jump" (it's really fall height) — fine for this use.
