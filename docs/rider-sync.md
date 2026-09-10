# Getting your data to Josh — read this after every ride

After every ride: plug the puck into your MacBook to charge, open the link
in Chrome, and send Josh the file. Thanks for doing this too — it's the only
way Josh ever sees what the puck recorded.

**Do it before your next ride, not "sometime".** The puck holds about five
hours of riding and no more — see [The puck's memory fills
up](#the-pucks-memory-fills-up--the-one-thing-to-know) below.

1. **Plug the puck in to charge — on your MacBook.** Same USB cable it
   already charges on. Do this first, every time.
2. **While it's charging, open this link in Chrome** (not Safari — Safari
   can't do this step):
   `https://joshcrow.github.io/Jump-height/sync/`
   - Don't have Chrome? It's a free one-time install — google.com/chrome.
3. **Click "Connect"** and pick the puck from the list Chrome
   shows you — it is called **"XIAO nRF52840 Sense"** (measured off the
   real board 2026-09-09; older listings may show it as "usbmodem" plus
   some digits). It will not say "JumpHeight" — that is its Bluetooth name,
   not its cable name.
4. **Click "Copy the ride"** (step 2 on the page). The page reads everything
   off the puck — jump count, the full trace, battery, all of it. There's a
   progress bar; just leave it alone until it says it's done.
5. **Type one line about the ride, and tap the Sea / Wind chips that
   match** — they ride along with your note. Where you rode, what the water
   was doing, anything that felt off. This isn't busywork — Josh reads the
   notes as much as the numbers, and "flat and glassy" vs. "victory at sea"
   changes what a jump number even means.
6. **Click Send**, then see what actually happens. *(The order below is
   measured on Josh's Mac; not yet on yours. If yours does something else,
   that's worth telling him.)*
   - **A share sheet slides up** (same as sharing a photo) — this is what
     Chrome tries first. Pick Josh in it and you're done. **If you close the
     sheet, nothing was saved anywhere** — no file in Downloads, nothing
     sent. Just click Send again.
   - **The page says it saved to your Downloads** — that's the fallback when
     Chrome can't offer a share sheet. Find the file in your Downloads folder
     and send it to Josh yourself — Messages, Mail, or AirDrop all work.
   - **The page says the ride "hasn't gone anywhere yet"** — nothing was
     sent anywhere. The ride is still safe on the puck (and in your
     Downloads, if it got that far). Tell Josh.
7. **Nothing. You're finished at step 6.** There is no "empty the puck"
   button on your page any more — it isn't hidden or switched off, it isn't
   there. Josh empties the puck when it comes home, and he'd rather have a
   full puck than a wrongly emptied one.

That's it. Go charge your watch too.

## The puck's memory fills up — the one thing to know

The puck holds **about five hours of riding**, and that's the whole of it.
It keeps a ride until Josh empties it **or until it runs out of room** —
whichever happens first.

Once it's full it records no more detail at all. And the next ride you start
after it's been sitting still for an hour or so, it **wipes the old ride** to
make room for the new one. Your **jump counts always survive** that — it's the
detailed second-by-second trace that goes.

So: get each ride off the puck before you go out again. That's the entire
reason this page exists. If two or three rides have stacked up unsent, tell
Josh rather than assuming they're all still in there.

## No Mac handy? Use your phone

Same page, same steps 4-7 above — the page offers **"Connect over
Bluetooth"** instead of the cable button in step 3:

- **iPhone:** open the link in the free **Bluefy** app (App Store) — regular
  Safari can't talk to the puck over Bluetooth, Bluefy can.
- **Android:** open it in Chrome, same as any web page.
- **Not from a Mac, though.** Chrome on a Mac always reports a cable port
  whether or not anything is plugged in, so the page offers the cable there
  and nothing else. On a Mac, use the cable.
- **Make sure your watch is NOT in the middle of an activity first.** If
  Wing Foil is still running from your ride, end it — it's fighting the puck
  for the same Bluetooth connection, and that's the most common cause of a
  slow copy.
- Bluetooth is slower than the cable — see "How long does this take?" below.

## If the screen says something's wrong

| It says (or does) | What it means | What to do |
|---|---|---|
| Chrome shows no puck in the list (cable) | The cable might be charge-only, not the data kind — or the wrong port | Try a different cable (some only charge, they don't carry data), and try another USB port on the Mac. Check the puck is actually on |
| Pulling is really slow / crawling (Bluetooth only — doesn't apply with the cable) | Most likely cause: your watch is still in an activity, fighting for the same Bluetooth connection | End the activity on your watch, then try again |
| "No puck picked. Tap Connect and choose the one whose name starts with JumpHeight." | It didn't find (or you didn't pick) the puck | Make sure the puck is on and either plugged in (cable) or nearby (Bluetooth), then try Connect again |
| "Couldn't connect: …" | The puck didn't answer | Check the cable is plugged in all the way (or the puck is charged and nearby, for Bluetooth); give it a few seconds to wake up; try Connect again |
| "The puck dropped out of range" (Bluetooth only) | The Bluetooth link dropped mid-copy | Nothing is lost — move closer, tap Connect, and start again |
| "That didn't come across cleanly…" / "Not complete — the puck still has everything." | Something about the copy didn't check out — could be anything | Don't worry about diagnosing it — try "Copy the ride" again, closer to the puck if you're on Bluetooth, with your watch out of an activity. If it happens twice, just tell Josh — nothing is lost, the ride is still on the puck. Get it sent before you ride again |
| It says the copy came up short — "N of M bytes" — but the gap is **tiny**, under about a thousand bytes | **The puck is full, not broken.** A full puck slightly over-states how much it's holding, so the page thinks a few hundred bytes went missing when nothing did | **Send it anyway — the ride is complete.** Tell Josh the two numbers so he knows it was the full-puck case, and get this one sent before your next ride |
| The ride "hasn't gone anywhere yet" after Send | The share sheet was closed or never opened, and nothing downloaded | The ride is still on the puck (and maybe in your Downloads). Click Send again; if that does nothing, tell Josh |
| Anything else that looks broken | — | Same as always: don't panic, tell Josh. Nothing on your page can empty the puck, so the ride is still on it — just don't let another ride or two go by before it's sent |

## How long does this take?

**With the cable: nobody has timed a real ride yet, so there is no number
here.** It's expected to be a lot faster than Bluetooth — it's the same USB
link the puck's always used for this on Josh's own bench — but that's an
expectation, not a measurement. Your first real sync with the cable will give
us the actual number, and this page will get updated once we know it.

**With Bluetooth (phone, or a Mac without the cable): also never timed, so
everything below is an estimate, not a promise.** Bluetooth moves roughly
10-16 KB of data per second on this kind of link (a ballpark from how
Bluetooth itself is built, not something measured on this exact puck).

- **A short ride:** minutes, not hours.
- **A puck that's been filling up over several rides:** yours sends its trace
  in the older, roomier format, and a full one is around 16 MB to move. At the
  rate above that's **roughly 20-30 minutes** — an estimate, and the longest
  single thing in this whole process. Plan for it: puck and phone together,
  screen on, watch out of any activity, and don't start it thirty seconds
  before you need to walk out the door.

**The first real sync you do will tell us the actual numbers**, and this page
will get updated once we know them.

---

## For Josh

- **First, before anything else: open the link yourself and confirm it
  actually loads.** `https://joshcrow.github.io/Jump-height/sync/` is a
  **404 until this work merges to `main` and the `pages` job in
  `.github/workflows/build.yml` goes green.** That job is `needs: test`, and
  the `test` job now installs Playwright and drives this page in headless
  Chromium — a job that has **never once run in CI**. So the first push is
  also the first run of a browser test suite, and a red `test` means no
  deploy and a dead link. Do not send Nick a link you have not loaded in
  your own browser.
- **One time only, and required before the above can work:** Settings →
  Pages → Source = "GitHub Actions" on the repo. The `pages` job fails
  loudly until this is set — nothing in the workflow can flip it for you.
- Drop the zip he sends you in `data/inbox/`, then
  `./tools/jump ingest data/inbox/<the file>.zip` (or point `ingest` at
  wherever it actually landed — Downloads, Messages, whatever he used).
  It unpacks the bundle into a session folder under `data/sessions/` and
  writes `report.md` alongside it — same analysis `jump sync` already runs,
  just fed from a bundle instead of a cable.
- Copy the folder **twice**, same as every other session — see
  [session-card.md](session-card.md).
- **Emptying the puck is yours, not his** (`docs/rider-brief.md`, "Never
  'empty' or clear it. I do that."). For this loan the page he is given has
  **no step 4 at all** — the section, the heading and the button are gone
  unless the URL carries `?allowclear=1`, e.g.
  `…/Jump-height/sync/?allowclear=1`, which only you ever type. The flag does
  not weaken the gate underneath it: the button still appears only after the
  ride is verified AND delivered. Clear the trace yourself, on that URL or
  over the cable, once the bundle has ingested and been copied twice.
- **If he reports a short copy with a sub-1 KB gap, that is a full puck, not
  a bad transfer** — the trap in F-22: a byte-complete file whose `STATS`
  `trace_bytes` over-reports. `tracecheck`'s *slow* number is the arbiter;
  `ingest` will want `--force`. The bundle is good.
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
