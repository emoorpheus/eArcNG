#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cec_volume_bridge.py - мост eArcNG (на основе trk501/lg-soundbar-volume-osd + Nikolay1243/Webos-EARC-Volume-Overlay).

Берёт РЕАЛЬНУЮ громкость саундбара из HDMI-CEC <Report Audio Status> (кадр 50:7A:xx) и показывает её:
  * --ui overlay (по умолчанию): прозрачный оверлей (web-app) через applicationManager/launch;
  * --ui native: в НАТИВНОМ индикаторе webOS <= 10.x - значение уходит в счётчик ТВ (audio setVolume),
    QML-патч native-osd.sh (метод trk501) показывает только такие значения. Без патча - оверлей.
Счётчик нажатий ТВ никогда не показывается.

Источники (все - только чтение, кроме двух CEC-команд ниже):
  * /dev/cecN в режиме MONITOR  - кадры в реальном времени (если устройство доступно);
  * /var/log/cecmessages        - inotify-слежение, запасной путь (лог пишется пачками);
  * /dev/input/event*           - физическое состояние кнопок пульта (tap guard, ранний показ);
  * luna://com.webos.service.audio/master/getVolume - soundOutput (гейт "выход = ARC"), для native - volume/mute.
  * luna://com.webos.service.eim/getArcVolume - громкость ARC, которую ТВ получил от саундбара (идея
    Nikolay1243 v1.4.0): --arc-source auto берёт её, только пока CEC-шина не приносит 50:7A.
Передаётся в шину только: 05:45 <User Control Released> (антиспам "быстрых тапов", идея trk501)
и 05:71 <Give Audio Status> один раз в конце серии нажатий (контрольное значение).

Python 3.6+, только стандартная библиотека, запуск от root.  --help - все параметры.
"""
import argparse
import ctypes
import errno
import fcntl
import glob
import json
import os
import random
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time

VERSION = "1.1.0"
DEFAULT_APP_ID = "com.github.emoorpheus.earcng"
STATE_DIR = "/home/root/cec-volume"
AUDIO_GET_VOLUME = "luna://com.webos.service.audio/master/getVolume"
AUDIO_SET_VOLUME = "luna://com.webos.service.audio/master/setVolume"
EIM_GET_ARC_VOLUME = "luna://com.webos.service.eim/getArcVolume"
QML_VOLUME = "/usr/lib/qml/WebOSCompositor/views/volume/StarfishVolume.qml"
APP_LAUNCH = "luna://com.webos.applicationManager/launch"
POSITIONS = ("top-right", "top-left", "top-center", "center-right", "center-left",
             "bottom-right", "bottom-left", "bottom-center")
VARIANTS = ("badge", "pill", "ring")
# Тайминги (замер try.log на OLED65M59LA + HT-A8000): пульт -> CEC 05:44 ~0.1 с, 05:44 -> 7A ~0.16 с
KEY_CEC_LAG = 0.1          # CEC-повтор ТВ запаздывает на столько за его внутренним событием громкости
MIN_VISIBLE = 0.4          # оверлей не гаснет раньше, чем через 0.4 с после обновления
NATIVE_RECHECK = 5.0       # как часто перепроверять, смонтирован ли QML-патч
FEEDBACK_WINDOW = 0.15     # кадр громкости ТВ в это окно после setVolume = подозрение на обратную связь
FEEDBACK_IDLE = 1.5        # ...если пульт перед этим не трогали столько секунд
FEEDBACK_STREAK = 3        # столько setVolume подряд с таким ответом -> native-синхронизация выключается
REJECT_STREAK = 3          # столько отказов setVolume подряд -> native выключается (прошивка не даёт)
ARC_SOURCES = ("auto", "cec", "eim")
EIM_FAST = 0.15            # с: опрос getArcVolume сразу после нажатия/изменения (как у Nikolay1243 v1.4.0)
EIM_FAST_FOR = 2.5         # с: ...столько после последнего нажатия/изменения
EIM_IDLE = 1.0             # с: опрос в остальное время
EIM_GRACE = 2.0            # с: auto - после старта сначала ждать CEC-шину
EIM_BUS_WAIT = 1.0         # с: auto - шина работает, но после нажатия 50:7A нет столько -> EIM
EIM_SETTLE = 0.5           # с: EIM - "то же значение" раньше этого после нажатия ещё не ответ саундбара
EIM_FAILS = 5              # неудач getArcVolume подряд -> компонент недоступен (повтор через 60 с)
EIM_TIMEOUT = 1.5          # с: один вызов luna-send
REJECT_RELOG = 300.0       # с: тот же отказ launch пишется в лог не чаще

# --- HDMI-CEC ---------------------------------------------------------------
LA_TV, LA_AUDIO = 0x0, 0x5
OP_UC_PRESSED, OP_UC_RELEASED = 0x44, 0x45
OP_GIVE_AUDIO_STATUS, OP_REPORT_AUDIO_STATUS = 0x71, 0x7A
OP_SET_AUDIO_VOLUME_LEVEL = 0x73
UC_KEYS = {0x41: "up", 0x42: "down", 0x43: "mute"}
# linux/cec.h
CEC_S_MODE = 0x40046109           # _IOW('a', 9, __u32)
CEC_TRANSMIT = 0xC0386105         # _IOWR('a', 5, struct cec_msg)
CEC_RECEIVE = 0xC0386106          # _IOWR('a', 6, struct cec_msg)
CEC_MODE_INITIATOR, CEC_MODE_MONITOR = 0x01, 0xE0
CEC_ADAP_G_CAPS = 0xC04C6100      # _IOWR('a', 0, struct cec_caps)       - только для --probe
CEC_ADAP_G_PHYS_ADDR = 0x80026101 # _IOR('a', 1, __u16)
CEC_ADAP_G_LOG_ADDRS = 0x805C6103 # _IOR('a', 3, struct cec_log_addrs)
CEC_CAPS = struct.Struct("=32s32sIII")              # struct cec_caps, 76 байт
CEC_LOG_ADDRS_SIZE = 92                              # struct cec_log_addrs
CAP_NAMES = ((0x1, "PHYS_ADDR"), (0x2, "LOG_ADDRS"), (0x4, "TRANSMIT"), (0x8, "PASSTHROUGH"), (0x10, "RC"),
             (0x20, "MONITOR_ALL"), (0x40, "NEEDS_HPD"), (0x80, "MONITOR_PIN"), (0x100, "CONNECTOR_INFO"))
CEC_CAP_TRANSMIT = 0x4
CEC_MSG = struct.Struct("=QQIIII16sBBBBBBBx")   # struct cec_msg, 56 байт
# --- evdev ------------------------------------------------------------------
INPUT_EVENT = struct.Struct("llHHi")             # struct input_event (нативный long)
EV_KEY = 0x01
KEY_MUTE, KEY_VOLUMEDOWN, KEY_VOLUMEUP = 113, 114, 115
EVIOCGBIT_KEY = 0x80604521                       # EVIOCGBIT(EV_KEY, 96)
# --- inotify ----------------------------------------------------------------
IN_MODIFY, IN_ATTRIB, IN_DELETE_SELF, IN_MOVE_SELF = 0x2, 0x4, 0x400, 0x800

# " RX507A0C" / " TX054441" - направление + байты кадра подряд (формат cecmessages)
FRAME_RE = re.compile(r"\b(RX|TX)((?:[0-9A-Fa-f]{2}){2,16})\b")
STAMP_RE = re.compile(r"(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d(?:\.\d+)?)")

_LOG = {"path": None, "debug": False, "stderr": False, "lock": threading.Lock()}


def log(msg, echo=True):
    now = time.time()
    line = "%s.%03d %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                             int(now * 1000) % 1000, msg)
    with _LOG["lock"]:
        if _LOG["stderr"] and echo:
            sys.stderr.write(line)
        path = _LOG["path"]
        if not path:
            return
        try:
            if os.path.getsize(path) > 256 * 1024:      # простая ротация
                os.replace(path, path + ".1")
        except OSError:
            pass
        try:
            with open(path, "a") as f:
                f.write(line)
        except OSError:
            pass


def dbg(msg):
    if _LOG["debug"]:
        log("debug: " + msg)


def hexs(data):
    return ":".join("%02X" % b for b in bytearray(data))


class Unavailable(Exception):
    """Компонент недоступен на этой прошивке - повтор редко и без спама в лог."""


# --- разбор кадров ----------------------------------------------------------
def parse_frames(line):
    """'... TM182747 RX507A0C' -> [('RX', b'\\x50\\x7a\\x0c')]"""
    return [(m.group(1), bytes(bytearray.fromhex(m.group(2)))) for m in FRAME_RE.finditer(line)]


def classify(frame):
    """Кадр -> ('report', status) | ('key', 'up'|'down'|'mute') | ('release',) | ('level', x) | None."""
    if len(frame) < 2:
        return None
    src, dst, op = frame[0] >> 4, frame[0] & 0x0F, frame[1]
    if op == OP_REPORT_AUDIO_STATUS and src == LA_AUDIO and len(frame) >= 3:
        return ("report", frame[2])
    if src == LA_TV and dst == LA_AUDIO:
        if op == OP_UC_PRESSED and len(frame) >= 3 and frame[2] in UC_KEYS:
            return ("key", UC_KEYS[frame[2]])
        if op == OP_UC_RELEASED:
            return ("release",)
        if op == OP_SET_AUDIO_VOLUME_LEVEL and len(frame) >= 3:
            return ("level", frame[2])           # только для защиты native-режима от обратной связи
    return None


def decode_status(status):
    """Байт Audio Status: бит7 = mute, биты0-6 = громкость 0..100 (0x7F = неизвестно)."""
    vol = status & 0x7F
    return bool(status & 0x80), (vol if vol <= 100 else None)


def parse_arc_volume(text):
    """Ответ eim/getArcVolume -> (громкость 0..100, mute | None) или None.
    OLED65M59LA (10.3.0): {"Volume":10}, без returnValue. 128..228 = бит mute, как в байте 0x7A (догадка)."""
    i = text.find("{")
    if i < 0:
        return None
    try:
        obj = json.JSONDecoder().raw_decode(text[i:])[0]
    except ValueError:
        return None
    if not isinstance(obj, dict) or obj.get("returnValue") is False:
        return None
    vol = obj.get("Volume", obj.get("volume"))
    if not isinstance(vol, int) or isinstance(vol, bool):
        return None
    if 0 <= vol <= 100:
        return vol, None
    if 0x80 <= vol <= 0x80 + 100:
        return vol - 0x80, True
    return None


def short_reply(text, limit=120):
    line = " ".join(text.split())
    return (line[:limit] + "...") if len(line) > limit else (line or "no reply")


def output_allowed(name, cfg):
    if cfg.any_output or not name:          # неизвестно -> не блокируем (7A шлёт только аудиосистема)
        return True
    allowed = [s.strip() for s in (cfg.outputs or "").split(",") if s.strip()]
    if allowed:
        return name in allowed
    return "arc" in name.lower()            # external_arc и пр.


# --- логика показа ----------------------------------------------------------
class Hub(object):
    """Все решения "показывать ли". Потокобезопасно; clock подменяется в тестах."""

    def __init__(self, publish, cfg, clock=time.monotonic, wall=time.time, native=None):
        self.publish, self.cfg, self.clock, self.wall, self.native = publish, cfg, clock, wall, native
        self.lock = threading.RLock()
        self.raw = None             # последняя реальная громкость 0..100 (от саундбара)
        self.mute = None
        self.output = None
        self.output_ok = True
        self.key_at = -1e9
        self.key_since_report = False
        self.last_publish_at = -1e9
        self.last_limit_at = -1e9
        self.key_ref = (-1e9, 0.0)   # (monotonic, epoch) последнего события громкости ТВ - от него гаснет и нативный
        self.remote_at = -1e9
        self.remote_echo = False     # ждём CEC-эхо физического нажатия (его время уже учтено)
        self.visible_until = -1e9    # когда оверлей погаснет (оценка)
        self.tv_mute = None          # muteStatus ТВ (audio getVolume) - mute для значений EIM
        self.activity = None         # threading.Event опроса EIM: нажатие будит его сразу

    def offset(self):
        try:
            with open(self.cfg.offset_file) as f:      # читается каждый раз - правка без рестарта
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def display(self, raw):
        shown = int(raw * self.cfg.scale + 0.5) + self.cfg.offset + self.offset()
        return max(0, min(self.cfg.max, shown))

    def set_output(self, name):
        with self.lock:
            ok = output_allowed(name, self.cfg)
            if name != self.output:
                log("sound output: %s -> indicator %s" % (name or "unknown", "on" if ok else "off"))
            if ok and not self.output_ok:
                self.raw = self.mute = None          # после переключения на ARC - тихая базовая точка
            self.output, self.output_ok = name, ok

    def native_on(self):
        return self.native is not None and self.native.active()

    def set_tv_state(self, volume, mute):
        if mute is not None:
            self.tv_mute = mute
        if self.native is None:
            return
        with self.lock:
            if self.native.tv_state(volume, mute) and self.raw is not None and self.output_ok and self.native_on():
                self._native(self.raw, self.mute, True, False, False)    # состояние ТВ стало известно

    def forget_tv_state(self):
        self.tv_mute = None
        if self.native is not None:
            self.native.forget_tv_state()

    def _key_time(self, kind, source, now):
        if kind == "mute":
            return
        if source == "remote":
            self.key_ref, self.remote_at, self.remote_echo = (now, self.wall()), now, True
        elif source == "cec":
            if self.remote_echo and now - self.remote_at < 0.3:
                self.remote_echo = False             # CEC-эхо нажатия: время нажатия точнее
            else:                                    # повтор удержания / нажатие без читаемого пульта
                self.key_ref = (now - KEY_CEC_LAG, self.wall() - KEY_CEC_LAG)

    def on_key(self, kind, source):
        with self.lock:
            now = self.clock()
            self.key_at = now
            self.key_since_report = True
            self._key_time(kind, source, now)
            dbg("key %s (%s)" % (kind, source))
            if self.activity is not None:
                self.activity.set()                  # опрос EIM - сразу и часто
            if self.native_on():
                return                               # нативный индикатор появляется сам, мгновенно
            if not self.output_ok or self.raw is None or kind == "mute":
                return
            if self._at_limit(kind):
                # на 0/100 саундбар часто молчит - повторно показать границу (не чаще 300 мс)
                if now - self.last_limit_at >= 0.3:
                    self.last_limit_at = now
                    self._emit("limit")
                return
            if self.cfg.prelaunch and now > self.visible_until - MIN_VISIBLE:
                # окно оверлея не видно (или вот-вот погаснет): открыть сразу с ПОСЛЕДНИМ РЕАЛЬНЫМ
                # значением, ответ саундбара (~0.25 с после нажатия) обновит число в открытом окне
                self._emit("key")

    def on_report(self, status, source):
        mute, raw = decode_status(status)
        if raw is None:
            dbg("audio status 0x%02X: volume unknown, ignored" % status)
            return
        self._on_volume(raw, mute, source, 0.0)

    def on_level(self, raw, mute, source):
        """Громкость без байта Audio Status (EIM getArcVolume). mute None -> muteStatus ТВ, если известен."""
        self._on_volume(raw, bool(self.tv_mute) if mute is None else mute, source, EIM_SETTLE)

    def _on_volume(self, raw, mute, source, settle):
        with self.lock:
            now = self.clock()
            key_recent = self.key_since_report and now - self.key_at <= self.cfg.key_window
            first = self.raw is None
            changed = raw != self.raw or mute != self.mute
            if not changed and key_recent and now - self.key_at < settle:
                return                       # опрос опередил саундбар: ждать нового значения
            self.raw, self.mute = raw, mute
            self.key_since_report = False
            dbg("report raw=%d mute=%s (%s) first=%s changed=%s key=%s%s"
                % (raw, mute, source, first, changed, key_recent,
                   " (%.0f ms after key)" % ((now - self.key_at) * 1000) if key_recent else ""))
            if not self.output_ok:
                return
            if self.native_on():
                self._native(raw, mute, first, changed, key_recent)
                return
            if first and not key_recent:
                log("baseline volume %d%s (not shown)" % (raw, " muted" if mute else ""))
                return
            if changed:
                self._emit("report")
            elif key_recent:
                self._emit("repeat")         # нажали, а значение то же (граница/шаг) - показать

    def _native(self, raw, mute, first, changed, key_recent):
        if mute:
            dbg("native: soundbar muted - TV value untouched")
            return
        value = self.display(raw)
        if first and not key_recent:
            log("baseline volume %d -> TV value (only if different)" % raw)
            self.native.push(value, False, "start")
        elif changed or key_recent:
            # после нажатия - всегда (ТВ сам насчитал и мог совпасть, а QML берёт только cause setVolume)
            self.native.push(value, key_recent, "key" if key_recent else "external")

    def tv_frame(self, ev, now, idle):
        """Кадр громкости ТВ->саундбар (05:44 / 05:73) - только для защиты native-режима."""
        if not self.native_on():
            return
        if ev[0] == "level":
            if self.raw is not None and ev[1] & 0x7F == self.raw:
                return                               # тот же уровень, что у саундбара - безвредно
            self.native.feedback(now, idle, "05:73:%02X" % ev[1])
        elif ev[0] == "key" and ev[1] != "mute":
            self.native.feedback(now, idle, "05:44 " + ev[1])

    def _at_limit(self, kind):
        shown = self.display(self.raw)
        if kind == "up":
            return self.raw >= 100 or shown >= self.cfg.max
        return self.raw <= 0 or shown <= 0

    def _emit(self, reason):
        now = self.clock()
        self.last_publish_at = now
        p = {"volume": self.display(self.raw), "mute": bool(self.mute), "raw": self.raw,
             "max": self.cfg.max, "reason": reason}
        ttl = self.cfg.visible_ms / 1000.0
        ref, ref_wall = self.key_ref
        if now - ref < ttl:
            # оверлей гаснет через ttl после ПОСЛЕДНЕГО НАЖАТИЯ (как нативный), а не после ответа саундбара
            p["kt"] = int(ref_wall * 1000)
            self.visible_until = max(ref + ttl, now + MIN_VISIBLE)
        else:
            self.visible_until = now + ttl
        self.publish(p)


class Publisher(object):
    """luna-send .../applicationManager/launch: один запрос в полёте, побеждает последнее значение."""

    def __init__(self, app_id, extra, dry_run=False, timeout=3.0):
        self.app_id, self.extra, self.dry_run, self.timeout = app_id, extra, dry_run, timeout
        self.sid = "%08x" % random.getrandbits(32)     # id сессии: оверлей отбросит старые seq
        self.seq = 0
        self.pending = None
        self.cv = threading.Condition()
        self.luna = shutil.which("luna-send") or "/usr/bin/luna-send"
        self.reject, self.reject_at, self.reject_count = None, -1e9, 0
        if not dry_run:
            t = threading.Thread(target=self._worker, name="publisher")
            t.daemon = True
            t.start()

    def __call__(self, params):
        with self.cv:
            self.seq += 1
            p = dict(params)
            p.update(self.extra)
            p.update(seq=self.seq, sid=self.sid, src="cec")
            if self.dry_run:
                line = "PUBLISH " + json.dumps(p, sort_keys=True)
                print(line)
                sys.stdout.flush()
                log(line, echo=False)
                return
            self.pending = p                  # перезаписывает невыпущенное старое значение
            self.cv.notify()

    def _worker(self):
        while True:
            with self.cv:
                while self.pending is None:
                    self.cv.wait()
                p, self.pending = self.pending, None
            payload = json.dumps({"id": self.app_id, "params": p}, separators=(",", ":"))
            t0 = time.monotonic()
            try:
                r = subprocess.run([self.luna, "-n", "1", APP_LAUNCH, payload], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout)
                out = r.stdout.decode("utf-8", "replace")
                if '"returnValue":false' in out.replace(" ", ""):
                    self.rejected(out.strip()[:300], time.monotonic())
                dbg("launch %s/%s %d in %d ms" % (p["reason"], p["seq"], p["volume"],
                                                  (time.monotonic() - t0) * 1000))
            except subprocess.TimeoutExpired:
                log("launch timed out")
            except OSError as e:
                log("luna-send failed: %s" % e)
                time.sleep(1)

    def rejected(self, text, now):
        # приложение удалили (например, в Homebrew Channel), а мост работает: не писать отказ на каждое нажатие
        if text == self.reject and now - self.reject_at < REJECT_RELOG:
            self.reject_count += 1
            return
        more = " [%d repeats not logged]" % self.reject_count if self.reject_count else ""
        log("launch rejected: %s - is the eArcNG app (%s) installed?%s" % (text, self.app_id, more))
        self.reject, self.reject_at, self.reject_count = text, now, 0


class NativeSink(object):
    """--ui native: реальное значение -> счётчик громкости ТВ (luna audio/master/setVolume, cause "setVolume").
    С QML-патчем (native-osd.sh, метод trk501) нативный индикатор показывает только такие значения,
    а появляется и гаснет сам - мгновенно и с нативными таймингами. Правила trk501: пока ТВ или саундбар
    в mute - ничего не трогать (setVolume снимает mute); без нажатия - только при расхождении."""

    def __init__(self, dry_run=False, clock=time.monotonic, timeout=3.0):
        self.dry_run, self.clock, self.timeout = dry_run, clock, timeout
        self.mounts = os.environ.get("CEC_MOUNTS", "/proc/mounts")      # переопределяются в тестах
        self.qml = os.environ.get("CEC_QML_FILE", QML_VOLUME)
        self.cv = threading.Condition()
        self.tv_volume = self.tv_mute = None
        self.skipped = None           # значение, не выставленное из-за mute ТВ (выставить после unmute)
        self.pending = None
        self.disabled = None          # причина отключения (обратная связь / отказы setVolume)
        self.rejects = 0
        self.home = os.environ.get("CEC_DIR") or os.path.dirname(os.path.abspath(__file__))
        self.set_start = self.set_done = -1e9
        self.sets = 0                 # номер последнего setVolume
        self.hit_set = 0              # номер setVolume, после которого ТВ уже ответил кадром громкости
        self.streak = 0
        self.unknown_said = False
        self._active, self._checked = False, -1e9
        self.luna = shutil.which("luna-send") or "/usr/bin/luna-send"
        if not dry_run:
            t = threading.Thread(target=self._worker, name="native")
            t.daemon = True
            t.start()

    def patch_mounted(self):
        try:
            with open(self.mounts) as f:
                if (" %s " % self.qml) not in f.read():
                    return False
            with open(self.qml) as f:
                return "shownVolume" in f.read()
        except OSError:
            return False

    def refresh(self):
        """Смонтирован ли QML-патч. Перепроверка раз в 5 с (патч ставится в фоне), смена - в лог."""
        now = self.clock()
        if now - self._checked >= NATIVE_RECHECK:
            self._checked = now
            was, self._active = self._active, self.patch_mounted()
            if self._active != was:
                log("native OSD patch %s" % ("active: values go to the system indicator" if self._active
                                             else "not active: overlay fallback"))
        return self._active

    def active(self):
        """QML-патч смонтирован, защита не сработала и состояние ТВ (getVolume) известно - без него
        setVolume вслепую не шлём, показывает оверлей."""
        if not self.refresh() or self.disabled is not None:
            return False
        if self.tv_mute is None:
            if not self.unknown_said:
                self.unknown_said = True
                log("native: TV volume state (getVolume) not known yet - overlay until it is")
            return False
        return True

    def tv_state(self, volume, mute):
        """Из подписки getVolume. True - состояние mute ТВ только что стало известно."""
        with self.cv:
            known = self.tv_mute is not None
            unmuted = self.tv_mute is True and mute is False
            if volume is not None:
                self.tv_volume = volume
            if mute is not None:
                self.tv_mute = mute
            if unmuted and self.skipped is not None:
                value, self.skipped = self.skipped, None
                self._queue(value, "unmute")             # пропущенное, пока ТВ был в mute
            return not known and self.tv_mute is not None

    def forget_tv_state(self):
        """Подписка getVolume оборвалась: до новых данных - оверлей (как trk501: не трогать вслепую)."""
        with self.cv:
            self.tv_volume = self.tv_mute = None
            self.unknown_said = False

    def push(self, value, forced, why):
        """value - отображаемое 0..max; forced - ответ на нажатие (иначе только при расхождении)."""
        with self.cv:
            if self.tv_mute is None:
                dbg("native: TV state unknown - %d not set" % value)
                return
            if self.tv_mute:
                self.skipped = value
                dbg("native: TV muted - %d not set yet (setVolume would unmute)" % value)
                return
            self.skipped = None
            if not forced and value == self.tv_volume:
                return
            self._queue(value, why)

    def _queue(self, value, why):     # вызывается под self.cv
        if self.disabled is not None:
            return
        if self.dry_run:
            self._begin_set()
            self.set_start = self.set_done = self.clock()
            self.tv_volume = value
            line = "NATIVE setVolume %d (%s)" % (value, why)
            print(line)
            sys.stdout.flush()
            log(line, echo=False)
            return
        self.pending = (value, why)   # побеждает последнее значение
        self.cv.notify()

    def _worker(self):
        while True:
            with self.cv:
                while self.pending is None:
                    self.cv.wait()
                (value, why), self.pending = self.pending, None
                self._begin_set()
                self.set_start = t0 = self.clock()
            payload = json.dumps({"volume": value}, separators=(",", ":"))
            try:
                r = subprocess.run([self.luna, "-n", "1", AUDIO_SET_VOLUME, payload], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout)
                out = r.stdout.decode("utf-8", "replace")
                if '"returnValue":false' in out.replace(" ", ""):
                    log("setVolume rejected: " + out.strip()[:300])
                    with self.cv:
                        self.rejects += 1
                        if self.rejects >= REJECT_STREAK and self.disabled is None:
                            self._disable("TV rejects setVolume %d times in a row" % self.rejects)
                else:
                    with self.cv:
                        self.rejects = 0
                        self.tv_volume = value
                dbg("native setVolume %d (%s) in %d ms" % (value, why, (self.clock() - t0) * 1000))
            except subprocess.TimeoutExpired:
                log("setVolume timed out")
            except OSError as e:
                log("luna-send failed: %s" % e)
                time.sleep(1)
            finally:
                with self.cv:
                    self.set_done = self.clock()

    def _begin_set(self):             # вызывается под self.cv
        if self.hit_set != self.sets:
            self.streak = 0           # прошлый setVolume остался без кадра ТВ - серия прервана
        self.sets += 1

    def feedback(self, now, idle, what):
        """Кадр громкости ТВ->саундбар сразу после нашего setVolume, хотя пульт не трогали?
        Так выглядела бы петля (ТВ пересчитывает setVolume в команды саундбару) - 3 раза подряд -> выкл."""
        with self.cv:
            if self.disabled is not None or idle is None or idle < FEEDBACK_IDLE or self.sets == 0:
                return
            in_flight = self.set_start > self.set_done
            if not (in_flight or self.set_start <= now <= self.set_done + FEEDBACK_WINDOW):
                return
            if self.hit_set == self.sets:
                return                                  # этот setVolume уже засчитан
            self.streak = self.streak + 1 if self.hit_set == self.sets - 1 else 1
            self.hit_set = self.sets
            dbg("native: TV sent %s right after setVolume (%d/%d)" % (what, self.streak, FEEDBACK_STREAK))
            if self.streak >= FEEDBACK_STREAK:
                self._disable("TV answers setVolume with CEC volume commands (%s)" % what)

    def _disable(self, reason):       # вызывается под self.cv
        """Синхронизация небезопасна/невозможна: больше не трогать ТВ, вернуть штатный индикатор
        (иначе он показывал бы устаревшее число рядом с оверлеем), не ставить патч при загрузке."""
        self.disabled = reason
        log("native sync OFF: %s - original indicator restored, overlay from now on "
            "(retry: earcng.sh <TV> ui native)" % reason)
        if not self.dry_run:
            t = threading.Thread(target=self._revert, name="native-revert")
            t.daemon = True
            t.start()

    def _revert(self):
        try:
            open(os.path.join(self.home, "no-native"), "a").close()
            r = subprocess.run(["sh", os.path.join(self.home, "native-osd.sh"), "revert"], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
            log("native-osd.sh revert: " + r.stdout.decode("utf-8", "replace").strip()[:200])
        except (OSError, subprocess.SubprocessError) as e:
            log("native-osd.sh revert failed: %s" % e)


# --- /var/log/cecmessages ---------------------------------------------------
class LineSink(object):
    """Строки лога -> Hub. Из пачки берётся только НОВЕЙШИЙ 7A (лог пишется с задержкой пачками).
    7A из лога не нужен, если его видит шина или значения даёт EIM (лог запаздывает)."""

    def __init__(self, hub, bus=None, eim=None):
        self.hub, self.bus, self.eim = hub, bus, eim

    def __call__(self, lines):
        events = [ev for line in lines for _, fr in parse_frames(line) for ev in [classify(fr)] if ev]
        last = max([i for i, ev in enumerate(events) if ev[0] == "report"] or [-1])
        bus, eim = self.bus, self.eim
        reports = not (bus and bus.sees_reports) and not (eim and eim.takes_reports())
        for i, ev in enumerate(events):
            if ev[0] == "report" and i == last and reports:
                self.hub.on_report(ev[1], "log")
            elif ev[0] == "key" and not (bus and bus.sees_keys):
                self.hub.on_key(ev[1], "log")


def load_libc():
    for name in ("libc.so.6", None):
        try:
            libc = ctypes.CDLL(name, use_errno=True)
            if hasattr(libc, "inotify_init1"):
                return libc
        except OSError:
            pass
    return None


class LogFollower(object):
    """tail -F на inotify: ждёт файл, переживает ротацию/усечение, при старте пропускает историю."""

    def __init__(self, path, on_lines, poll=1.0):
        self.path, self.on_lines, self.poll = path, on_lines, poll
        self.skip_history = True

    def __call__(self, stop):
        libc = load_libc()
        while not stop.is_set():
            try:
                f = open(self.path, "rb")
            except OSError:
                self.skip_history = False         # файл появится позже - читать с начала
                stop.wait(self.poll)
                continue
            try:
                self._follow(f, libc, stop)
            finally:
                f.close()

    def _follow(self, f, libc, stop):
        if self.skip_history:
            f.seek(0, os.SEEK_END)
        self.skip_history = False
        ino = os.fstat(f.fileno()).st_ino
        ifd = -1
        if libc is not None:
            ifd = libc.inotify_init1(os.O_CLOEXEC | os.O_NONBLOCK)
            mask = IN_MODIFY | IN_ATTRIB | IN_DELETE_SELF | IN_MOVE_SELF
            if ifd >= 0 and libc.inotify_add_watch(ifd, self.path.encode(), mask) < 0:
                os.close(ifd)
                ifd = -1
        rest = b""
        try:
            while not stop.is_set():
                data = f.read()
                if data:
                    lines = (rest + data).split(b"\n")
                    rest = lines.pop()
                    if len(rest) > 65536:
                        rest = b""
                    if lines:
                        self.on_lines([ln.decode("ascii", "replace") for ln in lines])
                    continue
                try:
                    st = os.stat(self.path)
                except OSError:
                    return                                    # удалён/переименован - переоткрыть
                if st.st_ino != ino or st.st_size < f.tell():
                    return                                    # ротация или усечение
                if ifd >= 0:
                    if select.select([ifd], [], [], self.poll)[0]:
                        try:
                            os.read(ifd, 4096)
                        except OSError:
                            pass
                else:
                    stop.wait(0.2)                            # без inotify - частый опрос
        finally:
            if ifd >= 0:
                os.close(ifd)


# --- пульт (/dev/input), только чтение --------------------------------------
class KeyState(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.held, self.down_at, self.up_at, self.devices = False, 0.0, 0.0, 0

    def set(self, down):
        with self.lock:
            if down:
                self.held, self.down_at = True, time.monotonic()
            else:
                self.held, self.up_at = False, time.monotonic()

    def snapshot(self):
        with self.lock:
            return self.held, self.down_at, self.devices

    def idle_for(self, now):
        """Секунд без физических нажатий/отпусканий громкости; None - пульт не читается."""
        with self.lock:
            if self.devices == 0:
                return None
            return 0.0 if self.held else now - max(self.down_at, self.up_at)


def input_names():
    names = {}
    try:
        with open("/proc/bus/input/devices") as f:
            blocks = f.read().split("\n\n")
    except OSError:
        return names
    for block in blocks:
        name = re.search(r'N: Name="([^"]*)"', block)
        ev = re.search(r"\b(event\d+)", block)
        if name and ev:
            names[ev.group(1)] = name.group(1)
    return names


def open_volume_inputs():
    """Все event-устройства с KEY_VOLUMEUP (без grab - кнопки по-прежнему доходят до ТВ)."""
    names, fds = input_names(), {}
    for path in sorted(glob.glob("/dev/input/event*")):
        ev = os.path.basename(path)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            bits = bytearray(96)
            fcntl.ioctl(fd, EVIOCGBIT_KEY, bits)
            ok = bool(bits[KEY_VOLUMEUP // 8] & (1 << (KEY_VOLUMEUP % 8)))
        except OSError:
            ok = names.get(ev, "").startswith("LGE")          # эвристика trk501
        if ok:
            fds[fd] = "%s(%s)" % (ev, names.get(ev, "?"))
        else:
            os.close(fd)
    return fds


class InputWatcher(object):
    def __init__(self, hub, keys, rescan=30.0):
        self.hub, self.keys, self.rescan = hub, keys, rescan

    def __call__(self, stop):
        fds, shown, rescan_at = {}, None, 0.0
        try:
            while not stop.is_set():
                now = time.monotonic()
                if now >= rescan_at:                 # Magic Remote может переподключиться
                    for fd in fds:
                        os.close(fd)
                    fds = open_volume_inputs()
                    with self.keys.lock:
                        self.keys.devices = len(fds)
                    names = sorted(fds.values())
                    if names != shown:
                        log("remote inputs: %s" % (", ".join(names) or "none (tap guard inactive)"))
                        shown = names
                    rescan_at = now + self.rescan
                if not fds:
                    stop.wait(max(1.0, rescan_at - now))
                    continue
                for fd in select.select(list(fds), [], [], 1.0)[0]:
                    try:
                        raw = os.read(fd, INPUT_EVENT.size * 32)
                    except OSError:
                        rescan_at = 0.0                  # устройство пропало
                        continue
                    self.events(raw)
        finally:
            for fd in fds:
                os.close(fd)

    def events(self, raw):
        for i in range(0, len(raw) - INPUT_EVENT.size + 1, INPUT_EVENT.size):
            _, _, typ, code, val = INPUT_EVENT.unpack_from(raw, i)
            if typ != EV_KEY:
                continue
            if code in (KEY_VOLUMEUP, KEY_VOLUMEDOWN) and val in (0, 1):   # 2 = автоповтор
                self.keys.set(val == 1)
                if val == 1:
                    self.hub.on_key("up" if code == KEY_VOLUMEUP else "down", "remote")
            elif code == KEY_MUTE and val == 1:
                self.hub.on_key("mute", "remote")


# --- шина CEC: монитор + tap guard + финальный запрос -----------------------
FRAME_RELEASE = bytes(bytearray([(LA_TV << 4) | LA_AUDIO, OP_UC_RELEASED]))
FRAME_GIVE_AUDIO = bytes(bytearray([(LA_TV << 4) | LA_AUDIO, OP_GIVE_AUDIO_STATUS]))


class TapGuard(object):
    """Когда слать 05:45 (антиспам быстрых тапов, логика trk501) и 05:71 (контроль в конце серии)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.open_press, self.press_at, self.query_due = False, 0.0, None

    def on_event(self, ev, now):
        if ev[0] == "release":
            self.open_press = False
        elif ev[0] == "key" and ev[1] != "mute":
            self.open_press, self.press_at = True, now      # ТВ послал "нажато" без "отпущено"
            self.query_due = now + self.cfg.query_delay

    def tick(self, now, held, down_at, tap_ok):
        out = []
        if self.open_press:
            if not held and now - self.press_at >= self.cfg.tap_grace:
                self.open_press = False                     # кнопка уже отпущена -> "отпущено"
                if tap_ok:
                    out.append(FRAME_RELEASE)
            elif held and now - down_at > self.cfg.hold:
                self.open_press = False                     # удержание: саундбар повторяет сам
        if self.query_due is not None and now >= self.query_due:
            self.query_due = None
            if self.cfg.final_query:
                out.append(FRAME_GIVE_AUDIO)
        return out


class CecBus(object):
    def __init__(self, cfg, hub, keys):
        self.cfg, self.hub, self.keys = cfg, hub, keys
        self.sees_reports = self.sees_keys = False     # тогда лог для этих событий игнорируется
        self.running = False                           # монитор открыт (для --arc-source auto)

    def device(self):
        if self.cfg.cec_dev:
            return self.cfg.cec_dev if os.path.exists(self.cfg.cec_dev) else None
        devs = sorted(glob.glob("/dev/cec[0-9]*"))
        return devs[0] if devs else None

    def __call__(self, stop):
        path = self.device()
        if not path:
            raise Unavailable("no /dev/cec* - using /var/log/cecmessages only, tap guard off")
        try:
            mon = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            raise Unavailable("%s: %s" % (path, e))
        tx = None
        try:
            try:
                fcntl.ioctl(mon, CEC_S_MODE, struct.pack("I", CEC_MODE_MONITOR))
            except OSError as e:
                raise Unavailable("%s monitor mode: %s" % (path, e))
            try:
                tx = os.open(path, os.O_RDWR)
                fcntl.ioctl(tx, CEC_S_MODE, struct.pack("I", CEC_MODE_INITIATOR))
            except OSError as e:
                log("%s: transmit unavailable (%s) - tap guard/final query off" % (path, e))
                if tx is not None:
                    os.close(tx)
                tx = None
            log("CEC monitor on %s%s" % (path, "" if tx is not None else " (read-only)"))
            self.running = True
            self.loop(stop, mon, tx)
        finally:
            self.sees_reports = self.sees_keys = self.running = False
            os.close(mon)
            if tx is not None:
                os.close(tx)

    def tap_enabled(self, devices):
        # без читаемого пульта любое удержание выглядело бы как тап - тогда ничего не шлём (как trk501)
        return self.cfg.tap_guard and devices > 0 and not os.path.exists(self.cfg.no_tap_file)

    def send(self, tx, data):
        if self.cfg.dry_run:                     # --dry-run: никаких побочных эффектов на шине
            log("dry-run: would send %s" % hexs(data))
            return
        msg = bytearray(CEC_MSG.pack(0, 0, len(data), 0, 0, 0, data, 0, 0, 0, 0, 0, 0, 0))
        try:
            fcntl.ioctl(tx, CEC_TRANSMIT, msg)
            dbg("cec tx %s" % hexs(data))
        except OSError as e:
            log("cec tx %s failed: %s" % (hexs(data), e))

    def loop(self, stop, mon, tx):
        buf = bytearray(CEC_MSG.size)
        guard = TapGuard(self.cfg)
        if tx is not None and self.cfg.final_query:
            self.send(tx, FRAME_GIVE_AUDIO)      # базовое значение сразу после старта (тихо, без показа)
        while not stop.is_set():
            busy = guard.open_press or guard.query_due is not None
            if select.select([mon], [], [], 0.02 if busy else 1.0)[0]:
                for _ in range(16):
                    try:
                        fcntl.ioctl(mon, CEC_RECEIVE, buf)
                    except OSError as e:
                        if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                            break
                        raise
                    msg = CEC_MSG.unpack(bytes(buf))
                    ev = classify(msg[6][:min(16, msg[2])])
                    if ev is None:
                        continue
                    now = time.monotonic()
                    guard.on_event(ev, now)
                    if ev[0] == "level" or (ev[0] == "key" and ev[1] != "mute"):
                        self.hub.tv_frame(ev, now, self.keys.idle_for(now))
                    if ev[0] == "report":
                        self.sees_reports = True
                        if self.cfg.arc_source != "eim":     # --arc-source eim: значения только из EIM
                            self.hub.on_report(ev[1], "cec")
                    elif ev[0] == "key":
                        self.sees_keys = True
                        self.hub.on_key(ev[1], "cec")
            held, down_at, devices = self.keys.snapshot()
            tap_ok = guard.open_press and self.tap_enabled(devices)
            for frame in guard.tick(time.monotonic(), held, down_at, tap_ok):
                if tx is not None:
                    self.send(tx, frame)


# --- подписка на аудиосервис (только soundOutput) ---------------------------
def _cstr(raw):
    return raw.split(b"\0")[0].decode("ascii", "replace")


def probe_cec(devices=None, ioctl=fcntl.ioctl, opener=os.open, closer=os.close):
    """--probe: какие CEC-адаптеры есть и что с ними можно. В шину ничего не передаётся:
    только G_CAPS/G_LOG_ADDRS/G_PHYS_ADDR и пробная установка режима на своём дескрипторе."""
    devices = sorted(glob.glob("/dev/cec[0-9]*")) if devices is None else devices
    if not devices:
        return ["CEC: no /dev/cec* - log-only mode, tap guard off"]
    out = []
    for dev in devices:
        try:
            fd = opener(dev, os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            out.append("CEC %s: open failed: %s" % (dev, e))
            continue
        try:
            caps, las, pa = bytearray(CEC_CAPS.size), bytearray(CEC_LOG_ADDRS_SIZE), bytearray(2)
            ioctl(fd, CEC_ADAP_G_CAPS, caps)
            ioctl(fd, CEC_ADAP_G_LOG_ADDRS, las)
            ioctl(fd, CEC_ADAP_G_PHYS_ADDR, pa)
        except (OSError, IOError) as e:
            out.append("CEC %s: ioctl failed (%s) - not a Linux CEC adapter, log-only mode" % (dev, e))
            continue
        finally:
            closer(fd)
        drv, name, _avail, cap, _ver = CEC_CAPS.unpack(bytes(caps))
        addrs = list(bytearray(las[:min(las[7], 4)]))
        phys = struct.unpack("=H", bytes(pa))[0]
        out.append("CEC %s: Linux CEC API ok, driver=%s name=%s caps=%s" % (
            dev, _cstr(drv), _cstr(name), "|".join(n for bit, n in CAP_NAMES if cap & bit) or "none"))
        out.append("CEC %s: logical addresses %s, physical %x.%x.%x.%x" % (
            dev, addrs, phys >> 12, (phys >> 8) & 15, (phys >> 4) & 15, phys & 15))
        modes = []
        for label, mode in (("monitor", CEC_MODE_MONITOR), ("initiator", CEC_MODE_INITIATOR)):
            try:
                fd = opener(dev, os.O_RDWR | os.O_NONBLOCK)
            except OSError as e:
                modes.append("%s: %s" % (label, e))
                continue
            try:
                ioctl(fd, CEC_S_MODE, struct.pack("I", mode))
                modes.append("%s: ok" % label)
            except (OSError, IOError) as e:
                modes.append("%s: %s" % (label, e))
            finally:
                closer(fd)
        tx_ok = cap & CEC_CAP_TRANSMIT and LA_TV in addrs and "initiator: ok" in modes
        out.append("CEC %s: %s -> %s" % (dev, ", ".join(modes),
                                         "tap guard + 05:71 available" if tx_ok and "monitor: ok" in modes
                                         else "real-time monitor only" if "monitor: ok" in modes
                                         else "log-only mode"))
    return out


def die_with_parent():
    try:
        ctypes.CDLL("libc.so.6").prctl(1, 9)    # PR_SET_PDEATHSIG = SIGKILL
    except Exception:                            # noqa: BLE001 - не критично
        pass


class AudioSubscriber(object):
    def __init__(self, hub):
        self.hub = hub

    def __call__(self, stop):
        exe = shutil.which("luna-send")
        if not exe:
            raise Unavailable("luna-send not found - output gate off")
        master, slave = os.openpty()             # luna-send надёжно пишет только в терминал
        try:
            p = subprocess.Popen([exe, "-i", AUDIO_GET_VOLUME, '{"subscribe":true}'],
                                 stdin=subprocess.DEVNULL, stdout=slave, stderr=subprocess.DEVNULL,
                                 preexec_fn=die_with_parent, close_fds=True)
        finally:
            os.close(slave)
        buf = b""
        try:
            while not stop.is_set():
                try:
                    data = os.read(master, 4096)
                except OSError:
                    break
                if not data:
                    break
                lines = (buf + data).split(b"\n")
                buf = lines.pop()
                for line in lines:
                    self.handle(line)
        finally:
            p.kill()
            p.wait()
            os.close(master)
            self.hub.set_output(None)
            self.hub.forget_tv_state()

    def handle(self, line):
        text = line.decode("utf-8", "replace").strip()
        i = text.find("{")
        if i < 0:
            return
        try:
            obj = json.loads(text[i:])
        except ValueError:
            return
        st = obj.get("volumeStatus") if isinstance(obj.get("volumeStatus"), dict) else obj
        if isinstance(st.get("soundOutput"), str):
            self.hub.set_output(st["soundOutput"])
        vol = st.get("volume")
        mute = next((st[k] for k in ("muteStatus", "mute", "muted") if isinstance(st.get(k), bool)), None)
        vol = vol if isinstance(vol, int) and not isinstance(vol, bool) else None
        if vol is not None or mute is not None:
            self.hub.set_tv_state(vol, mute)          # нужно только --ui native


# --- громкость ARC от eim-сервиса (--arc-source auto|eim) ---------------------
def eim_query(luna=None, timeout=EIM_TIMEOUT):
    """Один вызов getArcVolume. stdin - /dev/null: из канала luna-send на ТВ ничего не выводит."""
    luna = luna or shutil.which("luna-send") or "/usr/bin/luna-send"
    r = subprocess.run([luna, "-n", "1", EIM_GET_ARC_VOLUME, "{}"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    return r.stdout.decode("utf-8", "replace")


def probe_eim(query=eim_query):
    """--probe: что отвечает getArcVolume (только чтение)."""
    try:
        text = query()
    except (OSError, subprocess.SubprocessError) as e:
        return ["EIM getArcVolume: unavailable (%s)" % e]
    got = parse_arc_volume(text)
    if got is None:
        return ["EIM getArcVolume: unavailable (%s)" % short_reply(text)]
    return ["EIM getArcVolume: Volume %d%s - --arc-source auto uses it when the CEC bus brings no 50:7A"
            % (got[0], " muted" if got[1] else "")]


class EimPoller(object):
    """Опрос luna://com.webos.service.eim/getArcVolume (идея Nikolay1243 v1.4.0): 150 мс в течение 2,5 с
    после нажатия или изменения, иначе 1 с; нажатие будит опрос сразу.
      auto - только пока CEC-шина не даёт 50:7A: нет /dev/cec*, монитор не открылся или после нажатия
             отчёта нет EIM_BUS_WAIT с (тогда 50:7A из лога не показываются - лог запаздывает);
      eim  - всегда, а 50:7A с шины и из лога не показываются (нажатия, tap guard и 05:71 - как раньше)."""

    def __init__(self, hub, cfg, bus=None, query=eim_query, clock=time.monotonic):
        self.hub, self.cfg, self.bus, self.query, self.clock = hub, cfg, bus, query, clock
        self.wake = threading.Event()
        hub.activity = self.wake
        self.started = None
        self.active = False          # опрос идёт (для лога переключений)
        self.delivering = False      # последний опрос дал значение -> 50:7A из лога не нужен
        self.changed_at = -1e9
        self.last = None
        self.fails, self.error = 0, ""

    def takes_reports(self):
        return self.cfg.arc_source == "eim" or self.delivering

    def wanted(self, now):
        if self.cfg.arc_source == "eim":
            return True
        bus = self.bus
        if bus is None:
            return True
        if now - self.started < EIM_GRACE:
            return False
        if not bus.running:
            return True
        key_at = self.hub.key_at
        return not bus.sees_reports and key_at > self.started and now - key_at >= EIM_BUS_WAIT

    def reason(self):
        if self.cfg.arc_source == "eim":
            return "--arc-source eim"
        if self.bus is None:
            return "no CEC device in use"
        if not self.bus.running:
            return "CEC monitor unavailable"
        return "no 50:7A on the CEC bus after a key press"

    def step(self):
        """Один шаг: опросить, если нужно. Возвращает паузу до следующего шага (с)."""
        now = self.clock()
        if self.started is None:
            self.started = now
        want = self.wanted(now)
        if want != self.active:
            self.active = want
            log("arc source: eim getArcVolume (%s)" % self.reason() if want
                else "arc source: CEC 50:7A (eim polling paused)")
        if not want:
            self.delivering = False
            return EIM_IDLE
        try:
            text = self.query()
            got = parse_arc_volume(text)
            if got is None:
                self.error = short_reply(text)
        except (OSError, subprocess.SubprocessError) as e:
            got, self.error = None, str(e)
        now = self.clock()
        if got is None:
            self.fails += 1
            self.delivering = False
            dbg("eim getArcVolume failed (%d): %s" % (self.fails, self.error))
            if self.fails >= EIM_FAILS:
                self.fails = 0
                raise Unavailable("getArcVolume unavailable (%s)" % self.error)
        else:
            self.fails = 0
            if self.wanted(now):                       # шина могла заговорить, пока шёл запрос
                self.delivering = True
                if got != self.last:
                    self.changed_at = now
                    self.last = got
                self.hub.on_level(got[0], got[1], "eim")
        recent = max(self.hub.key_at, self.changed_at)
        return EIM_FAST if now - recent < EIM_FAST_FOR else EIM_IDLE

    def __call__(self, stop):
        self.started, self.fails = None, 0
        try:
            while not stop.is_set():
                pause = self.step()
                self.wake.wait(pause)
                self.wake.clear()
        finally:
            self.delivering = False      # active остаётся: после повтора через 60 с лог не повторяется


# --- запуск -----------------------------------------------------------------
def run_forever(name, target, stop):
    delay, last = 5.0, None
    while not stop.is_set():
        t0 = time.monotonic()
        try:
            target(stop)
            msg = "%s: stopped" % name
        except Unavailable as e:
            msg, delay = "%s: %s" % (name, e), 60.0
        except Exception as e:                   # noqa: BLE001 - сбой компонента не валит остальные
            msg = "%s: crashed: %r" % (name, e)
        if stop.is_set():
            return
        if time.monotonic() - t0 > 300:
            delay = 5.0
        if msg != last:
            log("%s (retry in %ds)" % (msg, delay))
            last = msg
        stop.wait(delay)
        delay = min(60.0, delay * 2)


def spawn(name, target, stop):
    t = threading.Thread(target=run_forever, args=(name, target, stop), name=name)
    t.daemon = True
    t.start()
    return t


def stamp(line):
    m = STAMP_RE.search(line)
    if not m:
        return None
    y, mo, d, h, mi, s = m.groups()
    return ((int(d) * 24 + int(h)) * 60 + int(mi)) * 60 + float(s)


def replay(args, sink):
    """Прогон сохранённого cecmessages на ПК: python3 cec_volume_bridge.py --replay saved.log"""
    prev = None
    with open(args.replay, "rb") as f:
        for raw in f:
            line = raw.decode("ascii", "replace").rstrip("\r\n")
            t = stamp(line)
            if args.replay_speed > 0 and t is not None and prev is not None and 0 < t - prev < 30:
                time.sleep((t - prev) / args.replay_speed)
            prev = t if t is not None else prev
            sink([line])
    return 0


def already_running(pidfile):
    try:
        with open(pidfile) as f:
            pid = int(f.read().strip())
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return pid != os.getpid() and b"cec_volume_bridge" in f.read()
    except (OSError, ValueError):
        return False


def build_parser():
    # allow_abbrev=False: опечатка/сокращение в args - явная ошибка, а не молчаливая подмена флага
    p = argparse.ArgumentParser(description="Real eARC/ARC volume from HDMI-CEC 0x7A -> overlay app",
                                allow_abbrev=False)
    a = p.add_argument
    a("--app-id", default=DEFAULT_APP_ID, help="eArcNG app id (default: %(default)s)")
    a("--cec-log", default="/var/log/cecmessages")
    a("--cec-dev", default=None, help="CEC device (default: first /dev/cec*)")
    a("--no-cec-device", dest="use_bus", action="store_false", help="log only: no monitor/tap guard/query")
    a("--no-input", dest="use_input", action="store_false", help="do not read remotes in /dev/input")
    a("--no-audio", dest="use_audio", action="store_false", help="no soundOutput gate (no luna subscription)")
    a("--arc-source", choices=ARC_SOURCES, default="auto",
      help="soundbar volume source: cec = 50:7A only; eim = luna eim/getArcVolume only; "
           "auto (default) = 50:7A, getArcVolume while the CEC bus brings no 50:7A")
    a("--outputs", default="", help="allowed soundOutput values, comma separated (default: contains 'arc')")
    a("--any-output", action="store_true", help="show regardless of soundOutput")
    a("--scale", type=float, default=1.0, help="displayed = round(raw*scale)+offset (raw 0-100, bar 0-50: 0.5)")
    a("--max", type=int, default=100, help="displayed maximum / full bar (default 100)")
    a("--offset", type=int, default=0, help="static correction, added to the offset file value")
    a("--offset-file", default=STATE_DIR + "/offset", help="live correction, re-read on every report")
    a("--no-tap-file", default=STATE_DIR + "/no-tap")
    a("--no-tap-guard", dest="tap_guard", action="store_false", help="never inject 05:45 key released")
    a("--tap-grace", type=float, default=0.0, help="min seconds after TV press before injecting release")
    a("--hold", type=float, default=0.35, help="press longer than this = hold (soundbar repeats itself)")
    a("--no-final-query", dest="final_query", action="store_false", help="do not send 05:71 after a burst")
    a("--query-delay", type=float, default=0.6)
    a("--ui", choices=("overlay", "native"), default="overlay",
      help="native: value into the system volume indicator (needs native-osd.sh QML patch, webOS <= 10.x; "
           "overlay is used while the patch is not active)")
    a("--no-prelaunch", dest="prelaunch", action="store_false", help="open overlay only on soundbar reports")
    a("--key-window", type=float, default=3.0)
    a("--visible-ms", type=int, default=1500,
      help="overlay visible time after the last key press / update, 500-10000 (native OSD: 1500)")
    a("--fade-ms", type=int, default=150, help="overlay fade, 0-1000 ms (native OSD: 150)")
    a("--keepalive", type=float, default=0.0,
      help="keep the hidden overlay window alive N s (0-600) so it re-appears faster (experimental)")
    a("--variant", choices=VARIANTS, default=None, help="overlay design (default: badge)")
    a("--position", "--pos", dest="position", choices=POSITIONS, default=None,
      help="overlay position (default: center-right, next to the native ARC indicator)")
    a("--log-file", default="/tmp/cec-volume-bridge.log")
    a("--pidfile", default=None)
    a("--debug", action="store_true")
    a("--dry-run", action="store_true", help="print overlay updates instead of launching the app")
    a("--replay", metavar="FILE", help="replay a saved cecmessages file (implies --dry-run)")
    a("--replay-speed", type=float, default=1.0, help="0 = no delays")
    a("--exit-after", type=float, default=0.0, help="stop after N seconds (earcng.sh try)")
    a("--print-config", action="store_true", help="validate options, print them as JSON and exit")
    a("--probe", action="store_true",
      help="read-only check of /dev/cec* (caps, addresses, modes) and eim getArcVolume, then exit")
    a("--version", action="version", version=VERSION)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.scale <= 0 or args.max <= 0:
        build_parser().error("--scale and --max must be positive")
    if not (500 <= args.visible_ms <= 10000 and 0 <= args.fade_ms <= 1000 and 0 <= args.keepalive <= 600):
        build_parser().error("--visible-ms 500-10000, --fade-ms 0-1000, --keepalive 0-600")
    if args.print_config:
        print(json.dumps(vars(args), sort_keys=True))
        return 0
    if args.probe:
        print("\n".join(probe_cec([args.cec_dev] if args.cec_dev else None)))
        print("\n".join(probe_eim()))
        return 0
    if args.replay:
        args.dry_run, args.log_file = True, None
    _LOG.update(path=args.log_file, debug=args.debug, stderr=args.dry_run)
    extra = {"ttl": args.visible_ms, "fade": args.fade_ms}
    if args.keepalive > 0:
        extra["keep"] = int(args.keepalive * 1000)
    if args.variant:
        extra["variant"] = args.variant
    if args.position:
        extra["pos"] = args.position
    native = NativeSink(dry_run=args.dry_run) if args.ui == "native" else None
    hub = Hub(Publisher(args.app_id, extra, dry_run=args.dry_run), args, native=native)
    if args.replay:
        return replay(args, LineSink(hub))
    if args.pidfile and already_running(args.pidfile):
        log("already running, exit")
        return 0
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.set())
    if args.pidfile:
        with open(args.pidfile, "w") as f:
            f.write("%d\n" % os.getpid())
    log("cec_volume_bridge %s start (pid %d, python %s, app %s)"
        % (VERSION, os.getpid(), sys.version.split()[0], args.app_id))
    log("config: ui=%s scale=%g max=%d offset=%+d variant=%s position=%s visible=%dms fade=%dms keepalive=%gs "
        "outputs=%s tap-guard=%s arc-source=%s%s"
        % (args.ui, args.scale, args.max, args.offset, args.variant or "badge", args.position or "center-right",
           args.visible_ms, args.fade_ms, args.keepalive,
           "any" if args.any_output else (args.outputs or "*arc*"), "on" if args.tap_guard else "off",
           args.arc_source, " DRY-RUN" if args.dry_run else ""))
    if native is not None and not native.refresh():       # refresh() сам пишет "native OSD patch active"
        log("ui native: QML patch not active (yet) - overlay until native-osd.sh apply succeeds")
    keys, bus, eim = KeyState(), None, None
    if args.use_bus:
        bus = CecBus(args, hub, keys)
        spawn("cec-bus", bus, stop)
    if args.arc_source != "cec":
        eim = EimPoller(hub, args, bus)
        spawn("arc-eim", eim, stop)
    if args.use_input:
        spawn("remotes", InputWatcher(hub, keys), stop)
    if args.use_audio:
        spawn("audio", AudioSubscriber(hub), stop)
    spawn("cec-log", LogFollower(args.cec_log, LineSink(hub, bus, eim)), stop)
    deadline = time.monotonic() + args.exit_after if args.exit_after > 0 else None
    try:
        while not stop.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                log("exit-after %gs reached" % args.exit_after)
                break
            stop.wait(0.2 if deadline is not None else 1.0)
    finally:
        log("stop")
        if args.pidfile and not already_running(args.pidfile):
            try:
                os.remove(args.pidfile)
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
