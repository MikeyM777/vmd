# Updating the offline console from a USB stick

**Status:** design agreed 22 Aug 2026. Not built.

The console runs on a machine with no internet and no way to get one. Today the
only way to change the software on it is to build the whole 2 GB kit on a
connected machine and replace `C:\VMD` by hand. This describes the thing that
replaces that trip: a dedicated USB stick, a small program on a borrowed laptop
that fills it from GitHub, and an **Update** button on the console that applies
it.

`vmd/updater.py` already exists and does none of this: it runs `git pull` and
needs a `.git`, a git binary and the internet, none of which the deployment has
(`scripts/offline_kit.ps1` excludes `.git` from the kit on purpose). It is
replaced by what is described here.

---

## What was decided, and what was rejected

| Decision | Rejected alternative | Why |
|---|---|---|
| Update carries code + any libraries the target lacks | code only | The day a change needs a new library, a code-only update lands and the console does not start. |
| | everything, always (~2 GB) | That is the kit that already exists. The point of this is a trip that takes minutes. |
| Smoke-test the new version, keep the old one, offer a way back | apply and hope | Nobody on that site can read a traceback, and there is no second machine. |
| Laptop needs nothing installed | git, or a full VMD install | It is a borrowed laptop whose only job is carrying updates. |
| The stick carries a note back about the machine | pack every wheel every time | torch alone is over 2 GB. The note is 10 KB and makes the payload exact. |
| Version is one whole number | dates, semver | `8 > 7` is a comparison anybody can make over a phone. |
| Stop everything, apply, start again | juggling running files | Recording is being dropped from the product, so there is nothing to keep up. |

**Out of scope, flagged rather than folded in:** recording is being removed from
the product. That touches the recorder service, the autostart tasks, retention
and the Playback tab, and it is its own conversation. This design assumes only
that the console may be stopped and started at will.

---

## The version number

A file `VERSION` in the repository root holding one integer, bumped when a
change is worth shipping. It travels inside the update, so after an update the
offline copy's own `VERSION` **is** its version - there is no second place for
it to be recorded and no way for the two to disagree.

Shown as `VMD 7` in the window title, at the top of the Settings tab, and in
both installers' summaries. The build date is shown beside it for context
(`VMD 7 - 22 Aug 2026`) but plays no part in any comparison.

---

## The stick

Dedicated to VMD and formatted for it, so its root is the update:

```
(stick root)
  README.txt          "VMD update stick - version 8, built 22 Aug 2026"
  update.json         { "version": 8, "built": "...", "commit": "303c42c" }
  manifest.json       every file in files\ with its SHA-256 and size
  files\              vmd\, scripts\, docs\, *.bat, VMD.exe, pyproject.toml,
                      uv.lock, VERSION
  wheels\             wheels the offline PC lacks. Usually empty
  machines\
    <computer-name>.json   written by that PC: its version, every installed
                           library and version, when it last updated
```

Plain files rather than an archive: the offline side can verify and copy without
unpacking anything, and a person can look at the stick in Explorer when
something is confusing.

`manifest.json` is the whole trust story. There is no signature - the stick is
physically controlled and the machine is air-gapped - but every byte is checked
before anything is touched, so a half-written stick is refused rather than half
applied.

`machines\` is keyed by computer name, so one stick can serve several sites.

---

## The laptop side

`VMD-Update-Stick.bat` plus `scripts/update_stick.ps1`, copied onto any Windows
laptop. Nothing to install: no git, no Python.

A WinForms window with four lines - the USB drive (detected, with the version
already on it), the GitHub repository and branch, a **Build the stick** button,
and a progress line.

What the button does:

1. Download `master.zip` over HTTPS from GitHub and unpack it to a temp folder.
2. Read `VERSION` from it. Read `machines\*.json` from the stick.
3. Compare the new `uv.lock` against each machine's library list. For anything
   missing, fetch the wheel with the `uv.exe` that lives on the stick, pinned to
   the TARGET's platform (`--python-platform windows --python-version 3.12`),
   not the laptop's.
4. Write `files\`, `manifest.json`, `update.json`, `README.txt`.
5. Re-hash what it wrote and compare against the manifest it just made. A stick
   that fails its own verification is reported as unusable, not shipped.

With no `machines\` note on the stick - a new stick, or one that has never been
to the site - it packs no wheels and says so: "This stick has never been to a
VMD machine, so it carries code only. If the update needs a new library the
console will say so and nothing will be changed."

---

## The console side

### The button

At the bottom of the Settings tab, always visible - on this machine it is now
the main maintenance action:

```
  This system:  VMD 7          (22 Aug 2026)
  Update stick: VMD 8 found on E:\        [ Update now ]
```

Other states, in the same place: `No update stick found  [Look again]` ·
`Stick has VMD 7 - the same version this system runs` · `Updating...` with the
step · `Updated to VMD 8` · `VMD 8 did not start. VMD 7 was put back. Nothing
was lost.`

A **Go back to VMD 7** button appears whenever a previous version is on disk,
including after a successful update. It asks first - "Put VMD 7 back? The
console will close and start again." / Yes / Cancel - because it is one press
away from undoing an update somebody has just travelled to deliver.

### Applying

The console does not apply the update: it starts `scripts/apply_update.ps1`
detached and watches its log, because the files being replaced include the
console itself.

1. **Find the stick.** Removable drives with `update.json` and `manifest.json`.
   Two of them: refuse, and name both.
2. **Compare versions.** Equal, say so and stop. Lower, refuse - going back is
   the other button.
3. **Verify.** SHA-256 every file in `files\` against the manifest. One
   mismatch and it stops with nothing touched.
4. **Write the note back** to `machines\<name>.json` - now, not at the end, so
   that even a failed update teaches the laptop what this machine has.
5. **Stop** the console and go2rtc (`Stop-ProjectProcesses`, which exists).
6. **Keep the old copy** in `C:\VMD\previous\7\`. One previous version is kept.
7. **Copy** `files\` in, by whitelist: `vmd\`, `scripts\`, `docs\`, `*.bat`,
   `VMD.exe`, `pyproject.toml`, `uv.lock`, `VERSION`. Never `settings.json`,
   `cameras\`, `bin\logs\`, `bin\*.exe`, `bin\python\`, `.venv\`,
   `Ultralytics\`.
8. **Libraries**, only when `uv.lock` changed:
   `uv sync --offline --no-index --find-links <stick>\wheels --extra detect`.
9. **Smoke test:** `python -m vmd.selftest` - a new module that imports the
   console's own packages, reads the settings file and exits 0.
10. Pass: start the console, report `Updated to VMD 8`. Fail at 8 or 9: put
    `previous\7\` back, sync, start VMD 7, and say what happened.
11. Everything to `bin\logs\update.log`. A marker file is written before step 5
    and removed after step 10, so an apply killed by a power cut is spotted at
    the next start and a way back is offered.

`VMD.exe` is replaced while it is not running, but Windows may still hold it.
The same fix as in `scripts/install.ps1`: rename the old one aside, copy, and
delete the leftover on the next run.

`previous\` holds code only. If an update also changed libraries, going back
relies on uv's cache on that machine still holding the old ones. It usually
does - they were installed there once - and when it does not, the rollback says
so rather than pretending.

---

## What can go wrong

| Situation | What the operator sees | State afterwards |
|---|---|---|
| No stick, or not a VMD stick | "No update stick found" | untouched |
| Two sticks | both named, nothing done | untouched |
| Bad or missing file on the stick | "The stick is damaged: 3 files do not match. Nothing was changed." | untouched |
| Stick older or the same | which version each side is | untouched |
| A library is needed and not on the stick | "This update needs a library that is not on the stick. Nothing was changed. Build the stick again on the laptop - it will pick it up now." | untouched |
| `uv sync` fails | old version put back | VMD 7 running |
| Smoke test fails | "VMD 8 did not start. VMD 7 was put back." plus the error in the log | VMD 7 running |
| Power cut or stick pulled out mid-apply | at next start: "An update was interrupted. [Put VMD 7 back]" | one press from VMD 7 |
| Two offline machines, one stick | each writes its own note; the laptop packs what both need | both updatable |

---

## Testing

Unit and end-to-end, all offline, in `tests/`:

- `test_update_manifest.py` - build a manifest for a folder and verify it; flip
  one byte and assert verification fails and names the file.
- `test_update_apply.py` - a fake install tree in `tmp_path`: the whitelist is
  copied, `settings.json` and `cameras\` are byte-for-byte untouched, a failing
  smoke test restores the old tree exactly, and an apply interrupted partway
  leaves the marker and can be rolled back.
- `test_update_stick.py` - given a fake GitHub zip and a machine note, the
  stick gets exactly the missing wheels and no others; with no note, no wheels
  and the sentence that says so.
- `test_selftest.py` - `vmd.selftest` exits 0 on a good tree and non-zero with
  the real error on a broken one.
- `test_desktop_update_panel.py` - the panel's states, and that **Go back**
  asks before doing anything.
- By hand, once: build a real stick, apply it to a copy of the folder at another
  path, roll back.

---

## Deliberately not built

- **Signatures.** The stick is physically controlled and the machine is
  air-gapped. A signing key on a borrowed laptop is a worse risk than the one it
  removes.
- **Patch files.** The code tree is a few MB; copying all of it is simpler and
  is always correct.
- **Automatic updates on stick insert.** An update is a decision somebody makes.
- **More than one previous version.** Two ways back is two things to explain on
  a machine where the answer must be obvious.
