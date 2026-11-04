# Changelog

All notable changes to eArcNG.

## [Unreleased]

Checked on one TV after the 1.1.1 release (LG OLED65M59LA, webOS 10.3.0, Homebrew Channel 0.7.3; the buttons were
pressed with key events over SSH, not with a remote): updating from the `main` address. The package page showed
`1.1.1 (installed: 1.1.0)`; UPDATE → "Application installed: eArcNG"; the settings screen offered Update (backend
1.1.1 in the app, 1.1.0 installed) and after it showed the bridge running, without the hint. The bridge log: 1.1.0
stopped, 1.1.1 started 1.2 s later, the native indicator patch stayed active, no errors; the TV did not restart.

Not checked on a TV yet: a fresh install from the `main` address.

### Changed
- README, AGENTS: updating from the `main` address is marked as checked.

## [1.1.1] - 2026-11-04

Documentation only: the bridge and the app work as in 1.1.0. Checked on one TV (LG OLED65M59LA, webOS 10.3.0,
Homebrew Channel 0.7.3):
- The repository address from `main` in Homebrew Channel: eArcNG in the list with its icon, the package page with
  the description (EN + RU) and the three pictures, scrolled to the end.
- 1.1.0 after two restarts of the TV: the hook started the bridge 36–39 s after boot, the native indicator patch
  was active, no errors in the bridge log.

Not checked on a TV yet: installing and updating from the `main` address, standby, the hints for Homebrew Channel
errors, mute with `eim`, `auto` when the bus works but the soundbar does not answer, webOS 11.

### Added
- Homebrew Channel package page: a description (EN + RU) with three pictures from `docs/` (settings screen, badge,
  pill and ring): `docs/hb-description.html`, `fullDescriptionUrl` in `repo.json`. Before, Homebrew Channel 0.7.3
  on OLED65M59LA showed an empty description (the `shortDescription` text was not visible).

## [1.1.0] - 2026-11-04

Checked on one TV (LG OLED65M59LA, webOS 10.3.0, Homebrew Channel 0.7.3) + Sony HT-A8000 over SSH; volume steps
were sent through the TV's audio service, not with a remote:
- `check` shows `EIM getArcVolume: Volume N` with the soundbar's level. `install` from a PC over 1.0.0 keeps `args`
  and `ui native`; `status` shows `backend: 1.1.0` and no `update:`. `uninstall` and a fresh `install` work.
- `--arc-source eim` (dry run next to `cec`): the value came 0.18–0.4 s after a step (CEC on the same step:
  0.16 s); in a fast series (3 steps 0.25 s apart) values in between were skipped, the last one was right. `auto`
  without the CEC device (`--no-cec-device`) logs `arc source: eim getArcVolume (no CEC device in use)` and shows
  the same values; with the bus working `auto` did not ask the TV.
- Homebrew Channel with a test repository on the local network (an `http://` address): eArcNG in the list with its
  icon, the details page from the manifest, INSTALL with the hash check. The settings screen showed "Not
  installed" with **Install**; after **Install** the bridge was running within ~3 s. A test 1.1.1 package installed
  through the Homebrew Channel service (the way it installs updates) brought **Update**; after it `backend: 1.1.1`,
  `args` kept. With the app removed in Homebrew Channel the bridge keeps running and logs `launch rejected` once
  per 5 minutes.
- A disabled bridge and **Update**: disabled with `ui native` through the Homebrew Channel service (the screen's
  command), the patch was reverted and the bridge stopped. Test packages 1.1.1 and then 1.1.0 again, installed
  through the Homebrew Channel service: **Update** on the screen brought `backend: 1.1.1`, then `backend: 1.1.0`;
  the bridge stayed disabled, the patch inactive, `args` kept. **Enable** on the screen started the bridge and
  applied the patch again.
- Switching the indicator on the screen both ways (`native` → `overlay` → `native`): each time the TV interface
  restarted and the screen reopened with the result; the patch was reverted and applied again.

Not checked on a TV yet: the repository address from `main` (it needs the release manifest), the start after a
reboot, standby, the hints for Homebrew Channel errors, mute with `eim`, `auto` when the bus works but the
soundbar does not answer, webOS 11.

### Added
- Second volume source `--arc-source auto|cec|eim` (default `auto`): `luna://com.webos.service.eim/getArcVolume`,
  Nikolay1243's idea from v1.4.0. `auto` asks the TV only while the CEC bus brings no `50:7A` (no `/dev/cec*`, the
  monitor does not open, no reply 1 s after a key press; the first 2 s after start it waits for the bus); `eim`
  takes values only from it. Polling: every 150 ms for 2.5 s after a key press or a change, otherwise every 1 s;
  5 failures in a row → `getArcVolume unavailable (…)`, retried in 60 s. The log says which source is in use
  (`arc source: …`). Mute for these values comes from the TV's `muteStatus`.
- `--probe` (and so `check` / `report`): the line `EIM getArcVolume: Volume N …` or `… unavailable (…)`.
- Install from Homebrew Channel: the app package carries the backend (`backend/` in the app); `repo.json` in the
  repository root for Homebrew Channel. The settings screen shows **Install** when the backend is not installed
  and **Update** when the app brings another backend (`remote-setup.sh activate`, root through Homebrew Channel);
  `status` prints `backend:`, `bundled:` and `update:` lines.
- `tools/make-release.py`: the IPK, the Homebrew Channel manifest (`ipkUrl`, `ipkHash.sha256`, `ipkSize`), the
  source zip from the tag and `SHA256SUMS`; `--draft` and `--base-url` for a test repository.
- Settings screen: plain hints for known Homebrew Channel errors (root access refused, not root, service not
  found, trk501 running, no `python3`) in EN/RU, the TV's error text after it.

### Changed
- `install` from the PC builds the app package with the backend inside, like the Homebrew Channel package.
- The bridge logs a refused overlay launch (`launch rejected: … - is the eArcNG app installed?`) once per 5 minutes
  with the number of repeats, not on every key press.

### Fixed
- **Install** / **Update** on the settings screen under Homebrew Channel: `remote-setup.sh` read every process with
  its own `tr` (3–4 s per pass there), so `activate` took ~13 s against the screen's 15 s limit. Now one `python3`
  pass over `/proc` (also when stopping the stock `watcher.js`), and the screen waits up to 60 s for actions
  (15 s for `status`). On the TV the bridge was running within ~3 s after **Install**.
- Overlay on a cold start: the hide timer ran from the app start, but the window can reach the screen much later.
  On OLED65M59LA, with launches like the bridge's, one series had the window on screen ~0.1 s after the app start,
  another only at ~1.4 s after the launch while `visibilitychange` came at ~0.35 s (no frames in between): the
  overlay stayed ~0.4 s + fade. Now the time counts from the first frame after such a pause
  (`requestAnimationFrame`), otherwise from `visibilitychange`; hiding waits for the window at most 3 s from the
  start; presses extend it as before. On the TV after the fix (`ui overlay`): on screen ~1.5 s + fade. Present
  since 1.0.0.
- `tools/earcng.sh` `status` / `log` / `restart` / `args` / `ui` / `uninstall` / `monitor` on a TV without the
  backend: a clear message with the way to install instead of `sh: can't open …`; with only the app installed,
  `status`, `log` and `uninstall` use the backend inside the app. Checked on the TV.
- Settings screen: after **Update** no button had focus (the arrows brought it back). On the TV a focused button
  that gets hidden loses focus at once, so the check after hiding never matched. Now the screen remembers the
  focused button before hiding it and moves focus to **Enable** / **Disable**. The indicator buttons, hidden while
  the mode switches, had the same check since 1.0.0. Checked on the TV after the fix: after **Update** the focus
  was on **Enable**, after the second press on an indicator button (both ways) on **Disable**.

## [1.0.0] - 2026-11-02

The first release. Field-tested by the owner on LG OLED65M59LA (webOS 10.3.0) + Sony HT-A8000 with the Hearth
launcher: install, icon, `uninstall` and a fresh install of the eArcNG app, both indicator modes, switching them on
the settings screen with the screen reopening, all settings screen buttons, `report`.

### Added
- Bridge `backend/cec_volume_bridge.py` (Python 3.6+, stdlib only): shows only the volume the soundbar reported over
  HDMI-CEC (`50:7A:xx` <Report Audio Status>). Reads `/dev/cec0` in real time (fallback: `/var/log/cecmessages`),
  sends `05:45` right on key release (fast-tap guard) and a final `05:71` query after a series of presses; scale,
  max and offset for other soundbars.
- `ui native` (webOS ≤ 10.x with QML): the system LG indicator shows the soundbar's real number and bar instead of
  "+/−" (trk501's QML patch method: copies attached with bind mounts, checked before mounting, rollback if the TV
  interface does not come back, feedback and rejection guards).
- `ui overlay`: a focus-less overlay window (badge, pill or ring) next to the native ARC column. It hides 1500 ms
  after the last key press like the native indicator and stays the full time after a slow app launch.
- The **eArcNG** app (`com.github.emoorpheus.earcng`, own icon): `install` builds its package on the TV
  (`backend/ipk.py`) and installs it with `appInstallService`; if the TV refuses, nothing else is changed.
  `uninstall` removes it. Nikolay1243's eARC Volume Overlay is not needed: its `watcher.js` (the TV key-press
  counter) is turned off.
- Settings screen in English or Russian, following the TV language: status ring, Enable/Disable, Test and the
  System / Overlay choice with confirmation; after a switch the screen opens again by itself and shows the result.
- `tools/earcng.sh` on the PC (Bash 3.2+, macOS too): `check`, `try`, `install`, `args`, `ui`, `status`, `log`,
  `restart`, `monitor`, `report`, `uninstall`.
- `report`: TV model, firmware, versions, check, status and log in one text for a GitHub issue (no TV address),
  and an issue form for it.
- Tests without a TV (Python, Node, BusyBox ash, ssh stub, Bash 3.2, shellcheck), run in CI.

[Unreleased]: https://github.com/emoorpheus/earcng/compare/v1.1.1...HEAD
[1.1.1]: https://github.com/emoorpheus/earcng/releases/tag/v1.1.1
[1.1.0]: https://github.com/emoorpheus/earcng/releases/tag/v1.1.0
[1.0.0]: https://github.com/emoorpheus/earcng/releases/tag/v1.0.0
