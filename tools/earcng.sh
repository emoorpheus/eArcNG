#!/usr/bin/env bash
# earcng.sh - eArcNG: установка и управление на rooted LG webOS TV по SSH (на ПК: Linux / macOS / WSL).
#   ./tools/earcng.sh root@<TV-IP> [check|try|install|status|log|monitor|args|ui|restart|report|uninstall] [FLAGS...]
#   ./tools/earcng.sh root@<TV-IP> report                              # отчёт для issue на GitHub (без IP)
#   SSH_KEY=~/.ssh/id_rsa_webos ./tools/earcng.sh root@<TV-IP> try     # dry-run без установки
#   ./tools/earcng.sh root@<TV-IP> ui native                           # число в СИСТЕМНОМ индикаторе (webOS <= 10)
#   ./tools/earcng.sh root@<TV-IP> ui overlay                          # назад к оверлею
#   ./tools/earcng.sh root@<TV-IP> args --visible-ms 1500              # проверить, сохранить, перезапустить
set -Eeuo pipefail
IFS=$'\n\t'

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
readonly ROOT
readonly STAGE=/tmp/cec-volume-stage
readonly RDIR=/home/root/cec-volume
readonly APP_ID=com.github.emoorpheus.earcng
readonly NO_BACKEND=3   # код выхода команды на ТВ: бэкенд не установлен (remote-setup.sh и мост так не выходят)

trap 'echo "error: line ${LINENO}: ${BASH_COMMAND}" >&2' ERR

usage() {
	sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' >&2
	exit 2
}

[[ $# -ge 1 ]] || usage
readonly TV=$1
shift
MODE=${1:-install}
if [[ $# -gt 0 ]]; then shift; fi
readonly MODE
# остаток "$@" - флаги бэкенда для try/args/monitor или режим для ui

SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
if [[ -n "${SSH_KEY:-}" ]]; then
	SSH_OPTS+=(-i "$SSH_KEY")
fi

# shellcheck disable=SC2029  # переменные ($STAGE, $RDIR) намеренно раскрываются на ПК
tv() { ssh "${SSH_OPTS[@]}" "$TV" "$@"; }
# shellcheck disable=SC2029
tv_tty() { ssh -t "${SSH_OPTS[@]}" "$TV" "$@"; }

# флаги -> строка для удалённого sh (пустой список -> пустая строка, а не '')
quote_args() {
	if [[ $# -gt 0 ]]; then printf '%q ' "$@"; fi
}

check() {
	echo "== preflight on ${1:-$TV}"
	tv 'sh -s' <<'TVEOF'
grep -E '^(PRETTY_NAME|VERSION)=' /etc/os-release
# QML_VIEWS/APP_BASES/HOOKDIR/TRK_DIR задаются только в тестах на ПК
if [ -f "${QML_VIEWS:-/usr/lib/qml/WebOSCompositor/views}/volume/StarfishVolume.qml" ]; then
	echo "UI: QML compositor (webOS TV <= 10.x) - the system indicator can show the real volume (ui native)"
fi
if [ -d /usr/palm/applications/com.webos.app.starfish_flutter_systemui ]; then
	echo "UI: Flutter system UI (webOS TV 11.x) - QML patching impossible, overlay is the way"
fi
# MOUNTS_FILE задаётся только в тестах на ПК. Копии в jail приложений (распространение mount) - только числом
mounts=$(grep StarfishVolume "${MOUNTS_FILE:-/proc/mounts}" 2>/dev/null | awk '{print $2}')
if [ -n "$mounts" ]; then
	jails=$(printf '%s\n' "$mounts" | grep -c '/jail/')
	main=$(printf '%s\n' "$mounts" | grep -v '/jail/' | tr '\n' ' ')
	if [ "$jails" -gt 0 ]; then main="${main}(+$jails in app jails)"; fi
	echo "QML bind-mounts: $main"
fi
echo "python3: $(python3 -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || echo MISSING)"
echo "node: $(node -v 2>/dev/null || echo missing)"
if [ -r /var/log/cecmessages ]; then
	echo "cecmessages: present, last soundbar reports:"
	grep -o 'RX5[0-9A-Fa-f]7A[0-9A-Fa-f][0-9A-Fa-f]' /var/log/cecmessages | tail -n 3
else
	echo "cecmessages: MISSING"
fi
app=
old=
for base in ${APP_BASES:-/media/developer/apps/usr/palm/applications /media/cryptofs/apps/usr/palm/applications}; do
	if [ -z "$app" ] && [ -f "$base/com.github.emoorpheus.earcng/appinfo.json" ]; then app="$base/com.github.emoorpheus.earcng"; fi
	if [ -z "$old" ] && [ -f "$base/com.github.nikolay1243.earcvolume/appinfo.json" ]; then old="$base/com.github.nikolay1243.earcvolume"; fi
done
version() { grep -oE '"version"[[:space:]]*:[[:space:]]*"[^"]*"' "$1/appinfo.json" | head -n 1 | sed 's/.*"\([^"]*\)"$/\1/'; }
if [ -n "$app" ]; then
	echo "eArcNG app: $app (version $(version "$app"))"
else
	echo "eArcNG app: not installed yet (install builds and installs it)"
fi
if [ -n "$old" ]; then
	echo "eARC Volume Overlay (Nikolay1243): $old (version $(version "$old")) - not needed: install turns off its watcher.js (the TV key-press counter)"
fi
echo "init.d hooks: $(ls "${HOOKDIR:-/var/lib/webosbrew/init.d}" 2>/dev/null | tr '\n' ' ')"
trk=${TRK_DIR:-/home/root/volume-osd}
if [ -d "$trk" ]; then
	state=enabled
	if [ -e "$trk/disable" ]; then state=disabled; fi
	echo "trk501 lg-soundbar-volume-osd: installed ($state) - only one of the two may be active"
fi
if command -v script >/dev/null 2>&1; then
	script -q -c "luna-send -n 1 luna://com.webos.service.audio/master/getVolume '{}'" /dev/null 2>/dev/null \
		| grep -o '"soundOutput": *"[^"]*"' | head -n 1
fi
TVEOF
	# проба /dev/cec* (только чтение) и QML-патча (копии во временном каталоге) - кодом из stage
	upload
	tv "if command -v python3 >/dev/null 2>&1; then python3 '$STAGE/backend/cec_volume_bridge.py' --probe; fi"
	tv "sh '$STAGE/backend/native-osd.sh' check || true"
}

local_version() {
	sed -n 's/^VERSION = "\(.*\)"$/\1/p' "$ROOT/backend/cec_volume_bridge.py"
}

# Всё, что нужно для issue: модель/прошивка, preflight, проба CEC и QML-патча, status, лог. Адрес ТВ не пишется.
report() {
	local file=${REPORT_FILE:-earcng-report.txt}
	{
		echo "eArcNG report: $(date -u '+%Y-%m-%d %H:%M UTC'), installer $(local_version)"
		echo
		echo "== TV"
		tv 'sh -s' <<'TVEOF' || echo "(failed: exit $?)"
# stdin - не канал ssh: иначе luna-send на ТВ ничего не выводит (OLED65M59LA, 10.3.0)
info=$(luna-send -n 1 luna://com.webos.service.tv.systemproperty/getSystemInfo \
	'{"keys":["modelName","firmwareVersion","sdkVersion","boardType"]}' </dev/null 2>/dev/null)
echo "system info: ${info:-unavailable}"
TVEOF
		tv "echo \"installed bridge: \$(python3 '$RDIR/cec_volume_bridge.py' --version 2>/dev/null || echo none)\"" \
			|| echo "(failed: exit $?)"
		echo
		check "the TV" || echo "(preflight failed: exit $?)"
		echo
		echo "== status"
		tv "sh '$STAGE/backend/remote-setup.sh' status" || echo "(failed: exit $?)"
		echo
		echo "== log"
		tv "sh '$STAGE/backend/remote-setup.sh' log" || echo "(failed: exit $?)"
		tv "rm -rf '$STAGE'" || true
	} 2>&1 | tee "$file"
	echo
	echo "Saved: $file"
	if command -v pbcopy >/dev/null 2>&1 && pbcopy < "$file"; then
		echo "Copied to the clipboard."
	fi
	echo "Paste it into a new issue: https://github.com/emoorpheus/earcng/issues/new?template=report.yml"
}

upload() {
	(cd "$ROOT" && COPYFILE_DISABLE=1 tar -cf - backend app) \
		| tv "rm -rf '$STAGE' && mkdir -p '$STAGE' && tar -xf - -C '$STAGE'"
}

install_from_stage() {
	echo "== install"
	tv "FORCE='${FORCE:-}' sh '$STAGE/backend/remote-setup.sh' install '$STAGE'; rc=\$?; rm -rf '$STAGE'; exit \$rc"
}

# remote-setup.sh <действие> на ТВ. Без установленного бэкенда (например, приложение поставлено через Homebrew
# Channel, а «Установить» на экране настроек ещё не нажато) status/log/uninstall выполняет копия из пакета
# приложения, остальное - понятное сообщение вместо "sh: can't open .../remote-setup.sh"
setup() {
	local action=$1 rc=0
	shift
	tv "S='$RDIR/remote-setup.sh'
if [ ! -f \"\$S\" ]; then
	S=
	case $action in status | log | uninstall)
		for base in \${APP_BASES:-/media/developer/apps/usr/palm/applications /media/cryptofs/apps/usr/palm/applications}; do
			if [ -z \"\$S\" ] && [ -f \"\$base/$APP_ID/backend/remote-setup.sh\" ]; then S=\$base/$APP_ID/backend/remote-setup.sh; fi
		done
	esac
	if [ -z \"\$S\" ]; then exit $NO_BACKEND; fi
fi
sh \"\$S\" $action $(quote_args "$@")" || rc=$?
	if [[ $rc -eq $NO_BACKEND ]]; then not_installed "$action"; fi
	return "$rc"
}

not_installed() {
	local hint="install: $0 root@<TV-IP> install, or the eArcNG app from Homebrew Channel + Install on its settings screen"
	case $1 in
		status)
			echo missing
			echo "eArcNG is not installed on this TV - $hint"
			exit 0
			;;
		uninstall)
			echo "nothing to uninstall: eArcNG is not installed on this TV"
			exit 0
			;;
		*)
			echo "error: the eArcNG backend is not installed on this TV - $hint" >&2
			exit 1
			;;
	esac
}

try_dry_run() {
	local secs=${TRY_SECONDS:-90}
	if ! [[ $secs =~ ^[0-9]+$ ]]; then
		echo "TRY_SECONDS must be a whole number" >&2
		exit 2
	fi
	upload
	echo "== dry run on the TV for ${secs}s (Ctrl+C stops). Nothing is installed or sent to the CEC bus/overlay."
	echo "   Press: 3 single taps, 5 fast taps, hold 2 s, mute twice, then change volume in the Sony app."
	tv_tty "python3 '$STAGE/backend/cec_volume_bridge.py' --dry-run --debug --log-file /dev/null --exit-after $secs $(quote_args "$@"); rc=\$?; rm -rf '$STAGE'; exit \$rc"
}

case $MODE in
	check)
		check
		tv "rm -rf '$STAGE'"
		;;
	try) try_dry_run "$@" ;;
	install)
		check
		install_from_stage
		echo "Done. Press volume +/- on the LG remote. Settings: open eArcNG from the app list. Log: $0 $TV log"
		;;
	status | log | restart | uninstall) setup "$MODE" ;;
	args | ui) setup "$MODE" "$@" ;;
	report) report ;;
	monitor)
		echo "Press volume keys (Ctrl+C to stop). Dry run: nothing is sent to the bus or the overlay."
		rc=0
		tv_tty "if [ ! -f '$RDIR/cec_volume_bridge.py' ]; then exit $NO_BACKEND; fi
python3 '$RDIR/cec_volume_bridge.py' --dry-run --debug --log-file /dev/null $(quote_args "$@")" || rc=$?
		if [[ $rc -eq $NO_BACKEND ]]; then not_installed monitor; fi
		exit "$rc"
		;;
	*) usage ;;
esac
