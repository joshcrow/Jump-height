<!-- PENDING WORK, not yet implemented. Written 2026-09-12 after the owner read the
     live page and called the copy "kinda bad". Implementation was started and stopped
     by a rate limit before any edit landed. Next session: implement this against
     web/sync/ with NO behaviour change (verification, gates, reconnect untouched;
     diagnostics still written to manifest.json/device.log), update the string-pinned
     tests in tools/tests/test_web_sync.py with a revert-proof, bump PAGE_VERSION to
     2026-09-12a, then drive every screen and read it back. Delete this file when done. -->

# Rider page copy — the spec (2026-09-12)

## Principles (Apple HIG, applied)
- One task per screen. One sentence of status. Buttons are verbs. Titles are nouns.
- Say what is happening and what to do next. Never explain the mechanism.
- No numbers he cannot act on: no bytes, no KB/s, no seconds-taken, no build hashes,
  no "check number", no "verified/complete" internals. Progress shows a bar and elapsed time only.
- No hedges or parentheticals ("an estimate — nobody has timed a real one yet").
- "Josh" appears where a human recipient is meant; not in every sentence.
- Diagnostics are for Josh: they stay in manifest.json/device.log exactly as now, and the
  screen shows at most one muted line: "Details for Josh are saved in the file."
- One recovery sentence, identical everywhere it appears, only on failure screens:
  "If anything goes wrong, press the small button on the puck twice."
- The save button is "Save" (it saves a file). Sending is what he does afterwards.
- Typographic apostrophes as the page already uses.

## Screen 1 — open
H1: Your ride
Lede: Plug in the puck, then press Connect.
[Connect]
hint (muted): When Chrome asks, choose XIAO nRF52840 Sense.
Bluetooth shape (phones only): lede "Turn on the puck, keep it next to your phone, then press Connect."
  hint "Choose the one that starts with JumpHeight." [Connect]
No status box is shown before anything happens.

## Screen 2 — working (no buttons)
status: Copying your ride…           → once jumps are known: Copying your ride · 3 jumps
caption: JumpHeight-E2C4 · 86% battery
progress bar + elapsed only, e.g. "1 min 12 s"   (no KB/s, no byte counts)
hint: Keep the puck plugged in. This takes a few minutes.
  Bluetooth: Keep the phone next to the puck. This can take up to half an hour.
Notes card — label: Notes   placeholder: Wind, conditions, anything worth remembering
  chips Sea / Wind unchanged.
The old-firmware fallback sentence ("older software, the slow way") is removed; the timer covers it.
wake-hint (phones): Keep the screen on while this copies.
slow-hint (Bluetooth): Is your watch running an activity? End it and try again.

## Screen 3 — copied
status: Ride copied. 3 jumps.        / Ride copied. No jumps this time.
[Save]

## Screen 4 — saved
status: Saved to Downloads.
result: jumpheight-E2C4-20260911-1503.zip — send it to Josh however you like.
then: Empty the puck so it’s ready for next time.
      (muted) Make sure the file is in Downloads first.
[Empty the puck]

## Screen 5 — emptied
status: All done. The puck is empty and ready.
result: Your ride is in Downloads as jumpheight-E2C4-20260911-1503.zip.
secondary, link-styled, not a black button: Save a copy again
  (the "two files, send the newer" line appears only after a second save)
Update card, only when offered:
  title: Software update available
  body:  Takes about two minutes. Your ride is already saved.
  [Update the puck]
Up to date: muted single line "Software is up to date." — no button.

## Update flow
press → status: Restarting the puck…
drag screen → status: Drag the file onto the XIAO-SENSE drive.
  panel title: One more step
   1. jumpheight-54c6826d.uf2 is in your Downloads.
   2. A drive called XIAO-SENSE has appeared in Finder. Drag the file onto it.
   3. The drive disappears during the copy. That’s normal. This page will confirm when it’s done.
  (muted) If anything goes wrong, press the small button on the puck twice.
done → status: Update complete. You can unplug the puck.
       (muted) Now running 54c6826d.
mismatch → status: The update didn’t take.
  panel: The puck is fine and still on its previous software. To try again: press the small
  button on the puck twice, wait for XIAO-SENSE, drag the file on again, then reload this
  page and press Connect. Your ride is already saved.
no restart → status: The puck didn’t enter update mode.
  panel: Nothing changed and your ride is saved. Tell Josh. If the puck seems stuck, press
  the small button on the puck twice.
unchecked → status: Couldn’t confirm the update.
  panel: Unplug the puck, plug it back in, reload this page and press Connect. If it says the
  software is up to date, it worked. If the puck won’t come back, press the small button on
  the puck twice.

## Failures in the ride flow
not verified → status: The copy didn’t check out. panel: Nothing was lost. [Try again]
  plus, when Save is still available: "You can still save this copy for Josh."
  (muted) Details for Josh are saved in the file.   ← the byte-count reasons are NOT rendered
puck not recording (NO REC) → status: The puck isn’t recording.
  panel: Don’t empty it. Save this copy and tell Josh.
cable lost → status: The cable disconnected. Plug it back in and press Connect.
went quiet → status: The puck stopped responding. Check the cable and press Connect.
no puck picked → status: No puck chosen. Press Connect and choose XIAO nRF52840 Sense.
save cancelled → status: Not saved yet. Press Save again.
last resort (no serial, no BLE) → Open this page in Chrome on a Mac and use the cable.

## Footer
Empty the puck after every ride. A full puck can’t record the next one.
page version 2026-09-12a
