# Changelog

All notable changes to eArcNG.

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

[1.0.0]: https://github.com/emoorpheus/earcng/releases/tag/v1.0.0
