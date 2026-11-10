# HonorBoot

Experimental, local-only Windows controller for legacy Huawei/Honor bootloader
code attempts. Durable per-device checkpoints, battery-aware recovery and a
native dashboard. No AI service, cloud account or telemetry is used.

**This is not a proven unlock method.** It reproduces the limited candidate
sequence in [vcka/huawei-honor-unlock-bootloader](https://github.com/vcka/huawei-honor-unlock-bootloader).
That sequence is not an exhaustive search and may never include a valid code.
An exhausted sequence does not mean the phone cannot be unlocked another way.

## Read this before starting

`fastboot oem unlock CODE` is an **unlock command**, not a harmless validation API.
A valid code may immediately erase user data and unlock the phone, or display a
confirmation on the phone. Behavior depends on the device. There is no guaranteed
second confirmation on the PC. Every new `run`/`resume` requires explicit consent.
An `OKAY` response is stored as **command accepted**, never as proof of a completed
unlock. The runner stops, saves the code and displays a native Windows popup.

Use only devices you own or are authorized to modify. The workflow was developed
around the Honor 9 STF family. Other models/firmware are not validated. Four
attempts followed by a bootloader reboot is an observed behavior on that device,
not a guarantee on other phones. This release is experimental; recovery logic is
covered by simulated tests, not validated against every physical failure mode.

## Windows download (no Python installation)

Download **HonorBoot-Windows-x64.zip** from this repository's **Releases** page,
extract it, and double-click **HonorBoot.exe**. Select your official Android
platform-tools folder and enter your device serial and chosen IMEI seed. These
settings stay on your PC. Opening the dashboard does not start attempts.

The executable includes Python and Tkinter, but not Android tools or USB drivers.
The portable build is unsigned. Releases are marked experimental. A SHA-256
checksum is published with each release; no code-signing certificate is claimed.
A companion console may remain visible; use the dashboard's command field.

GitHub Actions builds Windows x64 on pushes to `main` and manual dispatches.
Version tags (`v*`) additionally publish a downloadable prerelease. Development
builds are available under the workflow's **Artifacts** section.

## Requirements

- Windows 10/11 x64. Source users need Python 3.10 or newer with Tkinter; the executable includes both.
- [Official Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools), extracted separately.
- A working USB data connection and the appropriate device drivers.
- Authorized USB debugging in Android and OEM unlock enabled for code attempts.
- A stable ADB serial that matches the Fastboot serial. Devices whose serial
  changes between modes require additional support and will not be guessed.

No third-party Python runtime dependencies. Platform-tools and phone drivers are
not bundled, silently downloaded or installed.

## Quick start

From this directory, replace the example paths/identifiers with your own:

```powershell
py -3 -m honorboot --tools C:\Android\platform-tools detect
py -3 -m honorboot --tools C:\Android\platform-tools --serial YOUR_SERIAL --imei YOUR_IMEI dashboard
```

The dashboard uses a command field with `status`, `pause`, `resume`, `reboot`,
`fastboot`, `checkbattery` and `help`. It updates text in place without clearing
the entire screen. Closing it does not stop an independently running worker.
Use `pause` to stop the worker first, then use other phone commands. Busy
controllers refuse competing commands instead of operating on the same phone.

The dashboard also includes:

- A progress bar and an explicit active / paused / stopped / stale status banner.
- Green attempts, amber waits/charging, red errors and blue information.
- Quick-action buttons, Up/Down command history and a built-in `help` reference.
- Aliases: `start` for `resume`, `stop` for `pause`, `battery` for `checkbattery`,
  `android` for `reboot`, and `?` for `help`.
- `clear` clears the visible console only; saved logs remain intact.
- A bounded log buffer that respects your position when scrolling back.

Aliases are dashboard commands; the terminal CLI uses canonical command names.
The terminal interface accepts the same operations:

```powershell
py -3 -m honorboot --tools C:\Android\platform-tools --serial YOUR_SERIAL --imei YOUR_IMEI run
py -3 -m honorboot --tools C:\Android\platform-tools --serial YOUR_SERIAL --imei YOUR_IMEI status
py -3 -m honorboot --tools C:\Android\platform-tools --serial YOUR_SERIAL --imei YOUR_IMEI pause
```

For `run`/`resume`, type the displayed data-erasure acknowledgement. The dashboard
instead shows a confirmation dialog. There is no persistent consent flag that
silently authorizes a newly launched process. Routine recovery within an already
authorized run does not request repeated confirmation.

## Identity and progress

The default data directory is `%LOCALAPPDATA%\HonorBoot`, outside this source
directory. Use `--data` to choose another private location.

Each record is keyed by serial, selected IMEI and algorithm version. Disconnecting
one phone and reconnecting it later retains its checkpoint. The controller stays
pinned to the selected serial; it never redirects commands to another attached
phone. To work with a different phone, pause and explicitly select that phone.
Only one controller can operate per data directory. Do not run legacy tools or
another controller with a different data directory against the same phone.

Omitting `--serial` works only when exactly one authorized device is detected.
Omitting `--imei` attempts a legacy Android `iphonesubinfo` read over ADB, validates
the 15-digit Luhn checksum and displays the result before consent. This may fail
on restricted or newer Android versions; it does not defeat those restrictions.
Check the result against `*#06#`. Dual-SIM devices may have two IMEIs; a number
reported by HCU is not automatically preferred or assumed to be hardware truth.
Use `--imei` for an explicit alternative sequence. Existing seeds are never
silently replaced.

Progress, attempt outcomes and accepted codes are local **unencrypted private
data**. Do not commit them, attach them to public issues or share screenshots that
reveal IMEIs, serials or codes. A hash in the folder name is organization, not
encryption. The `.gitignore` is a precaution, not a substitute for reviewing files.

## Automatic recovery and charging

The Python process handles all scheduling locally. No ChatGPT polling is required.

| Event | Behavior |
|---|---|
| Startup / every 6 hours | Return to Android and read actual `dumpsys battery` output |
| Confirmed level below 10% | Stay in Android; stop attempts for at least one hour |
| Hourly charging check at 80% or less | Stay paused; check again after another hour |
| Hourly charging check above 80% | Return to Fastboot and continue the same sequence |
| Connection/read/reboot failure | Wait 5 minutes, attempt normal boot and battery read again |
| Phone absent | Keep the checkpoint, wait for the same serial; no command targets another phone |
| Explicit pause | Stop; no automatic resume |
| Unlock command times out / ambiguous reply | Stop for manual review; never automatically resend or reboot |
| Disk/storage failure | Stop before further phone writes |
| Code accepted | Durably save, stop all attempts, show Windows popup |

The software cannot force a powered-off/disconnected phone to reboot, supply
charging current, or guarantee charging from a PC USB port. Connect an appropriate
charger when needed. Reported battery percentage is a timestamped Android
snapshot, not an independent physical measurement. Between six-hour checks the
phone could still discharge; connection loss triggers recovery. Known frozen
Android battery reports are rejected.

Recovery intentionally continues while the phone is absent, with bounded command
timeouts and status updates. `pause` interrupts waits; a subprocess already in
progress may take up to its 30-second timeout to finish. Repeated absence does not
consume a new code or reset progress. A reboot/Android transition waits up to five
minutes for the selected phone; after that the recovery cycle repeats.

## Persistence and uncertain outcomes

Before each unlock command, an outstanding index is saved. After a definitive
rejection, a journal record is flushed to disk, then the checkpoint advances.
If a crash happens between the two, the journal reconciles the checkpoint at the
next controlled startup. If no definitive result exists, the pending index is
preserved and automatic restart is disabled. This protects against accidentally
repeating an accepted unlock command. Do not manually clear pending fields.

The next group starts only after the bootloader explicitly replies `LOCKED`.
Commands are sequential; the next code is not sent until the prior response.
No artificial per-code or post-reboot delay is used. Durable writes are retained
even though they impose some overhead.

## Import legacy progress

Stop all old scripts first. Both IMEI sequences can be imported separately.

```powershell
py -3 -m honorboot --tools C:\Android\platform-tools --serial YOUR_SERIAL --imei YOUR_IMEI import-legacy --source C:\Private\old-progress.json
```

The original file is never changed. Existing destination records are not
overwritten. Pending/uncertain outcomes and mismatching stored IMEI/step are
rejected. If an older file lacks an IMEI or step, you must verify the supplied
seed yourself. A fresh battery check is required on the first run after import.

## Tests and publishing

```powershell
py -3 -m unittest discover -s tests -v
py -3 -m compileall -q honorboot
```

Tests use fake transports and clocks, never a real phone. They cover recovery,
charging thresholds, device separation, journal reconciliation and ambiguous
unlock results. GitHub Actions runs the suite on Windows. No hardware-specific
CI or guaranteed unlock compatibility is claimed.

Before publishing, review `git status` and ensure only source, tests, documentation,
license and CI files are staged. Never include platform-tools, private runtime
data, backups, account credentials or actual device identifiers. There are no
embedded user-specific serials, IMEIs, paths, unlock codes or service credentials.

Apache-2.0. See `LICENSE` and `NOTICE`.

## Build the executable locally

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-build.txt
.venv\Scripts\python -m PyInstaller --clean --noconfirm --onedir --name HonorBoot launcher.py
```

The result is `dist/HonorBoot/HonorBoot.exe`. Keep the entire HonorBoot folder together. Build dependencies are separate from runtime
dependencies. Never distribute your private data directory with the application.

## Automatic setup and responsiveness

Setup detects platform-tools in saved settings, PATH, Android SDK locations and
nearby folders. Device discovery runs in the background with bounded timeouts.
Saved per-device IMEI sequences are reused; if multiple seeds exist, select one
explicitly. In Android, a legacy IMEI read is attempted when no saved seed exists.
Fastboot detection never reboots the phone just to read an identifier.

The portable distribution uses an application folder instead of extracting a
single-file executable on every launch. Keep `_internal` with the executable.
The dashboard reads at most 16 KiB of new logs per refresh and initially shows
only the recent tail. Status and pause no longer launch an extra process.
