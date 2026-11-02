# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, Gemini CLI, Copilot and others) working on eArcNG.
Human documentation is in [README.md](README.md) / [README.ru.md](README.ru.md).

## Project

eArcNG shows the real eARC/ARC soundbar volume on rooted LG webOS TVs, taken from HDMI-CEC
(`50:7A:xx` <Report Audio Status>). Two display modes:

* `ui native` (webOS ≤ 10.x with QML): a QML patch makes the system LG indicator show the soundbar's number;
* `ui overlay`: the eArcNG app (`com.github.emoorpheus.earcng`) shows a focus-less overlay window.

The app also has the settings screen (EN/RU). Built on trk501/lg-soundbar-volume-osd and
Nikolay1243/Webos-EARC-Volume-Overlay (MIT): keep their copyright lines in [LICENSE](LICENSE).

## Layout

| path | runs on | rules |
|------|---------|-------|
| `backend/cec_volume_bridge.py` | TV, root | Python 3.6+, stdlib only; `--probe`, `--print-config`, `--replay` |
| `backend/remote-setup.sh`, `native-osd.sh`, `95-cec-volume-bridge` | TV, root | POSIX sh for BusyBox ash, no bashisms |
| `backend/ipk.py` | TV (install) and PC (tests) | Python 3 stdlib; builds the app IPK like `ares-package` |
| `app/` (`appinfo.json`, `index.html`, `overlay.js`, `overlay.css`, icons) | TV web app | ES5 (`var`, no arrow functions), no build step, no dependencies |
| `tools/earcng.sh` | PC (macOS, Linux, WSL) | Bash 3.2+ (macOS stock bash): no `mapfile`, `${var,,}`, associative arrays |
| `tests/` | PC, CI | see below |

On the TV: `/home/root/cec-volume/` (backend, `args`, `ui`), hook `/var/lib/webosbrew/init.d/95-cec-volume-bridge`,
app `/media/developer/apps/usr/palm/applications/com.github.emoorpheus.earcng`, logs
`/tmp/cec-volume-bridge.log` (+ `.err`), `/tmp/cec-volume-ui.log` (switch from the settings screen),
`/tmp/earcng-install.log` (app install).

## Tests

```bash
BUSYBOX=/path/to/busybox BASH32=/path/to/bash-3.2 REQUIRE_ALL=1 bash tests/run-all.sh
```

* Seven suites: Python unittest (`tests/test_*.py`), `node tests/test-overlay.cjs`, `tests/test_shell.sh`
  (BusyBox), `tests/test_installer.sh` (ssh stub, also under Bash 3.2), shellcheck (bash and `-s sh`).
  CI (`.github/workflows/tests.yml`) runs the same with `REQUIRE_ALL=1`: a skipped suite is a failure.
* `BUSYBOX` must be a static binary from busybox.net (1.35.0 x86_64 musl in CI). Debian/Ubuntu `busybox-static`
  prefers its own applets over `PATH` and is rejected.
* `tests/fixtures/luna-send` is the shared `luna-send` stub (appInstallService, applicationManager, getVolume).
  Like the real one, it prints nothing when stdin is a pipe.
* The shell suites find and kill their processes with `pgrep -f` / `pkill -f`. While they run, do not start
  commands whose command line contains `cec_volume_bridge` or the suite's temp directory (self-match), and do not
  run two shell suites in parallel.
* Optional previews: `NODE_PATH=… node tests/render-preview.cjs` (playwright + chromium) → `tests/out/*.png`.
* Every change ships with tests. A bug fix gets a test that fails without the fix.

## Rules that matter

* `luna-send` on the TV always runs with `</dev/null` (use `luna()` in `remote-setup.sh`): over ssh its stdin is
  a pipe and it prints nothing.
* The version is in three places and must match (tests check it): `VERSION` in `backend/cec_volume_bridge.py`,
  `version` in `app/appinfo.json`, the footer in `app/index.html`.
* The app id `com.github.emoorpheus.earcng` is in the bridge, `remote-setup.sh`, `tools/earcng.sh` and
  `appinfo.json` (tests check it).
* Native mode safety: firmware files are never modified (copies + `mount --bind` only); the patch is mounted only
  if every replacement matched; if `surface-manager` does not come back, roll back and write `no-native`. Never
  touch TV or soundbar mute.
* `report` and logs must not contain the TV address or other private data. No IP addresses, SSH keys or tokens
  in the repository (tests use `192.0.2.x`).
* Docs: `README.md` and `README.ru.md` change together. New changes go under `## [Unreleased]` in `CHANGELOG.md`;
  release notes are `docs/release-notes/vX.Y.Z.md` (EN + RU).
* TV behavior is described only from real tests on a TV. Anything not checked there is written as not tested.
  Field-tested setup: LG OLED65M59LA (webOS 10.3.0) + Sony HT-A8000, Hearth launcher.

## Working with the maintainer

* The maintainer writes in Russian; answer in Russian. Code comments may be Russian, user-facing CLI output is
  English, the settings screen is EN/RU.
* Commands for the maintainer run in zsh on macOS: no `#` comments inside command blocks.
* Copy a folder's contents with `cp -R src/. dest/` (the `src/` form nests the folder on some systems).
  Safari unzips downloads automatically, so archives get a unique top folder.
* Claim only what was actually run: say which tests passed, and what still needs a check on the TV.

## Release

```bash
git tag -a vX.Y.Z -m "eArcNG X.Y.Z" && git push origin vX.Y.Z
git archive --format=zip --prefix=earcng/ -o earcng-X.Y.Z.zip vX.Y.Z
shasum -a 256 earcng-X.Y.Z.zip > SHA256SUMS
```

Attach the zip and `SHA256SUMS` to the GitHub release; the text is `docs/release-notes/vX.Y.Z.md`.
