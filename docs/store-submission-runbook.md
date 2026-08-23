# Connect IQ store submission — what YOU do, step by step

Written 2026-08-22, after the Instinct 3 (fw 15.18) was proven to reject
sideloaded `.prg` files outright. **The store is now the only way to get this
app onto the rider's watch**, so this stopped being a distribution nicety and
became the critical path to the water day.

Everything that can be done without your Garmin account is done. The upload
artifact exists. What follows needs a human logged in.

---

## The artifact

**`garmin/jumpfield/bin/JumpField.iq`** — 79,145 bytes, rebuilt **2026-08-23**,
4 of 4 device variants compiled clean.

> **Rebuilt 2026-08-23, and this matters.** The previous package was built
> 2026-08-22, BEFORE the audit's two watch fixes landed:
> **F-11** (the JUMP path could drive session count/best DOWN into the saved
> FIT — a 12-jump ride archived as "1 jump, best 0.20 m" after a puck brownout)
> and **F-12** (one dropped BLE callback parked the link permanently, no
> reconnect, no error). Submitting the older `.iq` would have shipped the app
> to the rider without either fix.
>
> **Rule: rebuild the package immediately before submitting, and after any
> change under `garmin/jumpfield/source/`.** A store listing is the slowest
> thing to correct in this project — review takes days, and the rider cannot
> get the app any other way.

Rebuild it any time with:
```bash
cd garmin/jumpfield
export PATH="/opt/homebrew/opt/openjdk/bin:$PATH"
"$SDK/bin/monkeyc" -f monkey.jungle -o bin/JumpField.iq \
    -y ~/.garmin-ciq/developer_key.der -e -w -r
```
(`-e` is the export/package flag — that is what makes a `.iq` rather than a
per-device `.prg`.)

**Keep `~/.garmin-ciq/developer_key.der` safe and backed up.** Garmin ties an
app's identity to the signing key; lose it and updates to this listing become
impossible — the app has to be re-published as a new one, and every installed
copy is orphaned.

## Step 1 — developer account

1. Sign in at **developer.garmin.com** with your normal Garmin account (the
   same one his watch pairs to is fine; the accounts do not need to match).
2. Accept the developer agreement if prompted. No fee.

## Step 2 — create the app

Connect IQ store dashboard → **Upload an App**.

- **Type:** Data Field
- **Upload:** `JumpField.iq`
- **Compatible devices** are read from the package — expect **Instinct 3 Solar
  45mm** and **Epix Gen 2**.

## Step 3 — the listing text

Draft copy is in [store-submission.md](store-submission.md). The two fields
that matter most:

**Name:** Jump Height

**Description — lead with the hardware requirement, honestly.** This app is
useless without a puck nobody can buy. Say so in the first line; a reviewer
who discovers it themselves may reject for a broken experience:

> Shows jump height and airtime live during a session, from a custom
> board-mounted motion sensor, and records them into the activity's FIT file.
> **Requires a compatible custom sensor — this app does nothing on its own.**

## Step 4 — permission justification

The package requests two, and the form asks why:

- **Bluetooth Low Energy** — "Connects to a custom board-mounted motion sensor
  over Bluetooth LE to receive jump measurements during the activity."
- **FitContributor** — "Writes jump count, best jump height and best airtime
  into the activity's FIT file as developer fields."

Both are accurate and minimal. No personal data leaves the watch; say that if
there is a field for it.

## Step 5 — decide the visibility

**Recommended: publish it, but consider whether you want it PUBLIC.**

The app requires hardware nobody else has. A public listing invites installs
from people it cannot possibly work for, which earns one-star reviews for
working exactly as designed. Options, in preference order:

1. **Check whether the dashboard offers a private/beta/unlisted channel** for
   testers — if it exists, that is exactly this situation and avoids the
   problem entirely.
2. **Publish publicly with the hardware requirement in the first sentence**
   and a clear title suffix if allowed.

*(I could not verify which visibility options the current dashboard offers —
this session's web-research budget was exhausted. Look when you are in there;
if only public exists, option 2 is fine.)*

## Step 6 — submit and wait

Garmin's own published figure: **reviews complete within 72 hours** (+48 h if
an app uses ANT+ profiles; ours does not).

**Submit as early as you can.** It is the longest-latency item between here
and the water day, and it is the only one where waiting cannot be compressed
by working harder.

## Step 7 — when it is approved

1. Install on **his** watch from the Connect IQ phone app.
2. Confirm the **Connect IQ Fields** category now offers "Jump Height".
3. Then the whole of [instinct-night.md](instinct-night.md) runs as written —
   the layout photos, the desk test, the two-central test, the FIT save. None
   of that changed; only the delivery mechanism did.

## What I still need from you

- The **water-day date** — everything sequences off it, and the store review
  makes lead time real now.
- Whether **Climb stays installed** on his watch (a known-good reference field
  is useful for layout comparison).
- Confirmation that the Connect IQ category appears with Climb in it — that
  closes the last link in the chain.
