# Connect IQ Store submission — draft (Task B5)

**Status: DRAFT ONLY.** No developer account was created, no `.iq` was
uploaded, no account action of any kind was taken, and no code was changed
while producing this document. Per execution-plan.md's B5 row, this is
listing text + a checklist for the owner to read; filing is a separate,
later, owner decision (execution-plan.md:74, §Phase E item 6).

Written 2026-08-21 against: `docs/garmin-datafield.md` §11 (the spec's own
distribution plan), `garmin/README.md` (the build/sideload guide),
`docs/STATUS.md` (ground truth on what is actually proven), and the root
`README.md`. Where this draft's recommended text differs from what §11.3
originally sketched, the difference and why are called out — the spec
section describes a plan from before some of it went stale (see §6).

---

## 0. Read this first — the one open question this draft does NOT resolve

**The puck is not for sale, and there is currently no complete build guide
for the hardware it needs.** Before writing listing copy, §8 below lays out
exactly what that means and asks the owner to decide whether public store
listing is the right move *right now*, versus later, versus at all in this
form. That is not this document's call — flagged, not decided, as instructed.

> **CORRECTED 2026-08-23 — §8's decision framing below predates a finding
> that removes one of its own options.** This draft (2026-08-21) still
> treats the Store as one of two channels, with sideload + GitHub Release as
> a standing fallback (§8 option 3). On 2026-08-22, file-copy sideloading to
> the Instinct 3 Solar — the rider's actual watch — was confirmed
> **architecturally impossible** on its firmware (`docs/STATUS.md:855-909`).
> The Store is no longer optional distribution polish; it is **the only way
> this app reaches the rider's watch at all**. §8 option 3 ("never list
> publicly... let sideload cover it") cannot deliver the product to the
> water day and should be read as foreclosed, not merely deprioritized. The
> currently operative submission plan is
> [store-submission-runbook.md](store-submission-runbook.md) (2026-08-22,
> rebuilt 2026-08-23), which reflects this; this file's listing-copy draft
> below is still the source text it points back to.

---

## 1. Listing text (draft)

### Store listing name
**Jump Height — Wing Foil Jumps**

- The in-watch app-list name (`strings.xml:11`, `AppName`) is just **"Jump
  Height"** — short, because watch app lists truncate. The Store listing
  name is separate metadata (set in the developer portal, not compiled into
  the app) and can carry the qualifier. No code change needed either way;
  flagging only so the two aren't expected to match if the owner looks at
  both.
- Confirmed against `manifest.xml:19-51`: does not start with "Garmin"
  anywhere, satisfying the App Review Guidelines' "cannot claim Garmin
  partnership" rule (§6 below) and the spec's own instruction
  (`docs/garmin-datafield.md:425-426`).

### Short summary (~1 line, for the store's summary field)
> See your last wing foil jump height on your wrist within seconds of
> landing — works with an open-hardware BLE sensor you build yourself.

### Long description (draft)

> **Jump Height** is a Connect IQ data field for wing foiling. Add it to any
> activity screen (Windsurf, Kitesurf, or similar) and it shows your last
> jump's height and airtime, plus your session's best and jump count — big,
> glanceable, no taps, no phone.
>
> It works with a companion open-hardware sensor ("the puck") that straps to
> your board and measures the actual jump via Bluetooth Low Energy — the
> puck free-falls with the board while your wrist, loaded by the wing, does
> not, which is why a wrist-only accelerometer app can't do this for wing
> foiling the way it can for kiting (full physics explanation:
> `docs/garmin-datafield.md` §12).
>
> **You will need to build the puck yourself.** It is not sold, anywhere, by
> anyone. It's an open-hardware project — roughly $20 in parts (a Seeed
> XIAO nRF52840 Sense board + battery + waterproofing), fully documented and
> open source. If you don't have one, this data field has nothing to show
> you. Full build info, firmware, and source: <https://github.com/joshcrow/Jump-height>.
>
> When you save your activity, jump count, best height, and best airtime are
> written into the FIT file as developer fields alongside Garmin's own
> GPS/HR/speed data — visible on the Garmin Connect activity page (Strava
> does not render Connect IQ developer fields, so cross-posted rides won't
> show jump data there — Garmin Connect is the archive of record).
>
> No accounts. No cloud. No data leaves your watch except what you
> explicitly save as part of your own Garmin Connect activity.

**Why this description differs from `docs/garmin-datafield.md:426`'s
sketch** ("description linking the repo + web flasher"): **there is
currently no web flasher.** The browser-based flash tool was ESP32-specific
and was removed 2026-08-18 when that hardware platform was retired
(`README.md:43`: "In-browser flashing was removed 2026-08-18 ... ESP Web
Tools cannot flash an nRF52"). Today the puck flashes by USB (`.uf2`
drag-drop or `./tools/jump flash`) — the draft above links the repo only
and does not promise a web flasher that doesn't exist.

### What it does (bullet form, for a features list)
- Finds your puck over BLE automatically when you start your activity — no
  pairing menu, no taps (US1).
- Shows your last jump height huge, with session best and airtime, updated
  within ~2 seconds of landing (US2).
- Optional vibration nudge on a new jump, if the watch permits it for data
  fields (US3).
- Writes jump count, best height, and best airtime into your saved
  activity's FIT file as developer fields (US4).
- Tells you honestly when the link is down instead of showing stale numbers
  (US5), and recovers the session's true count/best within seconds of
  reconnecting, even if you started the activity late (US6).

### Hardware required (must be prominent, not buried)
- **A "Jump Height" puck** — open-hardware, BLE-only, user-built. **Not for
  sale.** Build docs and firmware source: the linked GitHub repo.
- A compatible Garmin watch (see supported devices below). No phone
  required.

### Supported devices (as of this draft)
Only what's actually declared and built, per `manifest.xml:32,45`:
- Garmin Instinct® 3 Solar (45mm) — `instinct3solar45mm`
- Garmin epix™ (Gen 2) — `epix2`

Do not list a broader Fenix/Epix/Forerunner family in the store metadata
until each is actually added to the manifest and simulator-tested
(`docs/garmin-datafield.md:174` reserves that for after M5's Instinct pass —
listing untested devices would itself violate the guidelines' "accurately
disclose supported devices" rule, §6 below).

### Price
Free. (Nothing in the repo suggests otherwise; no payment/IAP code exists.)

---

## 2. Permission justification

Confirmed by reading `manifest.xml:47-52` directly — the app requests
exactly two permissions, nothing else (no `Communications`, no
`PersistedContent`, no network permission of any kind):

| Permission | Why the app needs it (submission-form text) |
|---|---|
| **BluetoothLowEnergy** | This data field connects over Bluetooth Low Energy exclusively to the user's own "Jump Height" puck — a self-built, open-hardware sensor the rider mounts on their own board — using a private Nordic UART Service (a standard BLE text-streaming profile). The app scans only for a device advertising that service or the name "JumpHeight," reads jump/session data the puck already broadcasts, and never connects to any other device, pairs with any Garmin account service, or transmits anything over the internet. This is the sole function of the app; no BLE use exists beyond it. |
| **FitContributor** | Used to write jump count, best jump height, and best airtime into the FIT file of the rider's own saved activity as standard developer fields (per Garmin's FitContributor API), so Garmin Connect can display them next to the activity's built-in GPS/HR/speed data. No fields belonging to the base activity are read, modified, or overridden — only new developer-field data is added. |

No ANT+ profile is registered anywhere in the manifest — this app is BLE
only. That matters for §5 below (the ANT+ review-latency add-on does not
apply here).

---

## 3. Screenshot shot-list

Spec requires 3+ screenshots (`docs/garmin-datafield.md:430-431`); this list
covers the field's actual states so a reviewer (and a store browser) sees
the real product, not just one frame.

Capture method: the CIQ simulator's data-field preview mode shows
full/half/small simultaneously (`garmin/README.md:88-90`); individual
full-screen captures per state need the field driving a real or scripted
puck feed (`fakejump`/`blecmd.py`) into the simulator so each state is
actually reachable, not mocked.

| # | Shot | Layout tier | State | Why it's in the list |
|---|---|---|---|---|
| 1 | Hero: live numbers | Full-screen | CONNECTED, mid-session | The primary sell — "here's a jump height on my wrist" |
| 2 | New-jump flash | Full-screen | CONNECTED, ~5 s after a JUMP (inverted highlight per §4.2) | Shows the glanceable "something happened" cue |
| 3 | Two-up | Half-screen | CONNECTED | Proves the field works alongside another data field, not just full-screen |
| 4 | Small slot | Quarter | CONNECTED | Shows it degrades gracefully to a corner slot |
| 5 | Searching | Full-screen | SEARCHING ("finding puck") | Sets honest expectations before purchase: this needs the puck |
| 6 | Reconnecting | Full-screen (or half) | RECONNECTING | Shows the "stale, marked as such" honesty behavior (US5), a differentiator worth showing off |
| 7 (optional) | No BLE | Full-screen | NO BLE | Completeness; lowest priority of the four states |
| 8 (optional, gated) | Garmin Connect activity page | n/a | Per-jump height chart + jump count/best tiles rendered on connect.garmin.com | **Do not shoot until this is confirmed to actually render** — see §7; a screenshot of something not yet observed would not be honest documentation |

Minimum for submission: #1–#5 (five shots, covers all three layout tiers
and the two most product-relevant states). #6–#8 strengthen the listing but
aren't required by the 3+ minimum.

---

## 4. `.iq` export — command, prerequisites, and what I verified today

### Prerequisites
- Connect IQ SDK installed and on `PATH` (confirmed present:
  `~/Library/Application Support/Garmin/ConnectIQ/Sdks/connectiq-sdk-mac-9.2.0-2026-06-09-92a1605b2`).
- `JAVA_HOME` set and **prepended** to `PATH`, not appended
  (`garmin/README.md:114-117`, `:206-215` — macOS's stub `/usr/bin/java`
  wins otherwise).
- The project developer key, already generated 2026-08-04
  (`garmin/README.md:49-61`), confirmed present at
  `~/.garmin-ciq/developer_key.der` — kept out of the repo, never commit it.
- A **free Connect IQ developer account** on the developer portal. **Not
  yet created** — `docs/STATUS.md:1896` states plainly "no developer-account
  trace in the repo," and nothing found in this session contradicts that.
  This is a real prerequisite gap, not a formality: the `.iq` cannot be
  uploaded anywhere without one.
- All devices you intend to ship listed in `manifest.xml`'s
  `<iq:products>` — currently `instinct3solar45mm` and `epix2`
  (`manifest.xml:32,45`).

### The command (this is the store bundle, not the sideload `.prg`)

The sideload path in `garmin/README.md:69-74` builds a single-device `.prg`
(`-d instinct3solar45mm -o bin/JumpField.prg`, no `-e`). The store wants one
`.iq` bundle covering **every** device in the manifest at once
(`docs/garmin-datafield.md:424`, "all supported devices in one upload") —
that's a different flag, `-e`/`--package-app`, and normally also `-r`
(release / strip debug info):

```bash
cd garmin/jumpfield
mkdir -p bin
export JAVA_HOME=/opt/homebrew/opt/openjdk
export PATH="$JAVA_HOME/bin:$PATH"
monkeyc -e -f monkey.jungle -o bin/JumpField.iq \
    -y ~/.garmin-ciq/developer_key.der -r -w
```

Note: **no `-d` flag** — `-e` packages every product declared in
`manifest.xml` into the one bundle; passing `-d` would be a single-device
sideload build again, the wrong artifact for the store.

**I ran this for real today** (not a guess from memory — `monkeyc --help`
was consulted first to confirm `-e, --package-app: Create an application
package` is the real flag on the installed 9.2.0 compiler, since guessing a
CLI flag wrong would violate the project's own verify-before-trusting rule).
Result, into a gitignored scratch path
(`garmin/jumpfield/bin/`, already excluded by `.gitignore:41`; the test
artifact was deleted afterward — nothing was left in the tree):

- Exit code 0, final line `BUILD SUCCESSFUL`.
- Output file: 76,478 bytes, both devices compiled (warnings for `epix2`
  and `instinct3solar45mm` both appear in the log; `--help`-confirmed flag,
  live compiler run, not documentation-only).
- **New finding, not previously in any doc:** a fresh warning appeared —
  `epix2: The launcher icon (62x62) isn't compatible with the specified
  launcher icon size of the device 'epix2' (60x60). The image will be
  scaled to the target size.` Non-blocking (the compiler auto-scales it),
  but worth a real per-device icon fix before an actual store submission —
  auto-scaled icons look soft at 1px off. Add to §6's checklist.
- The compiler's own device counter printed "4 OUT OF 4 DEVICES BUILT"
  rather than "2 out of 2" — each of the two manifest devices appears to be
  built in two internal passes (visible from the warning log repeating
  identically for each device). This doesn't affect the artifact; flagged
  as observed-but-not-fully-explained rather than silently smoothed over.

---

## 5. Pre-submission checklist, mapped to Garmin's App Review Guidelines

I fetched Garmin's live App Review Guidelines page this session
(`developer.garmin.com/connect-iq/app-review-guidelines/`) to check this
checklist against the actual current rules rather than assumed ones. Where
a rule doesn't apply, it's marked N/A with why — a checklist with no failing
items is not useful if every item was rubber-stamped.

| Guideline area | Rule (as published) | This app | Action before filing |
|---|---|---|---|
| Content/subject matter | No sexual, illegal, gambling, or hazard content; no encouraging risky challenges; wing foiling is not on Garmin's listed prohibited-sport examples (scuba/free-diving/skydiving/BASE/extreme flight) | Clean — it's a measurement tool, makes no claims about safety or performance | None |
| Device integrity / battery | "Apps should not cause Garmin's products to no longer meet their expected battery life" | Scan backoff caps at 15 s (`docs/garmin-datafield.md:240-241`); static footprint measured 12,417 B / 32,768 B budget, and today's simulator run found **no per-line memory leak** over ~1,200 lines (`docs/STATUS.md:743-761`, 2026-08-21) | Keep this evidence on hand in case a reviewer asks; no code change indicated |
| Quality ("fully completed, tested, and ready for use") | Apps must be complete and stable, not "broken links or non-functional features" | **Gaps, stated plainly:** the Instinct 3 Solar — the *primary* listed device and the actual rider's watch — has **never been sideloaded to real hardware**, only simulator-tested (48/48 pass, `docs/STATUS.md:739-766`, 2026-08-21). Garmin Connect's rendering of the FIT developer fields (the per-jump chart, US4) has **never been visually confirmed** — verified only by offline-parsing the FIT file (`docs/STATUS.md:1545`, `docs/glue-and-forget.md:277-278`). No water session has ever happened (M6 not attempted). | **CORRECTED 2026-08-23 — this gate cannot be satisfied the way it's written.** File-copy sideloading to the Instinct 3 (fw 15.18) was attempted twice and is now confirmed **architecturally impossible** — the firmware deletes any `.prg` from `Garmin/Apps` on the next USB disconnect (`docs/STATUS.md:855` "BLOCKER...", `:905` "Verdict: file-copy sideloading is architecturally impossible on this firmware"). There is no pre-submission path to real-Instinct-hardware evidence; the store review *is* the only route onto that watch (`docs/store-submission-runbook.md`). The gate this row should now enforce: file only once simulator coverage (48/48, done) and Epix real-hardware coverage (done, but a different device) are both current, and treat the first post-approval install on the rider's Instinct as the still-outstanding real-hardware proof, not a pre-filing requirement. |
| IP / branding | Cannot use Garmin's name/brand without permission; cannot claim partnership | Listing name doesn't start with "Garmin" (checked against `manifest.xml`) | None |
| Third-party hardware disclosure | Must disclose third-party hardware/software dependencies; must accurately disclose supported devices and minimum requirements | The puck dependency is stated up front in this draft's description, not buried — see §0/§1 | Keep it prominent; do not soften it in the final copy |
| ANT/ANT+ disclosure | "List all ANT/ANT+ profiles supported... pass ANT+ certification where applicable" | **N/A — this app registers zero ANT+ profiles** (confirmed: `manifest.xml` requests only `BluetoothLowEnergy` and `FitContributor`, no ANT module at all) | None; also relevant to §7 below |
| Privacy | Must publish a privacy policy if collecting personal data; "think carefully before collecting... data" | App collects nothing beyond the jump data it already broadcasts; no accounts, no network permission requested at all | **Still needed: an actual privacy-policy page/URL to submit** — a one-paragraph statement ("no accounts, no network, no data leaves the watch except your own saved FIT file") is accurate per the manifest, but doesn't yet exist as a hosted page anywhere |
| Support URL | Not explicitly quoted from the guidelines page in this session, but required by the submission form per `docs/garmin-datafield.md:427` | GitHub Issues on the public repo (confirmed public: `gh repo view` → `"visibility":"PUBLIC"`) works as a real, reachable URL today | Use `https://github.com/joshcrow/Jump-height/issues` |
| Promotion accuracy | "Must not make any inaccurate or misleading statements"; disclose minimum requirements | This draft's copy states the puck requirement and current device list accurately | Keep it that way through any future rewrite — don't let marketing language erase the hardware requirement |
| Icon assets | Per-family launcher icon sizes | `epix2` warning found in §4 (62×62 vs required 60×60) | Fix or accept the auto-scale; low priority, cosmetic |
| Web flasher claim | (Not a Garmin rule — an internal accuracy check) | §11.3's own draft template referenced a "web flasher" that no longer exists (`README.md:43`) | This draft's copy (§1) already avoids that claim |

---

## 6. Known facts about review latency — and what I could and could not verify

The number I was given to include: **Garmin's live docs state reviews
complete within 72 hours, +48 h for ANT+ profiles.** Per house rules
("verify claims against source, including mine"), I attempted to
independently re-confirm this against Garmin's actual current developer
pages this session rather than just restate it. Reporting the outcome
plainly:

- **Confirmed, directly:** the live App Review Guidelines page
  (`developer.garmin.com/connect-iq/app-review-guidelines/`) does **not**
  state a specific number of hours anywhere I could retrieve. Its actual
  text on review speed is: *"we endeavor to review the app and the related
  documentation as thoroughly and promptly as possible"* — no committed
  SLA on that page.
- **Undetermined:** the 72-hour figure (and the +48 h ANT+ add-on) most
  likely lives on the Connect IQ FAQ page, which is the natural place for
  it — but that page's actual Q&A answer text loads via client-side
  JavaScript that my fetch tooling could not retrieve (it returned only the
  navigation/heading structure, twice, with two different fetch methods).
  `WebSearch` was unavailable this session (session search budget already
  exhausted before I could use it). I could not confirm or refute the
  specific 72 h / 48 h numbers from a primary source in this session.
- **Where the number in this project actually comes from:**
  `docs/glue-and-forget.md:267-268`, itself the product of an earlier,
  separate web-research pass ("Garmin's live docs now state review within
  72 hours"). I am not able to independently corroborate that citation
  today; treat it as **plausible but not re-verified**, not as confirmed
  fact, until someone loads the live FAQ page in an actual browser (or the
  search budget resets) and reads the answer text directly.
- **What I can say with certainty regardless of the number's accuracy:**
  this app requests **zero ANT+ profiles** (§5 above), so even if the "+48 h
  for ANT+" rule is real, **it does not apply to this submission.**

Recommendation: re-check the live FAQ page (or ask a support contact) for
the actual current number immediately before filing, since Garmin can
change review-process text without notice and this project has already
been burned once by trusting a stale summary over a primary source
(`docs/STATUS.md`'s own stated reason for existing).

---

## 7. What this app does NOT do — stated plainly

- **Does not work at all without the puck**, and the puck is not sold by
  anyone. A store visitor without one gets a field stuck on "finding puck"
  forever.
- **Does not currently have a complete build guide for the hardware it
  needs.** `BUILD.md` documents the *retired* v1 (FireBeetle ESP32) board
  only (`README.md:29`, `:45`: "🪦 Retired 2026-08-18 ... cannot be built,
  flashed or flown any more"). `docs/sense.md`, the doc for the board that
  actually ships today, is explicitly labeled a "port spec & gap analysis,"
  is marked superseded-in-part, and is not a shopping-list/soldering
  runbook (`docs/sense.md:1,3-11`). A motivated stranger who reads the
  Store listing and wants to build a puck cannot currently do so from the
  repo's docs alone.
- **Does not have a web flasher.** The one that existed was ESP32-specific
  and was removed 2026-08-18 (`README.md:43`). Flashing today is USB-only
  (`.uf2` drag-drop or a CLI tool).
- **CORRECTED 2026-08-23 — stronger than "has never been": sideloading to
  the Instinct 3 Solar was tried twice (2026-08-22) and is now confirmed
  architecturally impossible on its firmware (15.18)**, not merely
  not-yet-attempted. The watch deletes any `.prg` copied to `Garmin/Apps`
  on the next USB disconnect; Connect IQ apps there live only in an internal
  registry a loose file never joins (`docs/STATUS.md:855-909`). Only
  simulator coverage exists for this app on that device
  (`docs/STATUS.md:739-766`), and it always will until this store submission
  is approved — the store is the only install path left for it.
- **Has never had its Garmin Connect chart rendering visually confirmed.**
  The FIT developer fields are proven correct by offline parsing of the
  downloaded FIT file; nobody has opened the activity on connect.garmin.com
  to look (`docs/STATUS.md:1545`).
- **Does not render on Strava** even when a ride is cross-posted there —
  Strava doesn't show Connect IQ developer fields at all
  (`docs/glue-and-forget.md:269-271`). The listing copy says this plainly
  so nobody is surprised.
- **Has never been used on the water.** Zero real riding sessions recorded
  anywhere in this project as of this draft (`docs/glue-and-forget.md:236`:
  "no real riding session has ever been recorded").
- **Computes jump height/count/best/airtime only** — no time-on-foil, no
  spins, no carve angle. Those are on the roadmap, not in this build.

---

## 8. The question for the owner (flagged, not decided)

Putting §0 and §7 together: as currently built and documented, this app is
**unusable by the overwhelming majority of anyone who would find it in the
Connect IQ Store** — it requires hardware that is not for sale and that, as
of today, cannot even be fully self-built from the repo's own docs. The
Store's actual audience for a listing like this is realistically "other
open-hardware tinkerers who'd read the source anyway," not a general Garmin
user.

Options, laid out without a recommendation between them:
1. **List anyway, now.** Cheap (review is fast whatever the exact number
   turns out to be), and it puts a real download count / review-feedback
   channel in front of the small audience who would actually want it.
   Downside: likely low-rated by casual downloaders who install it, see
   "finding puck" forever, and never read the description.
2. **Wait until the build guide + a hosted `.uf2`/Release exist**, so the
   listing's own promise ("build the puck yourself") is actually
   deliverable end to end. This is the execution-plan.md sequencing
   already implies (B5 is "draft," E5 is "store submission lands," ordered
   after several other Era-2 items) — but the plan doesn't explicitly say
   *why* to wait past "the draft is ready," and this reasoning (§7) is that
   why.
3. **Never list publicly in this form** — keep distribution to the GitHub
   Release + sideload guide (`docs/garmin-datafield.md` §11.1-11.2) and
   let word-of-mouth/README be the only discovery path, treating the Store
   as unnecessary overhead for a project whose real dependency (a
   custom sensor) the Store can't help distribute anyway.

This document does not choose between them. It exists so the choice can be
made with the actual gaps in front of the owner, not assumed away.

---

## 9. Source citations (for anyone re-verifying this draft)

- `docs/garmin-datafield.md` §11 (lines 388-445) — the spec's own
  distribution plan, listing template, and website touchpoints.
- `garmin/README.md` (whole file) — SDK setup, build commands, sideload
  paths, developer-key generation.
- `docs/STATUS.md:739-766` (2026-08-21 entry) — Instinct simulator
  dress-rehearsal results (48/48, no leak, static footprint).
- `docs/STATUS.md:1896` — no developer account / no `.iq` / no store
  artifacts exist yet.
- `docs/STATUS.md:1545` — Garmin Connect rendering of dev fields never
  checked.
- `docs/glue-and-forget.md:236, 267-271` — no real riding session ever
  recorded; 72 h review claim's origin; Strava non-rendering.
- `README.md:3, 29, 43, 45` — "$20 in parts," Sense build-guide gap, web
  flasher removal, v1 retirement.
- `docs/sense.md:1-11` — confirms it is a spec/gap-analysis doc, not a
  build runbook, and is itself marked stale.
- `garmin/jumpfield/manifest.xml:32,45,47-52` — actual supported devices
  and actual requested permissions (read directly, not assumed).
- `garmin/jumpfield/resources/strings/strings.xml:11` — actual in-app name.
- `.gitignore:41` — confirms `garmin/jumpfield/bin/` is excluded, so
  today's verification build left nothing in the tree.
- Live `monkeyc --help` output (installed SDK 9.2.0, this session) — real
  flag list, confirming `-e, --package-app` before it went into this doc.
- Live fetch of `developer.garmin.com/connect-iq/app-review-guidelines/`
  and attempted fetches of the Connect IQ FAQ page (this session) — see §6
  for exactly what was and wasn't confirmed.
