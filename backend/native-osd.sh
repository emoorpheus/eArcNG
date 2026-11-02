#!/bin/sh
# native-osd.sh - реальная громкость саундбара в НАТИВНОМ индикаторе webOS <= 10.x (QML compositor).
# Метод и замены - trk501/lg-soundbar-volume-osd (60-volume-osd, MIT): патченые копии двух QML-файлов
# лежат в /tmp и подключаются mount --bind. Системные файлы не меняются; перезагрузка без хука = оригинал.
# Индикатор показывает число и полосу и для внешних выходов (ARC), но только значения с cause
# "setVolume" - их выставляет cec_volume_bridge.py --ui native из CEC 0x7A. Счётчик нажатий ТВ не виден.
#   sh native-osd.sh check    подходит ли патч к этой прошивке (ничего не меняет)
#   sh native-osd.sh apply    подключить патч и перезапустить surface-manager (сбой -> откат и no-native)
#   sh native-osd.sh revert   убрать патч и перезапустить surface-manager
#   sh native-osd.sh status   active | inactive | foreign
set -u
# Пути переопределяются только в тестах на ПК (tests/test_shell.sh)
DIR=${CEC_DIR:-/home/root/cec-volume}
VIEWS=${QML_VIEWS:-/usr/lib/qml/WebOSCompositor/views}
TMP=${QML_TMP:-/tmp/cec-volume-qml}
MOUNTS=${MOUNTS_FILE:-/proc/mounts}
LOG=${RUNDIR:-/tmp}/cec-volume-bridge.log
SETTLE=${SM_SETTLE:-15}               # секунд ждать surface-manager после перезапуска (как trk501)
VOL=$VIEWS/volume/StarfishVolume.qml
VIEW=$VIEWS/StarfishVolumeView.qml
MARK='// cec-volume-bridge: native volume OSD patch (method: trk501/lg-soundbar-volume-osd)'

note() { echo "$(date '+%Y-%m-%d %H:%M:%S') native-osd: $*" >> "$LOG" 2>/dev/null || true; }
say() { echo "native OSD: $*"; }
mounted() { grep -q " $1 " "$MOUNTS" 2>/dev/null; }

state() {
	if mounted "$VOL" || mounted "$VIEW"; then
		if grep -qF "$MARK" "$VOL" 2>/dev/null; then echo active; else echo foreign; fi
	else
		echo inactive
	fi
}

# Патченые копии в $1. 0 - все замены сработали ровно как ожидалось, иначе 1 и причина в stdout.
make_patch() {
	out=$1
	if [ ! -f "$VOL" ] || [ ! -f "$VIEW" ]; then
		echo "QML files not found ($VIEWS) - firmware without QML compositor (webOS 11 = Flutter)"
		return 1
	fi
	mkdir -p "$out" || return 1
	cp "$VOL" "$out/StarfishVolume.qml" && cp "$VIEW" "$out/StarfishVolumeView.qml" || return 1
	# 1. число и полоса больше не скрываются для внешних выходов, стрелки +/- уходят
	sed -i 's/exterior && !syncable/false/g' "$out/StarfishVolume.qml"
	# 2. показывается shownVolume вместо живого счётчика ТВ
	sed -i -e 's/LS\.audioService\.volume\([^A-Za-z]\)/root.shownVolume\1/g' \
		-e 's/^\(    id: root\)$/\1\n    property int shownVolume: LS.audioService.volume/' "$out/StarfishVolume.qml"
	# 3. для ARC shownVolume берёт только значения не от нажатий (cause setVolume = реальная громкость)
	sed -i -e 's/LS\.audioService\.volume\([^A-Za-z]\)/volume.shownVolume\1/g' \
		-e 's/^\(    function updateVisibility() {\)$/\1\n        if (!(volume.exterior \&\& (LS.audioService.cause === "volumeUp" || LS.audioService.cause === "volumeDown")))\n            volume.shownVolume = LS.audioService.volume;/' "$out/StarfishVolumeView.qml"
	if grep -q "exterior && !syncable" "$out/StarfishVolume.qml"; then
		echo "StarfishVolume.qml: 'exterior && !syncable' left after replacement"
		return 1
	fi
	if [ "$(grep -c 'property int shownVolume' "$out/StarfishVolume.qml")" != 1 ]; then
		echo "StarfishVolume.qml: no unique '    id: root' line"
		return 1
	fi
	if [ "$(grep -c 'volume.shownVolume = LS.audioService.volume' "$out/StarfishVolumeView.qml")" != 1 ]; then
		echo "StarfishVolumeView.qml: no unique '    function updateVisibility() {' line"
		return 1
	fi
	if grep -E 'LS\.audioService\.volume([^A-Za-z]|$)' "$out/StarfishVolume.qml" | grep -qv 'shownVolume: LS' \
		|| grep -E 'LS\.audioService\.volume([^A-Za-z]|$)' "$out/StarfishVolumeView.qml" | grep -qv 'shownVolume = LS'; then
		echo "LS.audioService.volume is used in an unexpected form - firmware QML differs"
		return 1
	fi
	printf '%s\n' "$MARK" >> "$out/StarfishVolume.qml"
	printf '%s\n' "$MARK" >> "$out/StarfishVolumeView.qml"
	return 0
}

restart_ui() {
	systemctl restart surface-manager 2>/dev/null || return 1
	sleep "$SETTLE"
	systemctl is-active -q surface-manager 2>/dev/null
}

cmd_check() {
	case $(state) in
		active) say "active"; return 0 ;;
		foreign) say "not applicable - the QML files are already bind-mounted by something else (trk501 60-volume-osd?)"; return 1 ;;
	esac
	work=$(mktemp -d 2>/dev/null) || work=/tmp/cec-volume-qml-check.$$
	if reason=$(make_patch "$work"); then
		say "applicable - number and bar in the system indicator (switch: earcng.sh <TV> ui native)"
		rc=0
	else
		say "not applicable - $reason"
		rc=1
	fi
	rm -rf "$work"
	return $rc
}

cmd_apply() {
	case $(state) in
		active) say "already active"; return 0 ;;
		foreign) say "QML files are bind-mounted by something else (trk501?) - not touching them"; return 1 ;;
	esac
	if [ -e "$DIR/no-native" ]; then
		say "disabled after a failed attempt ($DIR/no-native) - run: earcng.sh <TV> ui native"
		return 1
	fi
	command -v systemctl >/dev/null 2>&1 || { say "systemctl not found - cannot restart surface-manager"; return 1; }
	rm -rf "$TMP"
	if ! reason=$(make_patch "$TMP"); then
		say "not applied - $reason"
		note "not applied - $reason"
		rm -rf "$TMP"
		return 1
	fi
	if ! mount --bind "$TMP/StarfishVolume.qml" "$VOL"; then
		say "mount --bind failed"
		return 1
	fi
	if ! mount --bind "$TMP/StarfishVolumeView.qml" "$VIEW"; then
		umount "$VOL" 2>/dev/null
		say "mount --bind failed"
		return 1
	fi
	if restart_ui; then
		note "active"
		say "active"
		return 0
	fi
	# интерфейс не поднялся: оригинал назад и больше не пытаться автоматически (как trk501)
	umount "$VOL" 2>/dev/null
	umount "$VIEW" 2>/dev/null
	systemctl restart surface-manager 2>/dev/null
	mkdir -p "$DIR" && touch "$DIR/no-native"
	note "surface-manager did not come back - patch removed, native mode disabled"
	say "surface-manager did not come back - patch removed, overlay stays"
	return 1
}

cmd_revert() {
	case $(state) in
		inactive) say "inactive"; return 0 ;;
		foreign) say "QML files are bind-mounted by something else (trk501?) - not touching them"; return 1 ;;
	esac
	umount "$VOL" 2>/dev/null
	umount "$VIEW" 2>/dev/null
	rm -rf "$TMP"
	systemctl restart surface-manager 2>/dev/null || true
	note "reverted"
	say "reverted (original indicator)"
}

case ${1:-status} in
	check) cmd_check ;;
	apply) cmd_apply ;;
	revert) cmd_revert ;;
	status) state ;;
	*) echo "usage: native-osd.sh check|apply|revert|status" >&2; exit 2 ;;
esac
