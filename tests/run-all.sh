#!/usr/bin/env bash
# Все тесты без ТВ (на ПК / в CI):
#   python unittest (бэкенд) + node (жизненный цикл оверлея) + BusyBox-интеграция + инсталлятор.
#   BUSYBOX=/path/to/busybox-static [BASH32=/path/to/bash-3.2] tests/run-all.sh
# Тесты, для которых нет инструментов, помечаются SKIP (а не PASS).
# REQUIRE_ALL=1 (CI): любой SKIP считается провалом.
set -Eeuo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
readonly HERE ROOT
export PYTHONDONTWRITEBYTECODE=1

passed=0 skipped=0 failed=0
results=()

run() {
	local name=$1
	shift
	printf '\n== %s\n' "$name"
	if "$@"; then
		passed=$((passed + 1)); results+=("PASS  $name")
	else
		failed=$((failed + 1)); results+=("FAIL  $name")
	fi
}
skip() {
	printf '\n== %s\n   SKIP: %s\n' "$1" "$2"
	skipped=$((skipped + 1)); results+=("SKIP  $1 ($2)")
}

cd "$ROOT"
run "backend: python unittest" python3 -m unittest discover -s tests -p 'test_*.py'

if command -v node >/dev/null 2>&1; then
	run "overlay: node lifecycle" node tests/test-overlay.cjs
else
	skip "overlay: node lifecycle" "node not found"
fi

BB=${BUSYBOX:-$(command -v busybox 2>/dev/null || true)}
if [[ -n "$BB" && -x "$BB" ]]; then
	export BUSYBOX=$BB
	run "TV scripts: BusyBox integration" bash tests/test_shell.sh
	run "PC installer: ssh stub" bash tests/test_installer.sh
	if [[ -n "${BASH32:-}" && -x "${BASH32:-}" ]]; then        # bash 3.2 = /bin/bash на macOS
		run "PC installer under $("$BASH32" --version | head -n 1 | cut -d' ' -f1-4)" \
			env PATH="$(dirname "$BASH32"):$PATH" "$BASH32" tests/test_installer.sh
	fi
else
	skip "TV scripts: BusyBox integration" "set BUSYBOX=/path/to/busybox"
	skip "PC installer: ssh stub" "set BUSYBOX=/path/to/busybox"
fi

if command -v shellcheck >/dev/null 2>&1; then
	run "shellcheck (bash)" shellcheck -x tools/earcng.sh tests/run-all.sh tests/test_shell.sh tests/test_installer.sh
	run "shellcheck (POSIX sh)" shellcheck -s sh backend/95-cec-volume-bridge backend/remote-setup.sh backend/native-osd.sh tests/fixtures/luna-send
else
	skip "shellcheck" "shellcheck not found"
fi

printf '\n== summary\n'
printf '   %s\n' "${results[@]}"
printf '   passed %d, skipped %d, failed %d\n' "$passed" "$skipped" "$failed"
if [[ "${REQUIRE_ALL:-0}" == 1 && $skipped -gt 0 ]]; then
	printf '   REQUIRE_ALL=1: %d suite(s) skipped -> fail\n' "$skipped"
	exit 1
fi
[[ $failed -eq 0 ]]
