#!/usr/bin/env bash
# Интеграционный тест remote-setup.sh + 95-hook + бэкенда в "фейковом корне" с BusyBox (как на ТВ).
#   BUSYBOX=/path/to/busybox tests/test_shell.sh
set -Eeuo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
BB=${BUSYBOX:-/tmp/busybox}
APP_ID=com.github.emoorpheus.earcng
OLD_ID=com.github.nikolay1243.earcvolume
T=$(mktemp -d)
FAILS=0

cleanup() {
	pkill -f "$T/" 2>/dev/null || true
	rm -rf "$T"
}
trap cleanup EXIT
trap 'echo "error at line $LINENO: $BASH_COMMAND" >&2' ERR

ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; FAILS=$((FAILS + 1)); }
check() { local msg=$1; shift; if "$@"; then ok "$msg"; else bad "$msg"; fi; }
procs() { { pgrep -f "$1" || true; } | wc -l | tr -d ' '; }
not() { ! "$@"; }
nprocs() { test "$(procs "$1")" = "$2"; }          # для wait_for: пересчитывается на каждой попытке
osd_state() { test "$(sh "$CEC_DIR/native-osd.sh" status)" = "$1"; }
wait_for() { local _; for _ in $(seq 1 40); do if "$@"; then return 0; fi; sleep 0.1; done; return 1; }
wait_long() { local _; for _ in $(seq 1 150); do if "$@"; then return 0; fi; sleep 0.1; done; return 1; }

# --- фейковый ТВ ---
mkdir -p "$T/bb" "$T/tmp" "$T/var/log" "$T/home/root/cec-volume" "$T/var/lib/webosbrew/init.d"
"$BB" --install -s "$T/bb"
# Заглушки mount/umount ищутся по PATH. Сборки BusyBox с FEATURE_PREFER_APPLETS (busybox-static из
# Debian/Ubuntu) запускают свои апплеты раньше PATH: тест native-режима сломался бы непонятно где.
mkdir -p "$T/probe"
printf '#!/bin/sh\necho path-stub\n' > "$T/probe/mount"
chmod +x "$T/probe/mount"
if [ "$(PATH="$T/probe:$PATH" "$BB" sh -c mount 2>/dev/null | head -n 1)" != path-stub ]; then
	echo "FAIL $BB runs its applets before PATH (FEATURE_PREFER_APPLETS, e.g. Debian/Ubuntu busybox-static)." >&2
	echo "     Use a BusyBox without it, e.g. https://busybox.net/downloads/binaries/1.35.0-x86_64-linux-musl/busybox" >&2
	exit 1
fi
# QML-компоновщик webOS <= 10 (синтетические файлы) + заглушки mount/umount/systemctl/luna-send
VIEWS=$T/usr/lib/qml/WebOSCompositor/views
mkdir -p "$T/usr/lib/qml/WebOSCompositor" "$T/stub" "$T/orig"
cp -R "$ROOT/tests/fixtures/qml/views" "$VIEWS"
echo "/dev/root / squashfs ro 0 0" > "$T/mounts"
export QML_VIEWS=$VIEWS QML_TMP=$T/tmp/cec-volume-qml MOUNTS_FILE=$T/mounts SM_SETTLE=0
export CEC_MOUNTS=$T/mounts CEC_QML_FILE=$VIEWS/volume/StarfishVolume.qml
export FAKE_ORIG=$T/orig FAKE_SYSTEMCTL_LOG=$T/systemctl.log FAKE_SM_FAIL=$T/sm-fail
cat > "$T/stub/mount" <<'EOF_STUB'
#!/bin/sh
# mount --bind SRC DST: содержимое SRC видно по пути DST (копия), строка в MOUNTS_FILE
[ "$1" = --bind ] || exit 1
key=$(printf '%s' "$3" | tr / _)
[ -e "$FAKE_ORIG/$key" ] || cp "$3" "$FAKE_ORIG/$key" || exit 1
cp "$2" "$3" || exit 1
echo "tmpfs $3 tmpfs rw 0 0" >> "$MOUNTS_FILE"
EOF_STUB
cat > "$T/stub/umount" <<'EOF_STUB'
#!/bin/sh
grep -q " $1 " "$MOUNTS_FILE" || { echo "umount: $1: not mounted" >&2; exit 1; }
key=$(printf '%s' "$1" | tr / _)
cp "$FAKE_ORIG/$key" "$1" && rm -f "$FAKE_ORIG/$key"
grep -v " $1 " "$MOUNTS_FILE" > "$MOUNTS_FILE.new"
mv "$MOUNTS_FILE.new" "$MOUNTS_FILE"
EOF_STUB
cat > "$T/stub/systemctl" <<'EOF_STUB'
#!/bin/sh
echo "$*" >> "$FAKE_SYSTEMCTL_LOG"
if [ "$1" = is-active ]; then [ ! -e "$FAKE_SM_FAIL" ]; fi
EOF_STUB
# luna-send: getVolume, appInstallService (IPK -> каталог приложений), applicationManager (getAppInfo, launch)
cp "$ROOT/tests/fixtures/luna-send" "$T/stub/luna-send"
chmod +x "$T/stub/"*
export FAKE_LUNA_LOG=$T/luna.log
export CEC_DIR=$T/home/root/cec-volume HOOKDIR=$T/var/lib/webosbrew/init.d RUNDIR=$T/tmp CEC_ALLOW_NONROOT=1
export TRK_DIR=$T/home/root/volume-osd INSTALL_WAIT=20 REMOVE_WAIT=10 REOPEN_WAIT=5 REOPEN_DELAY=0 REOPEN_PAUSE=0
DEV=$T/media/developer/apps/usr/palm/applications
export APP_BASES="$DEV $T/media/cryptofs/apps/usr/palm/applications"
APP=$DEV/$APP_ID
VER=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$ROOT/backend/cec_volume_bridge.py")
# eARC Volume Overlay (Nikolay1243) с заменённым index.html (штатный - index.html.stock) и своим watcher.js
OLD=$DEV/$OLD_ID
mkdir -p "$OLD/runtime"
printf '{\n  "id": "%s",\n  "version": "1.4.0",\n  "defaultWindowType": "card"\n}\n' "$OLD_ID" > "$OLD/appinfo.json"
echo 'STOCK INDEX' > "$OLD/index.html.stock"
echo 'REPLACED INDEX' > "$OLD/index.html"
: > "$OLD/overlay.js"
: > "$OLD/overlay.css"
echo 'setInterval(function () {}, 1000);' > "$OLD/runtime/watcher.js"
cat > "$OLD/runtime/91-earc-volume-overlay" <<EOF
#!/bin/sh
node "$OLD/runtime/watcher.js" </dev/null >/dev/null 2>&1 &
EOF
chmod +x "$OLD/runtime/91-earc-volume-overlay"
ln -s "$OLD/runtime/91-earc-volume-overlay" "$HOOKDIR/91-earc-volume-overlay"
: > "$T/var/log/cecmessages"
echo "--dry-run --no-cec-device --no-input --no-audio --debug --cec-log $T/var/log/cecmessages --offset-file $T/offset" > "$CEC_DIR/args"
mkdir -p "$T/stage"
cp -R "$ROOT/backend" "$ROOT/app" "$T/stage/"
sh "$HOOKDIR/91-earc-volume-overlay"
wait_for nprocs "$OLD/runtime/watcher.js" 1
export PATH="$T/stub:$T/bb:$PATH"  # дальше - утилиты BusyBox, как на ТВ (+ заглушки)
RS="sh $T/stage/backend/remote-setup.sh"

echo "== install gates"
nothing_installed() { test ! -e "$HOOKDIR/95-cec-volume-bridge" -a ! -e "$CEC_DIR/cec_volume_bridge.py" -a ! -e "$APP"; }
if out=$(FAKE_INSTALL=fail $RS install "$T/stage" 2>&1); then rc=0; else rc=$?; fi
check "appInstallService error -> install fails" test "$rc" != 0
check "  ... reason shown" grep -q '"reason":"FAILED_IPKG_INSTALL"' <<<"$out"
check "  ... explained" grep -q '^error: the eArcNG app was not installed' <<<"$out"
check "  ... nothing installed (backend, hook, app)" nothing_installed
check "  ... eARC Volume Overlay untouched" grep -q 'REPLACED INDEX' "$OLD/index.html"
check "  ... package kept for a manual retry" test -s "$RUNDIR/earcng.ipk"
if out=$(FAKE_INSTALL=hang INSTALL_WAIT=2 $RS install "$T/stage" 2>&1); then rc=0; else rc=$?; fi
check "no answer, no app -> fails after INSTALL_WAIT" test "$rc" != 0
check "  ... explained, nothing installed" test -n "$(grep 'the eArcNG app was not installed' <<<"$out")" -a ! -e "$CEC_DIR/cec_volume_bridge.py"
check "  ... luna-send stopped" wait_for nprocs "appInstallService/dev/install" 0
mkdir -p "$TRK_DIR"
touch "$HOOKDIR/60-volume-osd"
if out=$($RS install "$T/stage" 2>&1); then rc=0; else rc=$?; fi
check "active trk501 refused" test "$rc" != 0
check "refusal names trk501" grep -q 'trk501 lg-soundbar-volume-osd is enabled' <<<"$out"
rm -f "$HOOKDIR/60-volume-osd"
echo 'import time; time.sleep(60)' > "$TRK_DIR/cec-sync.py"   # trk501 запущен вручную, без хука
python3 "$TRK_DIR/cec-sync.py" </dev/null >/dev/null 2>&1 &
TRKPID=$!
wait_for nprocs "$TRK_DIR/cec-sync.py" 1
if out=$($RS install "$T/stage" 2>&1); then rc=0; else rc=$?; fi
check "running trk501 (no hook) refused" test "$rc" != 0 -a -n "$(grep 'trk501 lg-soundbar-volume-osd is running' <<<"$out")"
check "  ... nothing installed" nothing_installed
kill "$TRKPID" 2>/dev/null || true
wait "$TRKPID" 2>/dev/null || true
touch "$HOOKDIR/60-volume-osd"                     # дальше: хук есть, процесса нет -> 'enabled' (поиск не находит сам себя)

echo "== install"
: > "$FAKE_LUNA_LOG"
out=$(: | FORCE=1 $RS install "$T/stage")      # stdin - канал, как у ssh: luna-send должен идти с </dev/null
check "FORCE=1 overrides the trk501 gate" grep -q 'warning: trk501 is enabled (FORCE)' <<<"$out"
touch "$TRK_DIR/disable"
while IFS= read -r line; do echo "     | $line"; done <<<"$out"
check "status after install = running" grep -qx running <<<"$out"
check "app: IPK installed via appInstallService (as ares-install)" \
	grep -qF "luna://com.webos.appInstallService/dev/install {\"id\":\"com.ares.defaultName\",\"ipkUrl\":\"$RUNDIR/earcng.ipk\",\"subscribe\":true}" "$FAKE_LUNA_LOG"
check "  ... app files = app/ + backend/ (for Install/Update on the settings screen)" \
	python3 "$ROOT/backend/ipk.py" same "$ROOT/app" "$APP" "$ROOT/backend"
check "  ... no __pycache__ in the package" test ! -e "$APP/backend/__pycache__"
check "  ... confirmed" grep -qxF "app: eArcNG $VER installed ($APP)" <<<"$out"
check "  ... package removed, luna-send stopped" test ! -e "$RUNDIR/earcng.ipk" -a "$(procs appInstallService/dev/install)" = 0
check "  ... status: app line" grep -qxF "app: eArcNG $VER ($APP)" <<<"$out"
check "eARC Volume Overlay: stock index.html back" grep -qx 'STOCK INDEX' "$OLD/index.html"
check "  ... replaced files removed" test ! -e "$OLD/index.html.stock" -a ! -e "$OLD/overlay.js" -a ! -e "$OLD/overlay.css"
check "  ... 'not needed' note" grep -q '^note: eARC Volume Overlay is not needed with eArcNG' <<<"$out"
check "stock 91 hook removed" test ! -e "$HOOKDIR/91-earc-volume-overlay"
check "95 hook installed (+x)" test -x "$HOOKDIR/95-cec-volume-bridge"
check "stock watcher.js stopped" test "$(procs "$OLD/runtime/watcher.js")" = 0
check "one bridge process" test "$(procs "$CEC_DIR/cec_volume_bridge.py")" = 1

echo "== reinstall: appInstallService without output (buffered) -> files + getAppInfo"
echo '/* changed */' >> "$T/stage/app/overlay.css"
out=$(: | FAKE_INSTALL=silent $RS install "$T/stage")      # stdin - канал (ssh): getAppInfo тоже с </dev/null
check "reinstall -> running" grep -qx running <<<"$out"
check "  ... new files in place" cmp -s "$T/stage/app/overlay.css" "$APP/overlay.css"
check "  ... confirmed via getAppInfo" grep -qF "applicationManager/getAppInfo {\"id\":\"$APP_ID\"}" "$FAKE_LUNA_LOG"
check "  ... without waiting for the timeout" not grep -q 'no confirmation' <<<"$out"
check "  ... luna-send stopped" wait_for nprocs "appInstallService/dev/install" 0
cp "$ROOT/app/overlay.css" "$T/stage/app/overlay.css"

echo "== live CEC log -> overlay update"
sleep 0.5
printf '%s\n' "2026-11-01T20:00:05.000000Z [CECMSG] TM1 RX507A14" >> "$T/var/log/cecmessages"
check "first report = silent baseline 20" wait_for grep -q 'baseline volume 20' "$RUNDIR/cec-volume-bridge.log"
printf '%s\n' "2026-11-01T20:00:06.000000Z [CECMSG] TM2 TX054441" "2026-11-01T20:00:06.150000Z [CECMSG] TM3 RX507A15" >> "$T/var/log/cecmessages"
check "PUBLISH volume 21 in log" wait_for grep -q 'PUBLISH .*"reason": "report".*"volume": 21' "$RUNDIR/cec-volume-bridge.log"
check "key press opened overlay with last real value 20" grep -q 'PUBLISH .*"reason": "key".*"volume": 20' "$RUNDIR/cec-volume-bridge.log"
echo -2 > "$T/offset"
printf '%s\n' "2026-11-01T20:00:09.000000Z [CECMSG] TM4 RX507A1E" >> "$T/var/log/cecmessages"
check "offset file applied live (30-2=28)" wait_for grep -q '"volume": 28' "$RUNDIR/cec-volume-bridge.log"

echo "== idempotent hook"
node "$OLD/runtime/watcher.js" </dev/null >/dev/null 2>&1 &   # чужой watcher.js запущен вручную
wait_for nprocs "$OLD/runtime/watcher.js" 1
sh "$HOOKDIR/95-cec-volume-bridge"
sleep 0.5
check "still one bridge process" test "$(procs "$CEC_DIR/cec_volume_bridge.py")" = 1
check "hook stops a stray stock watcher.js" wait_for nprocs "$OLD/runtime/watcher.js" 0
check "  ... and says so" grep -q 'hook: stopped stock watcher.js' "$RUNDIR/cec-volume-bridge.log"

echo "== disable / enable / restart"
out=$($RS disable)
check "disable -> disabled" grep -qx disabled <<<"$out"
check "bridge stopped" test "$(procs "$CEC_DIR/cec_volume_bridge.py")" = 0
sh "$HOOKDIR/95-cec-volume-bridge"
sleep 0.5
check "hook respects disable flag" test "$(procs "$CEC_DIR/cec_volume_bridge.py")" = 0
node "$OLD/runtime/watcher.js" </dev/null >/dev/null 2>&1 &
wait_for nprocs "$OLD/runtime/watcher.js" 1
out=$($RS enable)
check "enable -> running" grep -qx running <<<"$out"
check "enable stops a stray stock watcher" test "$(procs "$OLD/runtime/watcher.js")" = 0
pid1=$(cat "$RUNDIR/cec-volume-bridge.pid")
out=$($RS restart)
pid2=$(cat "$RUNDIR/cec-volume-bridge.pid")
check "restart -> running with new pid" test "$(head -n1 <<<"$out")" = running -a "$pid1" != "$pid2"

echo "== args"
BASE="--dry-run --no-cec-device --no-input --no-audio --debug --cec-log $T/var/log/cecmessages --offset-file $T/offset"
# shellcheck disable=SC2086  # BASE - список флагов
out=$($RS args $BASE --pos center-right --offset -1)
check "args saved" grep -q -- '--pos center-right --offset -1' "$CEC_DIR/args"
check "args restarts -> running" grep -qx running <<<"$out"
check "config line in log" wait_for grep -q 'config: .*offset=-1 .*position=center-right' "$RUNDIR/cec-volume-bridge.log"
sleep 0.5
printf '%s\n' "2026-11-01T20:00:12.000000Z [CECMSG] TM5 TX054441" "2026-11-01T20:00:12.100000Z [CECMSG] TM6 RX507A1F" >> "$T/var/log/cecmessages"
check "static + file offset (31-1-2=28) and position" wait_for grep -q '"pos": "center-right".*"volume": 28' "$RUNDIR/cec-volume-bridge.log"
before=$(cat "$CEC_DIR/args")
if out=$($RS args --bogus 2>&1); then rc=0; else rc=$?; fi
check "invalid flag rejected" test "$rc" != 0 -a "$(cat "$CEC_DIR/args")" = "$before"
check "rejection is explained" grep -q 'unrecognized arguments: --bogus' <<<"$out"
if out=$($RS args --off 1 2>&1); then rc=0; else rc=$?; fi
check "abbreviation --off is not silently --offset-file" test "$rc" != 0
echo "--bogus-flag" > "$CEC_DIR/args"
out=$($RS restart)
check "bad args file -> stopped" test "$(head -n1 <<<"$out")" = stopped
check "status shows the backend error" grep -q 'unrecognized arguments: --bogus-flag' <<<"$out"
# shellcheck disable=SC2086
$RS args $BASE >/dev/null
check "valid args again -> running" test "$(head -n1 <<<"$($RS status)")" = running

echo "== native OSD: QML patch (trk501 method) on fixture QML"
NO="sh $CEC_DIR/native-osd.sh"
LOGF=$RUNDIR/cec-volume-bridge.log
MARK='// cec-volume-bridge: native volume OSD patch (method: trk501/lg-soundbar-volume-osd)'
patched() { grep -c " $VIEWS/" "$MOUNTS_FILE" || true; }
originals() { diff -r "$ROOT/tests/fixtures/qml/views" "$VIEWS" >/dev/null; }
check "native-osd.sh installed (+x)" test -x "$CEC_DIR/native-osd.sh"
check "status: inactive" test "$($NO status)" = inactive
out=$($RS ui)
check "ui (no arg): mode + check" grep -q '^ui: overlay; QML patch: inactive' <<<"$out"
check "ui (no arg): check says applicable" grep -q '^native OSD: applicable' <<<"$out"
check "check changes nothing" test "$(patched)" = 0 -a ! -e "$QML_TMP"
if out=$($RS ui bogus 2>&1); then rc=0; else rc=$?; fi
check "ui bogus rejected" test "$rc" != 0
out=$($RS ui native)
while IFS= read -r line; do echo "     | $line"; done <<<"$out"
check "ui native warns about the UI restart" grep -q 'screen can go black' <<<"$out"
check "both QML files bind-mounted" test "$(patched)" = 2
for f in volume/StarfishVolume.qml StarfishVolumeView.qml; do
	check "$f = exact trk501 sed output (BusyBox)" cmp -s <(grep -vxF "$MARK" "$VIEWS/$f") "$ROOT/tests/fixtures/qml/trk501/${f##*/}"
	check "$f ends with our marker" test "$(tail -n 1 "$VIEWS/$f")" = "$MARK"
done
check "surface-manager restarted, then checked" grep -qx 'restart surface-manager' "$FAKE_SYSTEMCTL_LOG"
check "  ... and is-active checked" grep -qx 'is-active -q surface-manager' "$FAKE_SYSTEMCTL_LOG"
check "ui file = native" test "$(cat "$CEC_DIR/ui")" = native
check "status: ui native (QML patch active)" grep -qx 'ui: native (QML patch active)' <<<"$out"
check "bridge runs with --ui native" wait_for nprocs "cec_volume_bridge.py .*--ui native" 1
out=$($RS ui native)
check "ui native again: idempotent" grep -q '^native OSD: already active' <<<"$out"
out=$($RS disable 2>"$T/disable.err")
check "disable: state line first (setup screen)" test "$(head -n 1 <<<"$out")" = disabled
check "disable reverts the patch (nothing would update it)" test "$($NO status)" = inactive -a "$(patched)" = 0
check "  ... explained on stderr" grep -q 'reverting the native OSD patch' "$T/disable.err"
out=$($RS enable)
check "enable: running" test "$(head -n 1 <<<"$out")" = running
check "enable re-applies the patch (hook, background)" wait_for osd_state active

echo "== native mode end-to-end (dry-run, fake getVolume: TV counter 17)"
echo 0 > "$T/offset"
n0=$(wc -l < "$LOGF")
logged() { tail -n +"$((n0 + 1))" "$LOGF" | grep -q -- "$1"; }
# shellcheck disable=SC2086
$RS args ${BASE/ --no-audio/} >/dev/null
check "bridge restarted in ui=native" wait_for logged 'config: ui=native'
check "bridge sees the patch" wait_for logged 'native OSD patch active'
sleep 0.5
printf '%s\n' "2026-11-01T20:01:00.000000Z [CECMSG] TM7 RX507A14" >> "$T/var/log/cecmessages"
check "TV counter 17 -> real 20 (setVolume, start)" wait_for logged 'NATIVE setVolume 20 (start)'
printf '%s\n' "2026-11-01T20:01:01.000000Z [CECMSG] TM8 TX054441" "2026-11-01T20:01:01.150000Z [CECMSG] TM9 RX507A15" >> "$T/var/log/cecmessages"
check "key press -> setVolume 21 (key)" wait_for logged 'NATIVE setVolume 21 (key)'
check "overlay app never launched in native mode" not logged 'PUBLISH'

echo "== back to the overlay"
out=$($RS ui overlay)
check "ui overlay: reverting" grep -q '^reverting' <<<"$out"
check "patch removed" test "$($NO status)" = inactive -a "$(patched)" = 0
check "original QML files back" originals
check "status: ui overlay" grep -qx 'ui: overlay' <<<"$out"
check "bridge without --ui native" wait_for nprocs "cec_volume_bridge.py .*--ui native" 0

echo "== switch from the TV settings screen (background; status says 'switching')"
state_is() { test "$(head -n 1 <<<"$($RS status)")" = "$1"; }
out=$($RS status)
check "status: qml: yes (QML compositor found)" grep -qx 'qml: yes' <<<"$out"
check "status: qml: no without QML files (webOS 11)" grep -qx 'qml: no (no QML compositor - webOS 11 Flutter UI?)' <<<"$(QML_VIEWS=$T/no-such-dir $RS status)"
if out=$($RS switch bogus 2>&1); then rc=0; else rc=$?; fi
check "switch bogus rejected" test "$rc" != 0
check "  ... usage" grep -q 'usage: switch native|overlay' <<<"$out"
out=$($RS switch overlay)
check "switch to the current mode: status only" test "$(head -n 1 <<<"$out")" = running -a ! -e "$RUNDIR/cec-volume-ui.pid"
rm -f "$TRK_DIR/disable"
out=$($RS switch native)
check "trk501 enabled -> 'switch error' line, nothing started" grep -qx 'switch error: trk501 lg-soundbar-volume-osd is enabled - disable it first' <<<"$out"
check "  ... status first, nothing mounted" test "$(head -n 1 <<<"$out")" = running -a "$(patched)" = 0 -a ! -e "$RUNDIR/cec-volume-ui.pid"
touch "$TRK_DIR/disable"
: > "$FAKE_SYSTEMCTL_LOG"
out=$(SM_SETTLE=1 $RS switch native)
check "switch native: answers at once with 'switching'" test "$(head -n 1 <<<"$out")" = switching
check "  ... status says 'switching' while it runs" state_is switching
out=$($RS switch overlay)
check "  ... a second switch does not start in parallel" test "$(head -n 1 <<<"$out")" = switching
check "  ... finishes: running" wait_long state_is running
out=$($RS status)
check "  ... system indicator active" grep -qx 'ui: native (QML patch active)' <<<"$out"
check "  ... both QML files bind-mounted, ui file = native" test "$(patched)" = 2 -a "$(cat "$CEC_DIR/ui")" = native
check "  ... surface-manager restarted once" test "$(grep -cx 'restart surface-manager' "$FAKE_SYSTEMCTL_LOG")" = 1
check "  ... output in the switch log" grep -q '^applying: the TV interface' "$RUNDIR/cec-volume-ui.log"
check "  ... bridge runs with --ui native" wait_for nprocs "cec_volume_bridge.py .*--ui native" 1
REOPEN_N="luna://com.webos.applicationManager/launch {\"id\":\"$APP_ID\",\"params\":{\"switched\":\"native\"}}"
check "  ... settings screen reopened with the result" wait_for grep -qxF -e "-n 1 $REOPEN_N" "$FAKE_LUNA_LOG"
check "  ... once the TV UI is back (foreground app first)" \
	test "$(grep -nE 'getForegroundAppInfo|"switched"' "$FAKE_LUNA_LOG" | head -n 1 | grep -c getForegroundAppInfo)" = 1
check "  ... in the switch log" wait_for grep -qx 'settings screen reopened' "$RUNDIR/cec-volume-ui.log"
out=$($RS switch native)
check "switch native again: status only" test "$(head -n 1 <<<"$out")" = running -a "$(grep -cx 'restart surface-manager' "$FAKE_SYSTEMCTL_LOG")" = 1
out=$($RS switch overlay)
check "switch overlay: 'switching'" test "$(head -n 1 <<<"$out")" = switching
check "  ... finishes: running" wait_long state_is running
check "  ... overlay, originals back" test "$(cat "$CEC_DIR/ui")" = overlay -a "$($NO status)" = inactive
check "  ... original QML files" originals
check "  ... status: ui overlay" grep -qx 'ui: overlay' <<<"$($RS status)"
check "  ... settings screen reopened: switched overlay" wait_for grep -qF '"params":{"switched":"overlay"}' "$FAKE_LUNA_LOG"
launches() { grep -c 'applicationManager/launch' "$FAKE_LUNA_LOG" || true; }
wait_for grep -qx 'settings screen reopened' "$RUNDIR/cec-volume-ui.log"
nl=$(launches)
$RS disable >/dev/null
out=$($RS ui native)
check "ui native while disabled: only saved" grep -q '^saved: the bridge is disabled - the system indicator is patched on enable' <<<"$out"
check "  ... nothing mounted, no UI restart" test "$(patched)" = 0 -a "$(grep -cx 'restart surface-manager' "$FAKE_SYSTEMCTL_LOG")" = 2
check "  ... ui file = native" test "$(cat "$CEC_DIR/ui")" = native
out=$($RS switch native)
check "  ... switch native while disabled: status only" test "$(head -n 1 <<<"$out")" = disabled -a "$(patched)" = 0
$RS enable >/dev/null
check "  ... enable applies it (hook, background)" wait_for osd_state active
$RS ui overlay >/dev/null
check "  ... back to the overlay" originals
check "ui from the PC (not the settings screen): nothing reopened" test "$(launches)" = "$nl"

echo "== native OSD: failures and foreign patches"
touch "$FAKE_SM_FAIL"
if out=$($RS ui native 2>&1); then rc=0; else rc=$?; fi
check "surface-manager not back -> ui native fails" test "$rc" != 0
check "  ... explained" grep -q 'patch removed, overlay stays' <<<"$out"
check "  ... rolled back (nothing mounted, originals)" test "$(patched)" = 0
check "  ... original QML files back" originals
check "  ... no-native written" test -e "$CEC_DIR/no-native"
check "  ... ui stays overlay" test "$(cat "$CEC_DIR/ui")" = overlay
check "  ... status: overlay + failed attempt" grep -qx 'ui: overlay (system indicator disabled after a failed attempt)' <<<"$($RS status)"
: > "$FAKE_LUNA_LOG"
out=$(SM_SETTLE=1 $RS switch native)
check "switch native that fails: 'switching' first" test "$(head -n 1 <<<"$out")" = switching
check "  ... finishes: running, overlay, failed attempt" wait_long state_is running
check "  ... status explains" grep -qx 'ui: overlay (system indicator disabled after a failed attempt)' <<<"$($RS status)"
check "  ... settings screen reopened anyway (shows the failure)" wait_for grep -qxF -e "-n 1 $REOPEN_N" "$FAKE_LUNA_LOG"
check "  ... error in the switch log" grep -q '^error: native OSD patch rolled back' "$RUNDIR/cec-volume-ui.log"
rm -f "$FAKE_SM_FAIL"
echo native > "$CEC_DIR/ui"
kill "$(cat "$RUNDIR/cec-volume-bridge.pid")"
wait_for test ! -f "$RUNDIR/cec-volume-bridge.pid"
sh "$HOOKDIR/95-cec-volume-bridge"
sleep 0.5
check "hook: no automatic retry after a failure (no-native)" test "$($NO status)" = inactive
check "  ... status explains" grep -q 'disabled after a failed attempt' <<<"$($RS status)"
rm -f "$CEC_DIR/no-native"
kill "$(cat "$RUNDIR/cec-volume-bridge.pid")"
wait_for test ! -f "$RUNDIR/cec-volume-bridge.pid"
sh "$HOOKDIR/95-cec-volume-bridge"
check "hook (boot): patch applied in the background" wait_for osd_state active
check "  ... bridge started with --ui native" test "$(procs "cec_volume_bridge.py .*--ui native")" = 1
$RS ui overlay >/dev/null
check "reverted again" originals
printf '        text: LS.audioService.volume\n' >> "$VIEWS/volume/StarfishVolume.qml"
cp "$VIEWS/volume/StarfishVolume.qml" "$T/changed.qml"
if out=$($RS ui native 2>&1); then rc=0; else rc=$?; fi
check "changed firmware QML -> refused" test "$rc" != 0
check "  ... reason" grep -q 'not applicable - LS.audioService.volume is used in an unexpected form' <<<"$out"
check "  ... nothing mounted, file untouched" test "$(patched)" = 0 -a "$(cat "$CEC_DIR/ui")" = overlay
check "  ... firmware file untouched" cmp -s "$T/changed.qml" "$VIEWS/volume/StarfishVolume.qml"
out=$($RS switch native)
check "  ... switch native: reason in 'switch error'" grep -q '^switch error: not applicable - LS.audioService.volume is used in an unexpected form' <<<"$out"
check "  ... nothing started" test "$(head -n 1 <<<"$out")" = running -a "$(patched)" = 0
cp "$ROOT/tests/fixtures/qml/views/volume/StarfishVolume.qml" "$VIEWS/volume/StarfishVolume.qml"
if out=$(QML_VIEWS=$T/no-such-dir $NO check); then rc=0; else rc=$?; fi
check "no QML files (webOS 11 Flutter) -> not applicable" test "$rc" != 0
check "  ... reason" grep -q 'QML files not found' <<<"$out"
echo "tmpfs $VIEWS/volume/StarfishVolume.qml tmpfs rw 0 0" >> "$MOUNTS_FILE"
check "foreign bind mount (trk501) detected" test "$($NO status)" = foreign
if out=$($NO apply); then rc=0; else rc=$?; fi
check "  ... apply does not touch it" test "$rc" != 0
if out=$($NO revert); then rc=0; else rc=$?; fi
check "  ... revert does not touch it" test "$rc" != 0 -a "$(patched)" = 1
grep -v " $VIEWS/" "$MOUNTS_FILE" > "$MOUNTS_FILE.new"
mv "$MOUNTS_FILE.new" "$MOUNTS_FILE"
# shellcheck disable=SC2086
$RS args $BASE >/dev/null

echo "== SIGTERM cleans pidfile"
kill "$(cat "$RUNDIR/cec-volume-bridge.pid")"
check "pidfile removed on SIGTERM" wait_for test ! -f "$RUNDIR/cec-volume-bridge.pid"
out=$($RS status)
check "status -> stopped" test "$(head -n1 <<<"$out")" = stopped
$RS enable >/dev/null
out=$($RS args --clear)
while IFS= read -r line; do echo "     | $line"; done <<<"$out"
check "args --clear" test ! -s "$CEC_DIR/args"
check "defaults -> running" grep -qx running <<<"$out"

echo "== uninstall"
$RS ui native >/dev/null
check "native patch active before uninstall" test "$(sh "$CEC_DIR/native-osd.sh" status)" = active
: > "$FAKE_LUNA_LOG"
out=$(: | $RS uninstall)
while IFS= read -r line; do echo "     | $line"; done <<<"$out"
check "uninstall reverted the QML patch" test "$(patched)" = 0
check "  ... original QML files back" originals
check "uninstall output" grep -qx uninstalled <<<"$out"
check "eArcNG app removed via appInstallService" grep -qxF -e "-n 1 luna://com.webos.appInstallService/dev/remove {\"id\":\"$APP_ID\"}" "$FAKE_LUNA_LOG"
check "  ... waited until it is gone" test ! -e "$APP" -a -n "$(grep -x 'app: eArcNG removed' <<<"$out")"
check "eARC Volume Overlay: stock index.html" grep -qx 'STOCK INDEX' "$OLD/index.html"
check "  ... no replaced files" test ! -e "$OLD/overlay.js" -a ! -e "$OLD/overlay.css" -a ! -e "$OLD/index.html.stock"
check "  ... stock 91 hook restored" test -L "$HOOKDIR/91-earc-volume-overlay"
check "  ... stock watcher running again" wait_for nprocs "$OLD/runtime/watcher.js" 1
check "backend removed and stopped" test ! -e "$CEC_DIR" -a "$(procs "cec_volume_bridge.py")" = 0
check "95 hook removed" test ! -e "$HOOKDIR/95-cec-volume-bridge"

echo "== Homebrew Channel: the app package alone -> Install / Update on the settings screen"
# Homebrew Channel ставит IPK (бэкенд внутри, файлы 0644) через appInstallService, как здесь заглушка
python3 "$ROOT/backend/ipk.py" build "$ROOT/app" "$T/hb.ipk" "$ROOT/backend" >/dev/null
luna-send -n 50 luna://com.webos.appInstallService/dev/install \
	"{\"id\":\"$APP_ID\",\"ipkUrl\":\"$T/hb.ipk\",\"subscribe\":true}" </dev/null >"$T/hb.log" 2>&1 &
hb=$!
check "app installed from the package" wait_long grep -q '"state":"installed"' "$T/hb.log"
kill "$hb" 2>/dev/null || true
wait "$hb" 2>/dev/null || true
check "  ... backend inside, not executable (like an IPK)" test -f "$APP/backend/remote-setup.sh" -a ! -x "$APP/backend/remote-setup.sh"
HB="sh $APP/backend/remote-setup.sh"
mkdir -p "$CEC_DIR"
echo "$BASE" > "$CEC_DIR/args"                  # тест: dry-run без шины, ввода и аудио
out=$($HB status)
check "status from the app package: missing" test "$(head -n 1 <<<"$out")" = missing
check "  ... offers the bundled backend" grep -qxF "bundled: backend $VER in the app - Install on the settings screen" <<<"$out"
check "  ... no backend/update lines" not grep -qE '^(backend|update): ' <<<"$out"
mv "$OLD/index.html" "$OLD/index.html.stock"     # eARC Volume Overlay снова с заменённым index.html и watcher.js
echo 'REPLACED INDEX' > "$OLD/index.html"
: > "$OLD/overlay.js"
out=$(: | $HB activate 2>"$T/activate.err")
while IFS= read -r line; do echo "     | $line"; done < "$T/activate.err"
check "activate (Install): running" test "$(head -n 1 <<<"$out")" = running
check "  ... eARC Volume Overlay: stock index.html back" \
	test "$(cat "$OLD/index.html")" = 'STOCK INDEX' -a ! -e "$OLD/index.html.stock" -a ! -e "$OLD/overlay.js"
check "  ... its 91 hook removed" test ! -e "$HOOKDIR/91-earc-volume-overlay"
check "  ... its watcher.js stopped" wait_for nprocs "$OLD/runtime/watcher.js" 0
check "  ... explained on stderr" \
	grep -qxF "backend installed from the app: $VER ($CEC_DIR, hook $HOOKDIR/95-cec-volume-bridge)" "$T/activate.err"
same_backend() { for f in cec_volume_bridge.py remote-setup.sh native-osd.sh; do cmp -s "$APP/backend/$f" "$CEC_DIR/$f" || return 1; done; }
check "  ... files from the app package" same_backend
check "  ... hook installed (+x)" test -x "$HOOKDIR/95-cec-volume-bridge"
check "  ... status: backend version" grep -qxF "backend: $VER" <<<"$out"
check "  ... nothing to install or update" not grep -qE '^(bundled|update): ' <<<"$out"
check "  ... one bridge process" wait_for nprocs "$CEC_DIR/cec_volume_bridge.py" 1
check "  ... args kept" grep -q -- '--dry-run' "$CEC_DIR/args"
echo '# newer' >> "$APP/backend/native-osd.sh"         # Homebrew Channel обновил приложение
out=$($HB status)
check "app updated -> status offers the update" \
	grep -qxF "update: backend $VER in the app, installed $VER - Update on the settings screen" <<<"$out"
check "  ... the installed script says the same" grep -q '^update: ' <<<"$(sh "$CEC_DIR/remote-setup.sh" status)"
pid1=$(cat "$RUNDIR/cec-volume-bridge.pid")
out=$($HB activate 2>"$T/activate.err")
check "activate (Update): running" test "$(head -n 1 <<<"$out")" = running
check "  ... nothing left to update" not grep -q '^update: ' <<<"$out"
check "  ... explained" grep -qxF "backend updated from the app: $VER -> $VER" "$T/activate.err"
check "  ... bridge restarted" test "$(cat "$RUNDIR/cec-volume-bridge.pid")" != "$pid1"
check "  ... new file in place" cmp -s "$APP/backend/native-osd.sh" "$CEC_DIR/native-osd.sh"
sh "$CEC_DIR/remote-setup.sh" disable >/dev/null 2>&1
echo '# newest' >> "$APP/backend/95-cec-volume-bridge"
check "  ... a changed hook is an update too" grep -q '^update: ' <<<"$($HB status)"
node "$OLD/runtime/watcher.js" </dev/null >/dev/null 2>&1 &
wait_for nprocs "$OLD/runtime/watcher.js" 1
out=$($HB activate 2>"$T/activate.err")
check "update while disabled: stays disabled" test "$(head -n 1 <<<"$out")" = disabled -a "$(procs "$CEC_DIR/cec_volume_bridge.py")" = 0
check "  ... stray stock watcher.js stopped (no hook run)" wait_for nprocs "$OLD/runtime/watcher.js" 0
check "  ... explained" grep -q 'stays disabled' "$T/activate.err"
check "  ... new hook in place" cmp -s "$APP/backend/95-cec-volume-bridge" "$HOOKDIR/95-cec-volume-bridge"
mkdir -p "$T/nocmp"                               # ТВ без cmp: сравнивает python3
for f in "$T"/stub/* "$T"/bb/*; do
	n=${f##*/}
	if [ "$n" != cmp ] && [ ! -e "$T/nocmp/$n" ]; then ln -s "$f" "$T/nocmp/$n"; fi
done
ln -s "$(command -v python3)" "$T/nocmp/python3"
check "no cmp: python3 compares (same files)" not grep -q '^update: ' <<<"$(PATH=$T/nocmp $HB status)"
echo '# changed' >> "$APP/backend/cec_volume_bridge.py"
check "  ... and sees a change" grep -q '^update: ' <<<"$(PATH=$T/nocmp $HB status)"
if out=$(sh "$CEC_DIR/remote-setup.sh" activate 2>&1); then rc=0; else rc=$?; fi
check "activate outside the app package refused" test "$rc" != 0
check "  ... explained" grep -q 'activate runs from the eArcNG app package' <<<"$out"
mkdir -p "$T/other.app/backend"                    # backend/ чужого приложения
cp "$APP/appinfo.json" "$T/other.app/"
cp "$APP"/backend/* "$T/other.app/backend/"
if out=$(sh "$T/other.app/backend/remote-setup.sh" activate 2>&1); then rc=0; else rc=$?; fi
check "  ... also from another app's backend/" test "$rc" != 0 -a -n "$(grep 'activate runs from the eArcNG app package' <<<"$out")"
rm -f "$TRK_DIR/disable"
if out=$($HB activate 2>&1); then rc=0; else rc=$?; fi
check "activate with an active trk501 refused" test "$rc" != 0 -a -n "$(grep 'trk501 lg-soundbar-volume-osd is enabled' <<<"$out")"
touch "$TRK_DIR/disable"
cp "$APP/backend/cec_volume_bridge.py" "$T/bridge-copy.py"
rm -f "$APP/backend/cec_volume_bridge.py"
if out=$($HB activate 2>&1); then rc=0; else rc=$?; fi
check "incomplete app package refused" test "$rc" != 0 -a -n "$(grep 'the app package has no cec_volume_bridge.py' <<<"$out")"
out=$(: | sh "$CEC_DIR/remote-setup.sh" uninstall)
check "uninstall after Homebrew Channel install: app and backend gone" \
	test -n "$(grep -x uninstalled <<<"$out")" -a ! -e "$APP" -a ! -e "$CEC_DIR" -a ! -e "$HOOKDIR/95-cec-volume-bridge"

echo
if [ "$FAILS" -eq 0 ]; then echo "shell integration: ALL PASSED"; else echo "shell integration: $FAILS FAILED"; exit 1; fi
