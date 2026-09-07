# Getting your data to Josh — read this after every ride

After every ride: plug the puck into your MacBook to charge, open the link
in Chrome, and send Josh the file. Thanks for doing this too — it takes a
few minutes and it's the only way Josh ever sees what the puck recorded.

1. **Plug the puck in to charge — on your MacBook.** Same USB cable it
   already charges on. Do this first, every time.
2. **While it's charging, open this link in Chrome** (not Safari — Safari
   can't do this step):
   `https://joshcrow.github.io/Jump-height/sync/`
   - Don't have Chrome? It's a free one-time install — google.com/chrome.
   - The first time you plug the puck into your Mac, you might see **"Allow
     accessory to connect?"** — click **Allow**.
3. **Click "Connect with the cable"** and pick the puck from the list Chrome
   shows you — it's the one whose name mentions "XIAO", "JumpHeight", or
   "usbmodem".
4. **Click "Copy the ride"** (step 2 on the page). The page reads everything
   off the puck — jump count, the full trace, battery, all of it. There's a
   progress bar; just leave it alone until it says it's done.
5. **Type one line about the ride, and tap the Sea / Wind chips that
   match** — they ride along with your note. Where you rode, what the water
   was doing, anything that felt off. This isn't busywork — Josh reads the
   notes as much as the numbers, and "flat and glassy" vs. "victory at sea"
   changes what a jump number even means.
6. **Click Send**, then see what actually happens:
   - **The page says it saved to your Downloads** — that's the normal case
     on a Mac. Find the file in your Downloads folder and send it to Josh
     yourself — Messages, Mail, or AirDrop all work.
   - **A share sheet pops up** instead (same as sharing a photo) — pick
     Josh in it, if Chrome offers you one.
   - **The page says the ride "hasn't gone anywhere yet"** — nothing was
     sent anywhere. The ride is still safe on the puck (and in your
     Downloads, if it got that far). Don't click "Empty the puck" — tell
     Josh instead.
7. **Click "Empty the puck"** once you've actually gotten the ride to
   Josh — not just once the button appears. The button shows up once the
   page has checked the copy is complete and handed the file off somewhere,
   but it can't see all the way to Josh's inbox: if the ride landed in your
   Downloads instead of going straight to him, send it first, then come
   back and click "Empty the puck". This empties the puck so it's ready for
   your next ride with the full ~5 hours of room again.

That's it. Go charge your watch too.

## No Mac handy? Use your phone

Same page, same steps 4-7 above — just tap **"Connect over Bluetooth"**
instead of "Connect with the cable" in step 3:

- **iPhone:** open the link in the free **Bluefy** app (App Store) — regular
  Safari can't talk to the puck over Bluetooth, Bluefy can.
- **Android:** open it in Chrome, same as any web page.
- **A Mac without the cable handy also works this way** — same "Connect
  over Bluetooth" button, from Chrome.
- **Make sure your watch is NOT in the middle of an activity first.** If
  Windsurf is still running from your ride, end it — it's fighting the puck
  for the same Bluetooth connection, and that's the most common cause of a
  slow copy.
- Bluetooth is slower than the cable — see "How long does this take?" below.

## If the screen says something's wrong

| It says (or does) | What it means | What to do |
|---|---|---|
| Chrome shows no puck in the list (cable) | The cable might be charge-only, not the data kind — or the wrong port | Try a different cable (some only charge, they don't carry data), try another USB port on the Mac, and if you see "Allow accessory to connect?", click **Allow** |
| Pulling is really slow / crawling (Bluetooth only — doesn't apply with the cable) | Most likely cause: your watch is still in an activity, fighting for the same Bluetooth connection | End the activity on your watch, then try again |
| "No puck picked. Tap Connect and choose the one whose name starts with JumpHeight." | It didn't find (or you didn't pick) the puck | Make sure the puck is on and either plugged in (cable) or nearby (Bluetooth), then try Connect again |
| "Couldn't connect: …" | The puck didn't answer | Check the cable is plugged in all the way (or the puck is charged and nearby, for Bluetooth); give it a few seconds to wake up; try Connect again |
| "The puck dropped out of range" (Bluetooth only) | The Bluetooth link dropped mid-copy | Nothing is lost — move closer, tap Connect, and start again |
| "That didn't come across cleanly…" / "Not complete — the puck still has everything." | Something about the copy didn't check out — could be anything | Don't worry about diagnosing it — try "Copy the ride" again, closer to the puck if you're on Bluetooth, with your watch out of an activity. If it happens twice, just tell Josh — nothing is lost, the puck keeps it all until you tap "Empty the puck" |
| The ride "hasn't gone anywhere yet" after Send | The share sheet didn't open and nothing downloaded | The ride is still on the puck (and maybe in your Downloads). Don't tap "Empty the puck" — tell Josh |
| Anything else that looks broken | — | Same as always: don't tap "Empty the puck", don't panic, tell Josh. The puck keeps everything until you empty it |

## How long does this take?

**With the cable:** nobody's timed a real ride yet, so there's no number
here — but it's expected to be a lot faster than Bluetooth, since it's the
same USB link the puck's always used for this kind of thing on Josh's own
bench. Your first real sync with the cable will tell us the actual number,
and this page will get updated once we know it.

**With Bluetooth (phone, or a Mac without the cable):** **Nobody's timed
this one either, so it's a guess, not a promise.** Bluetooth transfers
roughly 10-16 KB of data per second (that's a ballpark from how Bluetooth
itself is built, not something measured on this exact puck yet). An hour of
riding is somewhere around 3 MB of data in the older format, or maybe
roughly a sixth of that (~0.5 MB) if your puck has the newer, more compact
firmware. Rough math says a few minutes either way — call it 3-5 minutes on
the older format, under a minute on the newer one. **The first real sync
you do will tell us the actual number**, and this page will get updated
once we know it.

---

## For Josh

- **One time only, before Nick ever opens the link:** Settings → Pages →
  Source = "GitHub Actions" on the repo. The `pages` job in
  `.github/workflows/build.yml` fails loudly until this is set — nothing in
  the workflow can flip it for you. Confirm the link actually loads for you
  before you ever send it to him.
- Drop the zip he sends you in `data/inbox/`, then
  `./tools/jump ingest data/inbox/<the file>.zip` (or point `ingest` at
  wherever it actually landed — Downloads, Messages, whatever he used).
  It unpacks the bundle into a session folder under `data/sessions/` and
  writes `report.md` alongside it — same analysis `jump sync` already runs,
  just fed from a bundle instead of a cable.
- Copy the folder **twice**, same as every other session — see
  [session-card.md](session-card.md).
- The page itself lives in the repo (`web/sync/`) and republishes to
  `https://joshcrow.github.io/Jump-height/sync/` automatically whenever
  `main` gets pushed, once the one-time Pages setting above is done —
  nothing else to deploy by hand.
- The page also accepts an optional `?drop=<https URL>` on the link, which
  additionally uploads the zip straight to that URL — off by default, only
  useful if there's ever a server on the other end worth pointing it at.
  **Untested, optional, not part of Nick's normal flow above.** A
  three-line Google Apps Script sketch for a receiver, if this ever gets
  built out — again, **untested**:

  ```js
  function doPost(e) {
    DriveApp.getFolderById('FOLDER_ID').createFile(
      Utilities.newBlob(e.postData.bytes, 'application/zip', e.parameter.filename));
    return ContentService.createTextOutput('ok');
  }
  ```
- **Emergency fallback, MacBook only, not the normal path:** if the page
  itself ever breaks, Nick's Intel MacBook can still get you data the hard
  way — talk him through `git clone` and `./tools/jump ...` over a call.
  This needs Python 3 on his Mac and your patience on the phone; it is not
  something to hand him unsupervised, and it's not what step 1-7 above
  describes.
