#!/usr/bin/env bash
# Тест tools/earcng.sh без ТВ: ssh подменён заглушкой, "ТВ" = фейковый корень + BusyBox.
set -Eeuo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
BB=${BUSYBOX:-/tmp/busybox}
APP_ID=com.github.emoorpheus.earcng
OLD_ID=com.github.nikolay1243.earcvolume
T=$(mktemp -d)
cleanup() { pkill -f "$T/" 2>/dev/null || true; rm -rf "$T" /tmp/cec-volume-stage; }
trap cleanup EXIT
FAILS=0
ok() { echo "  ok   $*"; }
bad() { echo "  FAIL $*"; FAILS=$((FAILS + 1)); }
check() { local msg=$1; shift; if "$@"; then ok "$msg"; else bad "$msg"; fi; }
show() { while IFS= read -r line; do printf '     | %s\n' "$line"; done <<<"$1"; }

mkdir -p "$T/bb" "$T/bin" "$T/tmp" "$T/var/log" "$T/home/root/cec-volume" "$T/var/lib/webosbrew/init.d"
"$BB" --install -s "$T/bb"
cp -R "$ROOT/tests/fixtures/qml/views" "$T/views"                  # QML-компоновщик (webOS <= 10)
echo "/dev/root / squashfs ro 0 0" > "$T/mounts"
APP=$T/media/developer/apps/usr/palm/applications/$APP_ID
OLD=$T/media/developer/apps/usr/palm/applications/$OLD_ID         # eARC Volume Overlay (штатный)
mkdir -p "$OLD"
printf '{\n  "id": "%s",\n  "version": "1.4.0",\n  "defaultWindowType": "card"\n}\n' "$OLD_ID" > "$OLD/appinfo.json"
echo 'STOCK' > "$OLD/index.html"
: > "$T/var/log/cecmessages"
SAFE="--dry-run --no-cec-device --no-input --no-audio --cec-log $T/var/log/cecmessages"
echo "$SAFE" > "$T/home/root/cec-volume/args"
# заглушка ssh: пропускает опции и хост, выполняет команду локально в окружении "ТВ"
cat > "$T/bin/ssh" <<STUB
#!/bin/sh
while [ \$# -gt 0 ]; do
	case \$1 in -o|-i) shift 2 ;; -t) echo tty >> "$T/ssh-tty"; shift ;; *) break ;; esac
done
echo "\$1" >> "$T/ssh-hosts"; shift
export PATH="$T/bb:\$PATH" CEC_DIR="$T/home/root/cec-volume" HOOKDIR="$T/var/lib/webosbrew/init.d" RUNDIR="$T/tmp"
export APP_BASES="$T/media/developer/apps/usr/palm/applications" CEC_ALLOW_NONROOT=1 TRK_DIR="$T/home/root/volume-osd"
export QML_VIEWS="$T/views" MOUNTS_FILE="$T/mounts" FAKE_LUNA_LOG="$T/luna.log"
# RDIR в earcng.sh жёстко /home/root/cec-volume - перенаправляем в фейковый корень
exec sh -c "\$(printf '%s ' "\$@" | sed "s#/home/root/cec-volume#$T/home/root/cec-volume#g")"
STUB
chmod +x "$T/bin/ssh"
# luna-send (appInstallService, applicationManager, getSystemInfo); со stdin-каналом ничего не выводит, как на ТВ
cp "$ROOT/tests/fixtures/luna-send" "$T/bin/luna-send"
chmod +x "$T/bin/luna-send"
export PATH="$T/bin:$PATH"
INST=$ROOT/tools/earcng.sh

echo "== check"
out=$("$INST" root@192.0.2.10 check 2>&1)
show "$out"
check "python3 detected" grep -q 'python3: 3\.' <<<"$out"
check "CEC probe ran (no device here)" grep -q 'CEC: no /dev/cec\*' <<<"$out"
check "eArcNG app: not installed yet" grep -qx 'eArcNG app: not installed yet (install builds and installs it)' <<<"$out"
check "eARC Volume Overlay found: not needed" grep -q "^eARC Volume Overlay (Nikolay1243): $OLD (version 1.4.0) - not needed: install turns off its watcher.js" <<<"$out"
check "QML compositor detected" grep -q 'UI: QML compositor .*(ui native)' <<<"$out"
check "native OSD patch checked from stage" grep -q '^native OSD: applicable' <<<"$out"
check "check leaves no stage" test ! -d /tmp/cec-volume-stage

echo "== install gate passes FORCE through"
touch "$T/var/lib/webosbrew/init.d/60-volume-osd"
if out=$("$INST" root@192.0.2.10 install 2>&1); then rc=0; else rc=$?; fi
check "active trk501 blocks install" test "$rc" != 0
check "reason shown" grep -q 'trk501 lg-soundbar-volume-osd is enabled' <<<"$out"
out=$(FORCE=1 SSH_KEY=/dev/null "$INST" root@192.0.2.10 install 2>&1)
rm -f "$T/var/lib/webosbrew/init.d/60-volume-osd"
show "$(tail -n 4 <<<"$out")"
check "FORCE=1 -> installed and running" grep -qx running <<<"$out"
check "eArcNG app installed (IPK built on the TV)" diff -r "$ROOT/app" "$APP"
check "eARC Volume Overlay untouched" test "$(cat "$OLD/index.html")" = STOCK -a ! -e "$OLD/index.html.stock"
check "stage removed" test ! -d /tmp/cec-volume-stage
check "host passed to ssh" grep -qx 'root@192.0.2.10' "$T/ssh-hosts"
st=$("$INST" root@192.0.2.10 status)
check "status = running" test "$(head -n1 <<<"$st")" = running

echo "== args"
# shellcheck disable=SC2086  # SAFE - список флагов
out=$("$INST" root@192.0.2.10 args $SAFE --position center-right --offset -1)
check "args saved on the TV" grep -q -- '--position center-right --offset -1' "$T/home/root/cec-volume/args"
check "bridge restarted" grep -qx running <<<"$out"
if out=$("$INST" root@192.0.2.10 args --pos 2>&1); then rc=0; else rc=$?; fi
check "invalid args -> error exit" test "$rc" != 0
check "args file unchanged" grep -q -- '--position center-right' "$T/home/root/cec-volume/args"

echo "== ui (passthrough)"
out=$("$INST" root@192.0.2.10 ui 2>&1)
show "$out"
check "ui: current mode + check" grep -q '^ui: overlay; QML patch: inactive' <<<"$out"
check "ui: patch applicable" grep -q '^native OSD: applicable' <<<"$out"
if out=$("$INST" root@192.0.2.10 ui bogus 2>&1); then rc=0; else rc=$?; fi
check "ui bogus -> error exit" test "$rc" != 0
check "  ... usage shown" grep -q 'usage: ui \[native|overlay\]' <<<"$out"

echo "== try (dry run from stage, auto-exit)"
: > "$T/ssh-tty"
out=$(TRY_SECONDS=2 "$INST" root@192.0.2.10 try --no-input --no-audio --no-cec-device --cec-log "$T/var/log/cecmessages" 2>&1)
show "$(grep -E 'start|config|exit-after|stop' <<<"$out")"
check "dry run started" grep -q 'config: .*DRY-RUN' <<<"$out"
check "auto-exit after TRY_SECONDS" grep -q 'exit-after 2s reached' <<<"$out"
check "ssh -t used for try" test -s "$T/ssh-tty"
check "stage removed after try" test ! -d /tmp/cec-volume-stage
check "installed bridge untouched" test "$(head -n1 <<<"$("$INST" root@192.0.2.10 status)")" = running

echo "== report (for a GitHub issue; without the TV address)"
# как на OLED65M59LA: со stdin-каналом (sh -s через ssh) luna-send ничего не выводит
# QML-патч ТВ виден и в jail приложений (распространение mount): в отчёте - только числом
for j in "" /mnt/lg/user/var/palm/jail/a /mnt/lg/user/var/palm/jail/a /mnt/lg/flash/data/var/palm/jail/b; do
	echo "tmpfs $j/usr/lib/qml/WebOSCompositor/views/volume/StarfishVolume.qml tmpfs rw 0 0"
done >> "$T/mounts"
printf '#!/bin/sh\ncat > "%s/clipboard"\n' "$T" > "$T/bin/pbcopy"
chmod +x "$T/bin/pbcopy"
ver=$(sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$ROOT/backend/cec_volume_bridge.py")
if out=$(REPORT_FILE=$T/report.txt "$INST" root@192.0.2.10 report 2>&1); then rc=0; else rc=$?; fi
R=$T/report.txt
show "$(head -n 8 "$R")"
check "report: exit 0" test "$rc" = 0
check "report: file = what was shown" test "$(head -n "$(wc -l < "$R")" <<<"$out")" = "$(cat "$R")"
check "  ... header with the installer version" grep -q "^eArcNG report: .*, installer $ver\$" "$R"
check "  ... TV model and firmware" grep -q '^system info: .*"modelName":"OLED55C46LA","firmwareVersion":"33.21.65"' "$R"
check "  ... installed bridge version" grep -qx "installed bridge: $ver" "$R"
check "  ... QML bind-mounts: TV path + jail copies as a number" \
	grep -qx 'QML bind-mounts: /usr/lib/qml/WebOSCompositor/views/volume/StarfishVolume.qml (+3 in app jails)' "$R"
check "  ... preflight without the address" grep -qx '== preflight on the TV' "$R"
check "  ... eArcNG app and its version" grep -qxF "eArcNG app: $APP (version $ver)" "$R"
check "  ... python3 and the QML patch check" test -n "$(grep '^python3: 3\.' "$R")" -a -n "$(grep '^native OSD: applicable' "$R")"
check "  ... status: running, qml: yes" test "$(grep -A1 -x '== status' "$R" | tail -n 1)" = running -a -n "$(grep -x 'qml: yes' "$R")"
check "  ... log section" test -n "$(sed -n '/^== log$/,$p' "$R" | grep 'config: ')"
check "  ... no TV address anywhere" test -z "$(grep -F '192.0.2.10' "$R")"
check "  ... stage removed" test ! -d /tmp/cec-volume-stage
check "  ... copied to the clipboard (pbcopy)" cmp -s "$R" "$T/clipboard"
check "  ... where to paste it" grep -qF 'https://github.com/emoorpheus/earcng/issues/new?template=report.yml' <<<"$out"

echo "== uninstall / usage"
check "uninstall" grep -qx uninstalled <<<"$("$INST" root@192.0.2.10 uninstall)"
check "  ... eArcNG app removed" test ! -e "$APP"
check "  ... eARC Volume Overlay stays stock" test "$(cat "$OLD/index.html")" = STOCK
out=$(cd "$T" && "$INST" root@192.0.2.10 report 2>&1)
check "report without the backend: bridge 'none'" grep -qx 'installed bridge: none' "$T/earcng-report.txt"
check "  ... status: missing (default file name, current folder)" \
	test "$(grep -A1 -x '== status' "$T/earcng-report.txt" | tail -n 1)" = missing
if out=$("$INST" 2>&1); then rc=0; else rc=$?; fi
check "no args -> usage, exit 2" test "$rc" = 2
check "  ... usage lists report" grep -q 'report' <<<"$out"

echo
if [ "$FAILS" -eq 0 ]; then echo "installer test: ALL PASSED"; else echo "installer test: $FAILS FAILED"; exit 1; fi
