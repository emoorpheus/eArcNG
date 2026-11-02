#!/bin/sh
# remote-setup.sh - eArcNG: установка и управление НА ТВ (POSIX sh / BusyBox ash, root).
#   sh remote-setup.sh install <STAGE_DIR>   установить (из распакованного архива): бэкенд + приложение eArcNG
#   sh remote-setup.sh enable|disable|restart|status|log|uninstall
#   sh remote-setup.sh args [FLAGS... | --clear]   показать / проверить и сохранить флаги, перезапустить
#   sh remote-setup.sh ui [native|overlay]          нативный индикатор (QML-патч, webOS <= 10.x) или оверлей
#   sh remote-setup.sh switch native|overlay        то же для экрана настроек: проверка сразу, переключение в фоне
#   FORCE=1 sh remote-setup.sh install ...          установить, несмотря на trk501
set -eu
# Пути переопределяются переменными окружения только для тестов на ПК (tests/test_shell.sh)
APP_ID=${APP_ID:-com.github.emoorpheus.earcng}
OLD_APP_ID=com.github.nikolay1243.earcvolume   # eARC Volume Overlay (Nikolay1243): не нужен, его watcher.js выключается
DIR=${CEC_DIR:-/home/root/cec-volume}
HOOKDIR=${HOOKDIR:-/var/lib/webosbrew/init.d}
APP_BASES=${APP_BASES:-/media/developer/apps/usr/palm/applications /media/cryptofs/apps/usr/palm/applications}
RUNDIR=${RUNDIR:-/tmp}
HOOK=$HOOKDIR/95-cec-volume-bridge
STOCK_HOOK=$HOOKDIR/91-earc-volume-overlay
PIDFILE=$RUNDIR/cec-volume-bridge.pid
LOG=$RUNDIR/cec-volume-bridge.log
ERR=$RUNDIR/cec-volume-bridge.err
TRK_DIR=${TRK_DIR:-/home/root/volume-osd}     # trk501/lg-soundbar-volume-osd
VIEWS=${QML_VIEWS:-/usr/lib/qml/WebOSCompositor/views}
SWITCH_PID=$RUNDIR/cec-volume-ui.pid           # фоновое переключение ui с экрана настроек
SWITCH_LOG=$RUNDIR/cec-volume-ui.log
IPK=$RUNDIR/earcng.ipk                         # пакет приложения, собирается при установке (backend/ipk.py)
INSTALL_LOG=$RUNDIR/earcng-install.log         # ответы appInstallService
INSTALL_WAIT=${INSTALL_WAIT:-120}              # с: установка приложения
REMOVE_WAIT=${REMOVE_WAIT:-30}                 # с: удаление приложения
REOPEN_WAIT=${REOPEN_WAIT:-60}                 # с: ждать интерфейс ТВ после переключения индикатора
REOPEN_DELAY=${REOPEN_DELAY:-3}                # с: ...и ещё столько (лаунчер успевает открыться)
REOPEN_TRIES=${REOPEN_TRIES:-5}                # попыток открыть экран настроек снова
REOPEN_PAUSE=${REOPEN_PAUSE:-2}                # с: между попытками

die() { echo "error: $*" >&2; exit 1; }

# stdin не канал ssh/терминал: иначе luna-send на ТВ ничего не выводит (OLED65M59LA, 10.3.0)
luna() { luna-send -n 1 "$@" </dev/null 2>/dev/null; }

find_app() { # каталог приложения $1 (appinfo.json в одном из APP_BASES)
	# shellcheck disable=SC2086  # список каталогов через пробел
	for base in $APP_BASES; do
		if [ -f "$base/$1/appinfo.json" ]; then
			echo "$base/$1"
			return 0
		fi
	done
	return 1
}

app_version() {
	grep -o '"version"[[:space:]]*:[[:space:]]*"[0-9.]*"' "$1/appinfo.json" 2>/dev/null | head -n 1 \
		| sed 's/.*"\([0-9.]*\)"$/\1/'
}

trk501_state() {
	# running | enabled | none. Вместе с нашим мостом - два индикатора и двойной tap guard
	for d in /proc/[0-9]*; do
		cmd=$(tr '\0' ' ' 2>/dev/null < "$d/cmdline") || continue
		case $cmd in *"$TRK_DIR/cec-sync.py"*) echo running; return 0 ;; esac
	done
	if [ -e "$HOOKDIR/60-volume-osd" ] && [ ! -e "$TRK_DIR/disable" ]; then
		echo enabled
	else
		echo none
	fi
}

bridge_pid() {
	pid=$(cat "$PIDFILE" 2>/dev/null || true)
	case $pid in '' | *[!0-9]*) return 1 ;; esac
	if tr '\0' ' ' 2>/dev/null < "/proc/$pid/cmdline" | grep -q cec_volume_bridge; then
		echo "$pid"
		return 0
	fi
	return 1
}

stop_bridge() {
	if pid=$(bridge_pid); then
		kill "$pid" 2>/dev/null || true
		n=0
		while kill -0 "$pid" 2>/dev/null && [ "$n" -lt 3 ]; do
			sleep 1
			n=$((n + 1))
		done
		kill -9 "$pid" 2>/dev/null || true
	fi
	rm -f "$PIDFILE"
}

stop_stock_watcher() {
	# штатный watcher.js Nikolay1243 показывает счётчик нажатий ТВ - отключаем автозапуск и процесс
	if [ -L "$STOCK_HOOK" ] || [ -e "$STOCK_HOOK" ]; then
		rm -f "$STOCK_HOOK"
	fi
	for d in /proc/[0-9]*; do
		cmd=$(tr '\0' ' ' 2>/dev/null < "$d/cmdline") || continue
		case $cmd in
			*node*earcvolume*/runtime/watcher.js*) kill "${d#/proc/}" 2>/dev/null || true ;;
		esac
	done
	rm -f "$RUNDIR/earc-volume-overlay.pid"
}

proc_alive() { # процесс жив и не зомби (фоновый luna-send до wait - зомби)
	st=$(sed 's/.*) //' "/proc/$1/stat" 2>/dev/null | cut -c1)
	[ -n "$st" ] && [ "$st" != Z ]
}

app_ready() { # $1 - каталог app/ из архива, $2 - версия: файлы на месте и applicationManager знает приложение
	dir=$(find_app "$APP_ID") || return 1
	python3 "$IPKPY" same "$1" "$dir" || return 1
	luna luna://com.webos.applicationManager/getAppInfo "{\"id\":\"$APP_ID\"}" | grep -q "\"version\" *: *\"$2\""
}

install_app() { # $1 - каталог app/ из архива: собрать IPK и поставить через appInstallService (как ares-install)
	src=$1
	rm -f "$IPK" "$INSTALL_LOG"
	info=$(python3 "$IPKPY" build "$src" "$IPK") || die "could not build the eArcNG app package"
	ver=${info#* }
	echo "installing the eArcNG app $ver (appInstallService, up to ${INSTALL_WAIT}s)"
	luna-send -n 50 luna://com.webos.appInstallService/dev/install \
		"{\"id\":\"com.ares.defaultName\",\"ipkUrl\":\"$IPK\",\"subscribe\":true}" </dev/null >"$INSTALL_LOG" 2>&1 &
	lpid=$!
	n=0
	result=
	while [ "$n" -lt "$INSTALL_WAIT" ]; do
		sleep 1
		n=$((n + 1))
		if grep -qE '"errorCode"|"returnValue" *: *false' "$INSTALL_LOG"; then
			result=failed
		elif grep -qE '"state" *: *"installed"|"statusValue" *: *30[,}]' "$INSTALL_LOG" || app_ready "$src" "$ver"; then
			result=ok
		elif ! proc_alive "$lpid"; then
			result=failed           # luna-send завершился без "installed", приложения нет
		fi
		if [ -n "$result" ]; then break; fi
	done
	if [ -z "$result" ] && find_app "$APP_ID" >/dev/null && python3 "$IPKPY" same "$src" "$(find_app "$APP_ID")"; then
		result=ok
		echo "note: no confirmation from appInstallService in ${INSTALL_WAIT}s, but the app files are in place"
	fi
	kill "$lpid" 2>/dev/null || true
	wait "$lpid" 2>/dev/null || true
	if [ "$result" != ok ]; then
		grep -oE '"(errorCode|errorText|reason|state)" *: *("[^"]*"|-?[0-9]+)' "$INSTALL_LOG" 2>/dev/null | tail -n 4 >&2 || true
		die "the eArcNG app was not installed (appInstallService log: $INSTALL_LOG, package: $IPK)"
	fi
	rm -f "$IPK"
	echo "app: eArcNG $ver installed ($(find_app "$APP_ID"))"
}

remove_app() {
	find_app "$APP_ID" >/dev/null || return 0
	luna luna://com.webos.appInstallService/dev/remove "{\"id\":\"$APP_ID\"}" >/dev/null || true
	n=0
	while find_app "$APP_ID" >/dev/null && [ "$n" -lt "$REMOVE_WAIT" ]; do
		sleep 1
		n=$((n + 1))
	done
	if find_app "$APP_ID" >/dev/null; then
		echo "warning: the eArcNG app is still installed - remove it in Homebrew Channel"
	else
		echo "app: eArcNG removed"
	fi
}

restore_old_app() { # index.html eARC Volume Overlay заменён (есть index.html.stock): вернуть штатные файлы
	old=$(find_app "$OLD_APP_ID") || return 0
	if [ -f "$old/index.html.stock" ]; then
		mv -f "$old/index.html.stock" "$old/index.html"
		rm -f "$old/overlay.js" "$old/overlay.css"
		echo "eARC Volume Overlay ($OLD_APP_ID): stock index.html restored"
	fi
}

start_bridge() {
	rm -f "$DIR/disable"
	sh "$HOOK"
}

ui_mode() {
	if [ "$(cat "$DIR/ui" 2>/dev/null || true)" = native ]; then echo native; else echo overlay; fi
}

native_state() {
	if [ -f "$DIR/native-osd.sh" ]; then sh "$DIR/native-osd.sh" status; else echo inactive; fi
}

switch_pid() {
	# фоновое "ui native|overlay" ещё идёт (кроме самого этого процесса); до exec у дочернего
	# процесса ещё командная строка родителя ("switch ...")
	pid=$(cat "$SWITCH_PID" 2>/dev/null || true)
	case $pid in '' | *[!0-9]*) return 1 ;; esac
	[ "$pid" != "$$" ] || return 1
	case $(tr '\0' ' ' 2>/dev/null < "/proc/$pid/cmdline") in
		*remote-setup.sh\ ui\ * | *remote-setup.sh\ switch\ *)
			echo "$pid"
			return 0
			;;
	esac
	return 1
}

cmd_status() {
	if [ ! -f "$DIR/cec_volume_bridge.py" ] || [ ! -f "$HOOK" ]; then
		state=missing
	elif switch_pid >/dev/null; then
		state=switching
	elif [ -e "$DIR/disable" ]; then
		state=disabled
	elif bridge_pid >/dev/null; then
		state=running
	else
		state=stopped
	fi
	echo "$state"
	py=$(python3 -c 'import sys; print(sys.version.split()[0])' 2>/dev/null || echo none)
	cec=
	for c in /dev/cec*; do
		if [ -e "$c" ]; then cec="$cec$c "; fi
	done
	echo "python3: $py; cec: ${cec:-none}; args: $(cat "$DIR/args" 2>/dev/null || echo none)"
	ui=$(ui_mode)
	if [ "$ui" = native ]; then
		echo "ui: native (QML patch $(native_state)$([ -e "$DIR/no-native" ] && echo ', disabled after a failed attempt'))"
	elif [ -e "$DIR/no-native" ]; then
		echo "ui: overlay (system indicator disabled after a failed attempt)"
	else
		echo "ui: overlay"
	fi
	if [ -f "$VIEWS/volume/StarfishVolume.qml" ]; then
		echo "qml: yes"
	else
		echo "qml: no (no QML compositor - webOS 11 Flutter UI?)"
	fi
	if app=$(find_app "$APP_ID"); then
		echo "app: eArcNG $(app_version "$app") ($app)"
	else
		echo "app: not installed"
	fi
	if [ -f "$LOG" ]; then
		tail -n 3 "$LOG"
	fi
	if [ "$state" = stopped ] && [ -s "$ERR" ]; then
		echo "last error ($ERR):"
		tail -n 5 "$ERR"
	fi
}

cmd_args() {
	if [ $# -eq 0 ]; then
		echo "args: $(cat "$DIR/args" 2>/dev/null || true)"
		return 0
	fi
	[ -f "$DIR/cec_volume_bridge.py" ] || die "backend not installed"
	if [ "$1" = --clear ]; then
		: > "$DIR/args"
	else
		python3 "$DIR/cec_volume_bridge.py" --print-config "$@" >/dev/null || die "invalid options - args unchanged"
		printf '%s\n' "$*" > "$DIR/args"
	fi
	echo "args: $(cat "$DIR/args")"
	restart_if_enabled
	cmd_status
}

restart_if_enabled() {
	if [ ! -e "$DIR/disable" ]; then
		stop_bridge
		start_bridge
		sleep 1
	fi
}

cmd_ui() {
	if [ ! -f "$DIR/cec_volume_bridge.py" ] || [ ! -f "$DIR/native-osd.sh" ]; then
		die "backend not installed"
	fi
	case ${1:-} in
		'')
			echo "ui: $(ui_mode); QML patch: $(native_state)"
			sh "$DIR/native-osd.sh" check || true
			return 0
			;;
		native)
			trk=$(trk501_state)
			if [ "$trk" != none ]; then
				die "trk501 lg-soundbar-volume-osd is $trk - it patches the same QML files. Disable it: touch $TRK_DIR/disable && reboot"
			fi
			rm -f "$DIR/no-native"
			sh "$DIR/native-osd.sh" check || die "staying with the overlay"
			if [ -e "$DIR/disable" ]; then
				# без моста патченый индикатор показывал бы устаревшее число: патч поставит enable (хук)
				echo native > "$DIR/ui"
				echo "saved: the bridge is disabled - the system indicator is patched on enable"
			else
				echo "applying: the TV interface (surface-manager) restarts, open apps close, the screen can go black for ~15 s"
				sh "$DIR/native-osd.sh" apply || die "native OSD patch rolled back - staying with the overlay"
				echo native > "$DIR/ui"
			fi
			;;
		overlay)
			echo overlay > "$DIR/ui"
			if [ "$(native_state)" = active ]; then
				echo "reverting: the TV interface (surface-manager) restarts, open apps close"
				sh "$DIR/native-osd.sh" revert
			fi
			;;
		*) die "usage: ui [native|overlay]" ;;
	esac
	restart_if_enabled
	cmd_status
}

cmd_switch() {
	# Экран настроек: ошибки, видимые заранее, - сразу строкой "switch error:"; само переключение - в фоне,
	# т.к. перезапуск surface-manager закрывает окно приложения. Пока идёт, status пишет "switching".
	mode=${1:-}
	case $mode in native | overlay) ;; *) die "usage: switch native|overlay" ;; esac
	if [ ! -f "$DIR/cec_volume_bridge.py" ] || [ ! -f "$DIR/native-osd.sh" ]; then
		die "backend not installed"
	fi
	problem=
	if switch_pid >/dev/null; then
		cmd_status
		return 0
	fi
	if [ "$mode" = native ]; then
		trk=$(trk501_state)
		if [ "$trk" != none ]; then
			problem="trk501 lg-soundbar-volume-osd is $trk - disable it first"
		elif ! out=$(sh "$DIR/native-osd.sh" check 2>&1); then
			problem=$(printf '%s\n' "$out" | tail -n 1 | sed 's/^native OSD: //')
		fi
	fi
	if [ -n "$problem" ]; then
		cmd_status
		echo "switch error: $problem"
		return 0
	fi
	if [ "$(ui_mode)" = "$mode" ] && { [ "$mode" = overlay ] || [ "$(native_state)" = active ] || [ -e "$DIR/disable" ]; }; then
		cmd_status
		return 0
	fi
	if command -v setsid >/dev/null 2>&1; then run="setsid"; else run="nohup"; fi
	# EARCNG_REOPEN: по окончании экран настроек открывается снова (reopen_settings)
	EARCNG_REOPEN=1 $run sh "$0" ui "$mode" </dev/null >"$SWITCH_LOG" 2>&1 &
	echo $! > "$SWITCH_PID"
	cmd_status
}

reopen_settings() { # $1 - режим. surface-manager перезапускался - окно экрана настроек закрыто
	# переключение закончено: status снова "running", а не "switching"
	if [ "$(cat "$SWITCH_PID" 2>/dev/null || true)" = "$$" ]; then rm -f "$SWITCH_PID"; fi
	# интерфейс ТВ поднялся, когда на переднем плане есть приложение (лаунчер)
	n=0
	while [ "$n" -lt "$REOPEN_WAIT" ]; do
		if luna luna://com.webos.applicationManager/getForegroundAppInfo '{}' | grep -q '"appId" *: *"[^"]'; then break; fi
		sleep 1
		n=$((n + 1))
	done
	sleep "$REOPEN_DELAY"
	n=0
	while [ "$n" -lt "$REOPEN_TRIES" ]; do
		if luna luna://com.webos.applicationManager/launch "{\"id\":\"$APP_ID\",\"params\":{\"switched\":\"$1\"}}" \
			| grep -q '"returnValue" *: *true'; then
			echo "settings screen reopened"
			return 0
		fi
		n=$((n + 1))
		sleep "$REOPEN_PAUSE"
	done
	echo "settings screen not reopened"
}

cmd_install() {
	stage=${1:-}
	if [ -z "$stage" ] || [ ! -f "$stage/backend/cec_volume_bridge.py" ] || [ ! -f "$stage/app/appinfo.json" ]; then
		die "usage: install <STAGE_DIR>"
	fi
	[ "$(id -u)" = 0 ] || [ -n "${CEC_ALLOW_NONROOT:-}" ] || die "run as root"
	command -v python3 >/dev/null 2>&1 || die "python3 not found on this TV - the backend cannot run"
	IPKPY=$stage/backend/ipk.py
	trk=$(trk501_state)
	if [ "$trk" != none ]; then
		[ -n "${FORCE:-}" ] || die "trk501 lg-soundbar-volume-osd is $trk - two indicators would be shown. Disable it: touch $TRK_DIR/disable && reboot (or FORCE=1)"
		echo "warning: trk501 is $trk (FORCE)"
	fi
	# сначала приложение: если appInstallService откажет, на ТВ ничего не меняется
	install_app "$stage/app"
	mkdir -p "$DIR" "$HOOKDIR"
	cp "$stage/backend/cec_volume_bridge.py" "$stage/backend/remote-setup.sh" "$stage/backend/native-osd.sh" "$DIR/"
	cp "$stage/backend/95-cec-volume-bridge" "$HOOK"
	chmod 755 "$DIR/cec_volume_bridge.py" "$DIR/remote-setup.sh" "$DIR/native-osd.sh" "$HOOK"
	# eARC Volume Overlay не нужен: штатные файлы на месте, его watcher.js (счётчик нажатий ТВ) выключен
	restore_old_app
	stop_stock_watcher
	if find_app "$OLD_APP_ID" >/dev/null; then
		echo "note: eARC Volume Overlay is not needed with eArcNG (its watcher.js is off) - you can remove it in Homebrew Channel"
	fi
	stop_bridge
	start_bridge
	sleep 2
	echo "installed: backend $DIR, hook $HOOK, app $(find_app "$APP_ID")"
	cmd_status
}

cmd_uninstall() {
	stop_bridge
	rm -f "$HOOK"
	if [ "$(native_state)" = active ]; then
		sh "$DIR/native-osd.sh" revert || true
	fi
	remove_app
	# eARC Volume Overlay (если остался): штатные файлы и автозапуск его watcher.js - как до установки
	if old=$(find_app "$OLD_APP_ID"); then
		restore_old_app
		if [ -f "$old/runtime/91-earc-volume-overlay" ]; then
			ln -sf "$old/runtime/91-earc-volume-overlay" "$STOCK_HOOK"
			sh "$STOCK_HOOK" || true
			echo "eARC Volume Overlay: its watcher.js is enabled again"
		fi
	fi
	rm -rf "$DIR"
	echo "uninstalled"
}

case ${1:-status} in
	install) cmd_install "${2:-}" ;;
	enable)
		stop_stock_watcher
		start_bridge
		sleep 1
		cmd_status
		;;
	disable)
		mkdir -p "$DIR"
		touch "$DIR/disable"
		stop_bridge
		if [ "$(native_state)" = active ]; then
			# без моста патченый индикатор показывал бы устаревшее число; enable поставит патч снова
			# (в stderr: первая строка stdout - состояние для экрана настроек оверлея)
			echo "reverting the native OSD patch (bridge disabled): the TV interface restarts" >&2
			sh "$DIR/native-osd.sh" revert >&2 || true
		fi
		cmd_status
		;;
	restart)
		stop_bridge
		start_bridge
		sleep 1
		cmd_status
		;;
	status) cmd_status ;;
	log)
		tail -n 60 "$LOG" 2>/dev/null || echo "no log yet"
		if [ -s "$ERR" ]; then
			echo "--- $ERR"
			tail -n 20 "$ERR"
		fi
		;;
	args)
		shift
		cmd_args "$@"
		;;
	ui)
		shift
		if [ -n "${EARCNG_REOPEN:-}" ]; then
			# фоновое переключение с экрана настроек: при любом исходе (и при ошибке) открыть экран снова
			set +e
			(
				set -e
				cmd_ui "$@"
			)
			rc=$?
			set -e
			reopen_settings "${1:-}"
			exit "$rc"
		fi
		cmd_ui "$@"
		;;
	switch)
		shift
		cmd_switch "$@"
		;;
	uninstall) cmd_uninstall ;;
	*) die "unknown command: $1 (install|enable|disable|restart|status|log|args|ui|switch|uninstall)" ;;
esac
