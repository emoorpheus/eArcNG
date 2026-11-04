#!/usr/bin/env python3
"""Юнит- и интеграционные тесты cec_volume_bridge.py (запуск: python3 -m unittest -v tests/test_bridge.py)."""
import argparse
import errno
import io
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
BRIDGE = os.path.join(HERE, "..", "backend", "cec_volume_bridge.py")
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
import cec_volume_bridge as b  # noqa: E402


def cfg(**kw):
    ns = b.build_parser().parse_args([])
    ns.offset_file = kw.pop("offset_file", "/nonexistent/offset")
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


class Clock(object):
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make_hub(native=None, **kw):
    out, clock = [], Clock()
    hub = b.Hub(out.append, cfg(**kw), clock=clock, wall=lambda: clock.t + 1.7e9, native=native)
    return hub, out, clock


class FakeNative(object):
    """Подмена NativeSink для логики Hub."""
    def __init__(self, active=True):
        self.on, self.pushes, self.feedbacks, self.states = active, [], [], []

    def active(self):
        return self.on

    def push(self, value, forced, why):
        self.pushes.append((value, forced, why))

    def feedback(self, now, idle, what):
        self.feedbacks.append((what, idle))

    def tv_state(self, volume, mute):
        self.states.append((volume, mute))


FIXTURE = os.path.join(HERE, "fixtures", "overlay-telemetry-m59la.log")
EV_RE = re.compile(r"(\d\d):(\d\d):(\d\d\.\d+) debug: (?:key (up|down|mute) \((remote|cec)\)|"
                   r"report raw=(\d+) mute=(True|False))")


def telemetry():
    """Реальная последовательность с ТВ пользователя: [(t, 'key', kind, source) | (t, 'report', status, 'cec')]."""
    events = []
    with open(FIXTURE) as f:
        for line in f:
            m = EV_RE.search(line)
            if not m:
                continue
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            if m.group(4):
                events.append((t, "key", m.group(4), m.group(5)))
            else:
                events.append((t, "report", int(m.group(6)) | (0x80 if m.group(7) == "True" else 0), "cec"))
    return events


def ioc(direction, typ, nr, size):
    return (direction << 30) | (size << 16) | (ord(typ) << 8) | nr


class Constants(unittest.TestCase):
    def test_kernel_abi(self):
        self.assertEqual(b.CEC_MSG.size, 56)                       # sizeof(struct cec_msg)
        self.assertEqual(b.CEC_TRANSMIT, ioc(3, "a", 5, 56))
        self.assertEqual(b.CEC_RECEIVE, ioc(3, "a", 6, 56))
        self.assertEqual(b.CEC_S_MODE, ioc(1, "a", 9, 4))
        self.assertEqual(b.EVIOCGBIT_KEY, ioc(2, "E", 0x20 + b.EV_KEY, 96))
        self.assertEqual(b.INPUT_EVENT.size, 2 * struct.calcsize("l") + 8)
        self.assertEqual(b.CEC_CAPS.size, 76)                      # sizeof(struct cec_caps)
        self.assertEqual(b.CEC_ADAP_G_CAPS, ioc(3, "a", 0, 76))
        self.assertEqual(b.CEC_ADAP_G_PHYS_ADDR, ioc(2, "a", 1, 2))
        self.assertEqual(b.CEC_ADAP_G_LOG_ADDRS, ioc(2, "a", 3, b.CEC_LOG_ADDRS_SIZE))

    def test_cec_msg_pack(self):
        raw = b.CEC_MSG.pack(0, 0, 2, 0, 0, 0, b.FRAME_RELEASE, 0, 0, 0, 0, 0, 0, 0)
        msg = b.CEC_MSG.unpack(raw)
        self.assertEqual(msg[6][:msg[2]], b"\x05\x45")


class Parsing(unittest.TestCase):
    def test_frames(self):
        line = "2026-10-27T22:10:15.284413Z [CECMSG] TM182747 RX507A0C"
        self.assertEqual(b.parse_frames(line), [("RX", b"\x50\x7a\x0c")])
        self.assertEqual(b.classify(b"\x50\x7a\x0c"), ("report", 0x0C))
        self.assertEqual(b.classify(b"\x5f\x7a\x1e"), ("report", 0x1E))
        self.assertEqual(b.classify(bytes.fromhex("054441")), ("key", "up"))
        self.assertEqual(b.classify(bytes.fromhex("054442")), ("key", "down"))
        self.assertEqual(b.classify(bytes.fromhex("054443")), ("key", "mute"))
        self.assertEqual(b.classify(bytes.fromhex("0545")), ("release",))
        self.assertIsNone(b.classify(bytes.fromhex("044f")))
        self.assertIsNone(b.classify(bytes.fromhex("407a0c")))     # 7A не от аудиосистемы
        self.assertEqual(b.parse_frames("x TXDONE TM12 RX5"), [])
        # формат строк из отчёта с ТВ (без TM), mute 22 = 0x96
        self.assertEqual(b.parse_frames("2026-11-01T17:15:11Z [CECMSG] RX507A10"), [("RX", b"\x50\x7a\x10")])
        self.assertEqual(b.decode_status(b.classify(b"\x50\x7a\x96")[1]), (True, 22))

    def test_status(self):
        self.assertEqual(b.decode_status(0x0C), (False, 12))
        self.assertEqual(b.decode_status(0x8C), (True, 12))
        self.assertEqual(b.decode_status(0x64), (False, 100))
        self.assertEqual(b.decode_status(0x65), (False, None))
        self.assertEqual(b.decode_status(0x7F), (False, None))
        self.assertEqual(b.decode_status(0xFF), (True, None))

    def test_output_gate(self):
        c = cfg()
        self.assertTrue(b.output_allowed("external_arc", c))
        self.assertFalse(b.output_allowed("tv_speaker", c))
        self.assertTrue(b.output_allowed(None, c))
        self.assertTrue(b.output_allowed("tv_speaker", cfg(any_output=True)))
        self.assertTrue(b.output_allowed("external_optical", cfg(outputs="external_arc,external_optical")))
        self.assertFalse(b.output_allowed("external_arc", cfg(outputs="external_optical")))


class HubLogic(unittest.TestCase):
    def test_baseline_silent_then_key_report(self):
        hub, out, clock = make_hub()
        hub.on_report(0x14, "log")
        self.assertEqual(out, [], "первый отчёт без нажатия - тихая базовая точка")
        hub.on_key("up", "cec")
        self.assertEqual([o["reason"] for o in out], ["key"], "окно открывается сразу с последним реальным")
        self.assertEqual(out[-1]["volume"], 20)
        clock.advance(0.15)
        hub.on_report(0x15, "cec")
        self.assertEqual((out[-1]["volume"], out[-1]["reason"]), (21, "report"))

    def test_same_value(self):
        hub, out, clock = make_hub()
        hub.on_report(0x14, "log")
        hub.on_report(0x14, "log")
        self.assertEqual(out, [])
        clock.advance(5)
        hub.on_key("down", "cec")          # prelaunch
        hub.on_report(0x14, "cec")         # саундбар не изменил значение - показать ещё раз
        self.assertEqual([o["reason"] for o in out], ["key", "repeat"])
        clock.advance(10)
        hub.on_report(0x14, "cec")         # периодический отчёт без нажатия
        self.assertEqual(len(out), 2)

    def test_external_change_and_mute(self):
        hub, out, clock = make_hub()
        hub.on_report(0x14, "log")
        hub.on_report(0x1E, "log")         # пульт саундбара / приложение
        self.assertEqual((out[-1]["volume"], out[-1]["mute"]), (30, False))
        hub.on_key("mute", "cec")          # mute: без раннего показа (иначе мигнёт старое состояние)
        self.assertEqual(len(out), 1)
        hub.on_report(0x9E, "cec")
        self.assertEqual((out[-1]["volume"], out[-1]["mute"]), (30, True))
        hub.on_report(0x7F, "cec")         # громкость неизвестна - игнор
        self.assertEqual(len(out), 2)

    def test_limits(self):
        hub, out, clock = make_hub(prelaunch=False)
        hub.on_report(0x64, "log")
        hub.on_key("up", "cec")
        hub.on_key("up", "remote")         # тот же клик из другого источника - троттлинг 300 мс
        self.assertEqual([o["reason"] for o in out], ["limit"])
        clock.advance(0.35)
        hub.on_key("up", "cec")
        self.assertEqual(len(out), 2)
        hub.on_key("down", "cec")          # от 100 вниз - не граница, без prelaunch ничего
        self.assertEqual(len(out), 2)

    def test_prelaunch_only_when_hidden(self):
        hub, out, clock = make_hub(visible_ms=2000)
        hub.on_report(0x14, "log")
        hub.on_key("up", "remote")
        clock.advance(0.5)
        hub.on_key("up", "cec")
        self.assertEqual(len(out), 1, "пока окно видно - повторно не открывать")
        clock.advance(2.1)
        hub.on_key("up", "cec")
        self.assertEqual(len(out), 2)

    def test_output_gate_and_baseline_reset(self):
        hub, out, clock = make_hub()
        hub.set_output("external_arc")
        hub.on_report(0x14, "log")
        hub.set_output("tv_speaker")
        hub.on_report(0x30, "log")
        hub.on_key("up", "cec")
        self.assertEqual(out, [])
        hub.set_output("external_arc")
        clock.advance(10)
        hub.on_report(0x31, "log")         # после переключения на ARC - снова тихая базовая точка
        self.assertEqual(out, [])
        hub.on_report(0x32, "log")
        self.assertEqual(out[-1]["volume"], 50)

    def test_scale_offset(self):
        with tempfile.TemporaryDirectory() as d:
            off = os.path.join(d, "offset")
            hub, out, clock = make_hub(scale=0.5, max=50, offset_file=off)
            self.assertEqual(hub.display(51), 26)
            with open(off, "w") as f:
                f.write("-2\n")
            self.assertEqual(hub.display(51), 24)
            self.assertEqual(hub.display(2), 0)
            self.assertEqual(hub.display(100), 48)
            with open(off, "w") as f:
                f.write("+5")
            self.assertEqual(hub.display(100), 50, "ограничение сверху --max")


class Sink(unittest.TestCase):
    class Rec(object):
        def __init__(self):
            self.calls, self.frames = [], []

        def on_report(self, s, src):
            self.calls.append(("report", s))

        def on_key(self, k, src):
            self.calls.append(("key", k))

        def tv_frame(self, ev, now, idle):
            self.frames.append((ev, idle))

    def test_newest_report_per_batch_and_order(self):
        rec = self.Rec()
        sink = b.LineSink(rec)
        sink(["a RX507A14", "b TX054441", "c RX507A15", "d RX507A16", "e TX054442"])
        self.assertEqual(rec.calls, [("key", "up"), ("report", 0x16), ("key", "down")])

    def test_bus_takes_over(self):
        class Bus(object):
            sees_reports, sees_keys = True, False
        rec = self.Rec()
        b.LineSink(rec, Bus())(["RX507A14", "TX054441"])
        self.assertEqual(rec.calls, [("key", "up")])


class Guard(unittest.TestCase):
    def test_tap_release_and_final_query(self):
        g = b.TapGuard(cfg())
        g.on_event(("key", "up"), 10.0)
        self.assertEqual(g.tick(10.0, True, 9.95, True), [], "кнопка ещё нажата")
        self.assertEqual(g.tick(10.05, False, 9.95, True), [b.FRAME_RELEASE], "отпущена -> 05:45")
        self.assertEqual(g.tick(10.1, False, 9.95, True), [])
        self.assertEqual(g.tick(10.61, False, 9.95, True), [b.FRAME_GIVE_AUDIO], "через 0.6 с -> 05:71")
        self.assertEqual(g.tick(11.0, False, 9.95, True), [])

    def test_hold_and_disabled(self):
        g = b.TapGuard(cfg(final_query=False))
        g.on_event(("key", "up"), 10.0)
        self.assertEqual(g.tick(10.40, True, 10.0, True), [], "удержание > 0.35 с")
        self.assertFalse(g.open_press)
        self.assertEqual(g.tick(10.5, False, 10.0, True), [], "после удержания не вмешиваемся")
        g.on_event(("key", "down"), 11.0)
        self.assertEqual(g.tick(11.01, False, 10.9, False), [], "нет пульта/ no-tap - не шлём")
        g.on_event(("key", "up"), 12.0)
        g.on_event(("release",), 12.01)              # ТВ сам прислал 05:45
        self.assertEqual(g.tick(12.02, False, 11.9, True), [])
        g.on_event(("key", "mute"), 13.0)
        self.assertFalse(g.open_press)


class BusLoop(unittest.TestCase):
    """CecBus.loop с подменой ioctl: монитор (pipe как fd) -> Hub, tap guard и 05:71 через CEC_TRANSMIT."""

    def test_loop(self):
        r, w = os.pipe()
        queue, sent, lock = [], [], threading.Lock()

        def fake_ioctl(fd, req, buf, *a):
            if req == b.CEC_RECEIVE:
                with lock:
                    if not queue:
                        raise OSError(b.errno.EAGAIN, "empty")
                    frame = queue.pop(0)
                os.read(r, 1)
                buf[:] = b.CEC_MSG.pack(0, 0, len(frame), 0, 0, 0, frame, 0, 0, 0, 0, 0, 0, 0)
                return 0
            if req == b.CEC_TRANSMIT:
                msg = b.CEC_MSG.unpack(bytes(buf))
                sent.append((round(time.monotonic() - t0, 2), msg[6][:msg[2]]))
                return 0
            raise AssertionError(hex(req))

        def push(hexframe):
            with lock:
                queue.append(bytes.fromhex(hexframe))
            os.write(w, b"x")

        rec = Sink.Rec()
        keys = b.KeyState()
        keys.devices = 1
        bus = b.CecBus(cfg(no_tap_file="/nonexistent"), rec, keys)
        orig, b.fcntl.ioctl = b.fcntl.ioctl, fake_ioctl
        stop = threading.Event()
        t0 = time.monotonic()
        th = threading.Thread(target=bus.loop, args=(stop, r, 99))
        th.daemon = True
        try:
            th.start()
            time.sleep(0.1)
            keys.set(True)                          # кнопка нажата
            push("054441")                          # ТВ -> саундбар: "нажато"
            time.sleep(0.15)
            self.assertEqual([f for _, f in sent], [b.FRAME_GIVE_AUDIO], "пока держим - 05:45 не шлём")
            keys.set(False)                         # отпустили (тап)
            time.sleep(0.1)
            push("507A15")                          # отчёт саундбара
            time.sleep(0.8)
        finally:
            stop.set()
            th.join(2)
            b.fcntl.ioctl = orig
            os.close(r)
            os.close(w)
        frames = [f for _, f in sent]
        self.assertEqual(frames, [b.FRAME_GIVE_AUDIO, b.FRAME_RELEASE, b.FRAME_GIVE_AUDIO])
        self.assertLess(sent[1][0] - 0.25, 0.08, "05:45 сразу после отпускания: %r" % sent)
        self.assertEqual(rec.calls, [("key", "up"), ("report", 0x15)])
        self.assertEqual(rec.frames, [(("key", "up"), 0.0)], "кадр громкости ТВ -> защита native (кнопка нажата)")
        self.assertTrue(bus.sees_keys and bus.sees_reports)


class Audio(unittest.TestCase):
    class Rec(object):
        def __init__(self):
            self.outputs, self.tv = [], []

        def set_output(self, name):
            self.outputs.append(name)

        def set_tv_state(self, volume, mute):
            self.tv.append((volume, mute))

        def forget_tv_state(self):
            self.tv.append("forgotten")

    def test_handle_webos11_payload(self):
        rec = self.Rec()
        a = b.AudioSubscriber(rec)
        # реальный формат из ассетов Flutter systemui webOS 11 (issue #1 trk501)
        a.handle(b'{"returnValue":true,"volumeStatus":{"volumeLimitable":true,"activeStatus":true,"maxVolume":100,'
                 b'"soundOutput":"tv_external_speaker","volume":17,"muteStatus":false,"volumeSyncable":false}}\r')
        a.handle(b'junk {"soundOutput":"external_arc","volume":3}')
        a.handle(b'{"returnValue":false,"errorText":"x"}')
        a.handle(b"not json")
        self.assertEqual(rec.outputs, ["tv_external_speaker", "external_arc"])
        self.assertEqual(rec.tv, [(17, False), (3, None)], "volume/muteStatus для --ui native")

    def test_pty_subscription_with_fake_luna_send(self):
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "luna-send")
            with open(fake, "w") as f:
                f.write("#!/bin/sh\n"
                        "[ -t 1 ] || { echo NOTTY; exit 1; }\n"
                        "echo '{\"returnValue\":true,\"volumeStatus\":{\"soundOutput\":\"external_arc\"}}'\n"
                        "sleep 0.2\n"
                        "echo '{\"returnValue\":true,\"volumeStatus\":{\"soundOutput\":\"tv_speaker\"}}'\n")
            os.chmod(fake, 0o755)
            old_path = os.environ["PATH"]
            os.environ["PATH"] = d + os.pathsep + old_path
            try:
                rec = self.Rec()
                b.AudioSubscriber(rec)(threading.Event())
            finally:
                os.environ["PATH"] = old_path
        self.assertEqual(rec.outputs, ["external_arc", "tv_speaker", None], "вывод через PTY (luna-send видит терминал)")
        self.assertEqual(rec.tv, ["forgotten"], "подписка оборвалась - состояние ТВ сброшено (native -> оверлей)")


class Inputs(unittest.TestCase):
    def test_evdev_events(self):
        rec = Sink.Rec()
        keys = b.KeyState()
        w = b.InputWatcher(rec, keys)
        ev = lambda code, val: b.INPUT_EVENT.pack(0, 0, b.EV_KEY, code, val)  # noqa: E731
        w.events(ev(b.KEY_VOLUMEUP, 1) + b.INPUT_EVENT.pack(0, 0, 0, 0, 0))
        self.assertTrue(keys.snapshot()[0])
        w.events(ev(b.KEY_VOLUMEUP, 2))                 # автоповтор не считается новым нажатием
        w.events(ev(b.KEY_VOLUMEUP, 0) + ev(b.KEY_MUTE, 1))
        self.assertFalse(keys.snapshot()[0])
        self.assertEqual(rec.calls, [("key", "up"), ("key", "mute")])


class Publish(unittest.TestCase):
    def test_dry_run_params(self):
        b._LOG["path"] = None
        p = b.Publisher("app", {"ttl": 2000, "variant": "ring"}, dry_run=True)
        buf = io.StringIO()
        with redirect_stdout(buf):
            p({"volume": 21, "mute": False, "raw": 21, "max": 100, "reason": "report"})
            p({"volume": 22, "mute": False, "raw": 22, "max": 100, "reason": "report"})
        msgs = [json.loads(l.split(" ", 1)[1]) for l in buf.getvalue().splitlines()]
        self.assertEqual([m["seq"] for m in msgs], [1, 2])
        self.assertEqual(msgs[0]["sid"], msgs[1]["sid"])
        self.assertEqual((msgs[1]["src"], msgs[1]["ttl"], msgs[1]["variant"]), ("cec", 2000, "ring"))


class PublishLive(unittest.TestCase):
    def test_latest_wins_single_in_flight(self):
        with tempfile.TemporaryDirectory() as d:
            fake, rec = os.path.join(d, "luna-send"), os.path.join(d, "calls")
            with open(fake, "w") as f:
                f.write('#!/bin/sh\nprintf "%%s\\n" "$4" >> "%s"\nsleep 0.3\necho \'{"returnValue":true}\'\n' % rec)
            os.chmod(fake, 0o755)
            p = b.Publisher("com.test.app", {"ttl": 2000})
            p.luna = fake
            for v in range(21, 26):                 # 5 быстрых обновлений
                p({"volume": v, "mute": False, "raw": v, "max": 100, "reason": "report"})
                time.sleep(0.02)
            time.sleep(1.0)
            with open(rec) as f:
                calls = [json.loads(line) for line in f]
        self.assertEqual([c["id"] for c in calls], ["com.test.app"] * 2)
        self.assertEqual([c["params"]["volume"] for c in calls], [21, 25], "в полёте один запрос, затем последнее")
        self.assertEqual(calls[1]["params"]["seq"], 5)


class Follower(unittest.TestCase):
    def test_follow_rotation_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cecmessages")
            with open(path, "w") as f:
                f.write("old RX507A01\n")                 # история до старта - пропускается
            got, ev = [], threading.Event()

            def sink(lines):
                got.extend(lines)
                ev.set()
            stop = threading.Event()
            t = threading.Thread(target=b.LogFollower(path, sink, poll=0.2), args=(stop,))
            t.daemon = True
            t.start()
            time.sleep(0.3)

            def append(text):
                ev.clear()
                with open(path, "a") as f:
                    f.write(text)
                self.assertTrue(ev.wait(2), "нет реакции на " + text)

            t0 = time.monotonic()
            append("n1 RX507A02\n")
            latency = time.monotonic() - t0
            ev.clear()
            with open(path, "a") as f:
                f.write("partial RX5")                    # неполная строка ждёт \n
            self.assertFalse(ev.wait(0.4), "неполная строка не должна уходить в разбор")
            append("07A03\n")
            os.rename(path, path + ".1")                  # ротация
            with open(path, "w") as f:
                f.write("r1 RX507A04\n")
            deadline = time.monotonic() + 3
            while "r1 RX507A04" not in got and time.monotonic() < deadline:
                time.sleep(0.05)
            with open(path, "w") as f:                    # усечение (copytruncate)
                f.write("t1\n")
            deadline = time.monotonic() + 3
            while "t1" not in got and time.monotonic() < deadline:
                time.sleep(0.05)
            stop.set()
            t.join(2)
            self.assertNotIn("old RX507A01", got)
            self.assertEqual(got, ["n1 RX507A02", "partial RX507A03", "r1 RX507A04", "t1"])
            self.assertLess(latency, 0.15, "inotify должен будить сразу (%.3f с)" % latency)


class Options(unittest.TestCase):
    def parse(self, *argv):
        return b.build_parser().parse_args(list(argv))

    def test_offset_position_alias(self):
        ns = self.parse("--scale", "1.0", "--max", "100", "--offset", "-2", "--pos", "center-right", "--variant", "pill")
        self.assertEqual((ns.offset, ns.position, ns.variant), (-2, "center-right", "pill"))
        with tempfile.NamedTemporaryFile("w", suffix=".offset", delete=False) as f:
            f.write("1\n")
        try:
            hub = b.Hub(lambda p: None, cfg(offset=-2, offset_file=f.name))
            self.assertEqual(hub.display(30), 29)                   # 30 - 2 (args) + 1 (файл)
            self.assertEqual(hub.display(0), 0)
        finally:
            os.remove(f.name)

    def test_strict_flags(self):
        err = io.StringIO()
        for argv in (["--off", "1"], ["--position", "middle"], ["--offset", "x"]):
            with self.assertRaises(SystemExit), redirect_stderr(err):
                self.parse(*argv)
        self.assertIn("unrecognized arguments: --off", err.getvalue())

    def test_cli_print_config_probe_exit_after(self):
        r = subprocess.run([sys.executable, BRIDGE, "--print-config", "--pos", "center-left", "--offset", "3"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        conf = json.loads(r.stdout.decode())
        self.assertEqual((conf["position"], conf["offset"]), ("center-left", 3))
        r = subprocess.run([sys.executable, BRIDGE, "--scale", "0", "--print-config"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run([sys.executable, BRIDGE, "--probe", "--cec-dev", "/dev/null"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertIn("not a Linux CEC adapter", r.stdout.decode())
        with tempfile.TemporaryDirectory() as d:
            t0 = time.monotonic()
            r = subprocess.run([sys.executable, BRIDGE, "--dry-run", "--no-cec-device", "--no-input", "--no-audio",
                                "--cec-log", os.path.join(d, "cecmessages"), "--exit-after", "0.5"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            self.assertEqual(r.returncode, 0, r.stderr.decode())
            self.assertLess(time.monotonic() - t0, 5)
            self.assertIn("exit-after 0.5s reached", r.stderr.decode())
            self.assertRegex(r.stderr.decode(), r"\d\d:\d\d:\d\d\.\d{3} config: ui=overlay scale=1 max=100 offset=\+0 "
                                                r"variant=badge position=center-right visible=1500ms fade=150ms")


class Probe(unittest.TestCase):
    def fake(self, mode_errors=None, caps_error=None, addrs=(0,), cap=0x7 | 0x20):
        calls = []

        def ioctl(fd, req, arg):
            calls.append(req)
            if caps_error and req == b.CEC_ADAP_G_CAPS:
                raise OSError(caps_error, os.strerror(caps_error))
            if req == b.CEC_ADAP_G_CAPS:
                arg[:] = b.CEC_CAPS.pack(b"lgcec", b"HDMI-CEC", 1, cap, 0x060600)
            elif req == b.CEC_ADAP_G_LOG_ADDRS:
                arg[:len(addrs)] = bytearray(addrs)
                arg[7] = len(addrs)
            elif req == b.CEC_ADAP_G_PHYS_ADDR:
                arg[:] = struct.pack("=H", 0x0000)
            elif req == b.CEC_S_MODE:
                mode = struct.unpack("I", arg)[0]
                if mode_errors and mode in mode_errors:
                    raise OSError(mode_errors[mode], os.strerror(mode_errors[mode]))
            else:
                raise AssertionError("unexpected ioctl 0x%X" % req)
            return 0
        opened = []
        lines = b.probe_cec(["/dev/cec0"], ioctl=ioctl, opener=lambda *a: opened.append(1) or 7,
                            closer=lambda fd: opened.pop())
        self.assertEqual(opened, [], "every fd closed")
        self.assertNotIn(b.CEC_TRANSMIT, calls, "probe never transmits")
        return "\n".join(lines)

    def test_full(self):
        out = self.fake()
        self.assertIn("Linux CEC API ok, driver=lgcec name=HDMI-CEC caps=PHYS_ADDR|LOG_ADDRS|TRANSMIT|MONITOR_ALL", out)
        self.assertIn("logical addresses [0], physical 0.0.0.0", out)
        self.assertIn("monitor: ok, initiator: ok -> tap guard + 05:71 available", out)

    def test_degraded(self):
        out = self.fake(mode_errors={b.CEC_MODE_INITIATOR: errno.EBUSY})
        self.assertIn("initiator: [Errno %d]" % errno.EBUSY, out)
        self.assertIn("-> real-time monitor only", out)
        self.assertIn("-> log-only mode", self.fake(mode_errors={b.CEC_MODE_MONITOR: errno.EPERM,
                                                               b.CEC_MODE_INITIATOR: errno.EBUSY}))
        self.assertIn("-> real-time monitor only", self.fake(addrs=()))     # адрес ТВ 0 не занят
        self.assertIn("not a Linux CEC adapter", self.fake(caps_error=errno.ENOTTY))
        self.assertEqual(b.probe_cec([]), ["CEC: no /dev/cec* - log-only mode, tap guard off"])


class Cli(unittest.TestCase):
    def test_replay_fixture(self):
        r = subprocess.run([sys.executable, BRIDGE, "--replay", os.path.join(HERE, "fixtures", "cecmessages-sample.log"),
                            "--replay-speed", "0", "--offset-file", "/nonexistent"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        msgs = [json.loads(l.split(" ", 1)[1]) for l in r.stdout.decode().splitlines() if l.startswith("PUBLISH ")]
        seen = [(m["volume"], m["mute"], m["reason"]) for m in msgs]
        self.assertEqual(seen, [
            (20, False, "key"),      # нажатие: окно сразу с последним реальным значением
            (21, False, "report"),
            (22, False, "report"),
            (22, True, "report"),    # mute от саундбара
            (22, False, "report"),
            (30, False, "report"),   # изменение с пульта саундбара
            (100, False, "report"),
            (100, False, "limit"),   # нажатие на 100
            (100, False, "repeat"),
        ])


class KeyTiming(unittest.TestCase):
    """Оверлей гаснет через ttl после последнего нажатия (kt), как нативный индикатор."""

    def test_kt_from_remote_press_and_hold_repeats(self):
        hub, out, clock = make_hub()
        hub.on_report(0x14, "log")
        t_press = clock.t
        hub.on_key("up", "remote")                       # prelaunch с kt = время нажатия
        self.assertEqual((out[-1]["reason"], out[-1]["kt"]), ("key", int((t_press + 1.7e9) * 1000)))
        clock.advance(0.1)
        hub.on_key("up", "cec")                          # CEC-эхо нажатия: время не сдвигается
        clock.advance(0.16)
        hub.on_report(0x15, "cec")
        self.assertEqual(out[-1]["kt"], int((t_press + 1.7e9) * 1000))
        clock.advance(0.5)
        hub.on_key("up", "cec")                          # повтор удержания: время ТВ = CEC - 0.1 с
        t_rep = clock.t
        clock.advance(0.16)
        hub.on_report(0x16, "cec")
        self.assertEqual(out[-1]["kt"], int((t_rep - b.KEY_CEC_LAG + 1.7e9) * 1000))

    def test_external_change_has_no_kt_and_prelaunch_before_hide(self):
        hub, out, clock = make_hub()
        hub.on_report(0x14, "log")
        clock.advance(10)
        hub.on_report(0x1E, "log")                       # приложение Sony: без нажатия - ttl от обновления
        self.assertNotIn("kt", out[-1])
        clock.advance(5)
        hub.on_key("up", "remote")
        clock.advance(0.26)
        hub.on_report(0x1F, "cec")
        n = len(out)
        clock.advance(0.9)                               # окно погаснет через 1.5 с после нажатия ...
        hub.on_key("up", "remote")                       # ... новое нажатие за 0.34 с до этого - открыть сразу
        self.assertEqual((len(out), out[-1]["reason"]), (n + 1, "key"))
        clock.advance(0.2)
        hub.on_key("up", "remote")                       # окно точно видно - не дёргать
        self.assertEqual(len(out), n + 1)


class NativeHub(unittest.TestCase):
    def test_native_routes_reports_to_tv_value(self):
        nat = FakeNative()
        hub, out, clock = make_hub(native=nat)
        hub.on_report(0x14, "cec")
        self.assertEqual(nat.pushes, [(20, False, "start")], "старт: только если у ТВ другое значение")
        hub.on_key("up", "remote")
        hub.on_key("up", "cec")
        self.assertEqual(out, [], "нативный индикатор появляется сам - оверлей не запускается")
        clock.advance(0.26)
        hub.on_report(0x15, "cec")
        hub.on_key("up", "cec")
        clock.advance(0.2)
        hub.on_report(0x15, "cec")                       # то же значение после нажатия - всё равно setVolume
        clock.advance(5)
        hub.on_report(0x1E, "cec")                       # приложение Sony
        hub.on_report(0x9E, "cec")                       # mute саундбара - не трогать
        self.assertEqual(nat.pushes[1:], [(21, True, "key"), (21, True, "key"), (30, False, "external")])
        self.assertEqual(out, [])

    def test_fallback_to_overlay_and_guard_frames(self):
        nat = FakeNative(active=False)
        hub, out, clock = make_hub(native=nat)
        hub.on_report(0x14, "cec")
        hub.on_key("up", "remote")
        self.assertEqual([o["reason"] for o in out], ["key"], "патч не активен -> обычный оверлей")
        self.assertEqual(nat.pushes, [])
        hub.tv_frame(("key", "up"), clock.t, 5.0)
        self.assertEqual(nat.feedbacks, [], "без патча защита не нужна")
        nat.on = True
        hub.tv_frame(("key", "up"), clock.t, 5.0)
        hub.tv_frame(("key", "mute"), clock.t, 5.0)
        hub.tv_frame(("level", 0x14), clock.t, 5.0)      # тот же уровень, что у саундбара - безвредно
        hub.tv_frame(("level", 0x20), clock.t, 5.0)
        self.assertEqual(nat.feedbacks, [("05:44 up", 5.0), ("05:73:20", 5.0)])
        hub.set_tv_state(20, False)
        self.assertEqual(nat.states, [(20, False)])


class NativeHubWithSink(unittest.TestCase):
    def test_tv_state_known_later_mute_and_lost(self):
        with tempfile.TemporaryDirectory() as d:
            qml, mounts = os.path.join(d, "StarfishVolume.qml"), os.path.join(d, "mounts")
            with open(qml, "w") as f:
                f.write("    property int shownVolume: LS.audioService.volume\n")
            with open(mounts, "w") as f:
                f.write("tmpfs %s tmpfs rw 0 0\n" % qml)
            os.environ["CEC_QML_FILE"], os.environ["CEC_MOUNTS"] = qml, mounts
            try:
                b._LOG["path"] = None
                hub, out, clock = make_hub(native=b.NativeSink(dry_run=True))
                buf = io.StringIO()
                with redirect_stdout(buf):
                    hub.on_report(0x14, "cec")              # getVolume ещё не ответил
                    hub.set_tv_state(17, False)              # -> базовая точка на ТВ
                    hub.on_key("up", "remote")
                    clock.advance(0.25)
                    hub.on_report(0x15, "cec")
                    hub.set_tv_state(None, True)             # mute ТВ
                    hub.on_key("up", "remote")
                    clock.advance(0.25)
                    hub.on_report(0x16, "cec")
                    hub.set_tv_state(23, False)              # unmute (ТВ насчитал своё)
                    hub.forget_tv_state()
                    clock.advance(3)
                    hub.on_key("up", "remote")               # подписка оборвалась -> снова оверлей
            finally:
                del os.environ["CEC_QML_FILE"], os.environ["CEC_MOUNTS"]
        lines = [l for l in buf.getvalue().splitlines() if l.startswith("NATIVE")]
        self.assertEqual(lines, ["NATIVE setVolume 20 (start)", "NATIVE setVolume 21 (key)",
                                 "NATIVE setVolume 22 (unmute)"])
        self.assertEqual([(o["reason"], o["volume"]) for o in out], [("key", 22)], "без состояния ТВ - оверлей")


class NativeSinkLogic(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.qml = os.path.join(self.d.name, "StarfishVolume.qml")
        self.mounts = os.path.join(self.d.name, "mounts")
        with open(self.qml, "w") as f:
            f.write("Item {\n    id: root\n    property int shownVolume: LS.audioService.volume\n}\n")
        with open(self.mounts, "w") as f:
            f.write("/dev/root / squashfs ro 0 0\n")
        os.environ["CEC_QML_FILE"], os.environ["CEC_MOUNTS"] = self.qml, self.mounts
        b._LOG["path"] = None
        self.clock = Clock()
        self.n = b.NativeSink(dry_run=True, clock=self.clock)

    def tearDown(self):
        del os.environ["CEC_QML_FILE"], os.environ["CEC_MOUNTS"]
        self.d.cleanup()

    def sets(self, fn):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fn()
        return [l for l in buf.getvalue().splitlines() if l.startswith("NATIVE")]

    def test_patch_detection(self):
        self.n.tv_state(20, False)
        self.assertFalse(self.n.active())
        with open(self.mounts, "a") as f:
            f.write("tmpfs %s tmpfs rw 0 0\n" % self.qml)
        self.assertFalse(self.n.active(), "перепроверка не чаще раза в 5 с")
        self.clock.advance(5)
        self.assertTrue(self.n.active())
        self.n.forget_tv_state()
        self.assertFalse(self.n.active(), "состояние ТВ неизвестно - оверлей")
        self.n.tv_state(None, False)
        self.assertTrue(self.n.active())
        with open(self.qml, "w") as f:
            f.write("Item {}\n")                          # смонтировано что-то без shownVolume
        self.clock.advance(5)
        self.assertFalse(self.n.active())

    def test_rules(self):
        self.assertEqual(self.sets(lambda: self.n.push(23, True, "key")), [], "состояние ТВ неизвестно - вслепую не трогать")
        self.assertTrue(self.n.tv_state(20, False), "состояние стало известно -> Hub выставит базовую точку")
        self.assertFalse(self.n.tv_state(21, None))
        self.assertEqual(self.sets(lambda: self.n.push(21, False, "external")), [], "без нажатия и без расхождения")
        self.assertEqual(self.sets(lambda: self.n.push(23, False, "start")), ["NATIVE setVolume 23 (start)"])
        self.assertEqual(self.sets(lambda: self.n.push(23, True, "key")), ["NATIVE setVolume 23 (key)"],
                         "после нажатия - всегда (QML берёт только cause setVolume)")
        self.n.tv_state(None, True)
        self.assertEqual(self.sets(lambda: self.n.push(25, True, "key")), [], "ТВ в mute: setVolume снял бы mute")
        self.assertEqual(self.sets(lambda: self.n.tv_state(24, False)), ["NATIVE setVolume 25 (unmute)"],
                         "пропущенное - сразу после unmute")
        self.assertEqual(self.sets(lambda: self.n.tv_state(None, False)), [], "один раз")
        self.assertEqual(self.sets(lambda: self.n.push(25, False, "external")), [], "совпадает - не трогать")
        self.n.forget_tv_state()
        self.assertEqual(self.sets(lambda: self.n.push(27, True, "key")), [], "подписка оборвалась - не трогать")

    def test_feedback_guard_needs_three_in_a_row(self):
        self.n.tv_state(20, False)
        frame = lambda idle: self.n.feedback(self.clock.t + 0.05, idle, "05:44 down")   # noqa: E731
        for v in (21, 22):
            self.sets(lambda: self.n.push(v, True, "key"))
            frame(5.0)
            frame(5.0)                                   # второй кадр на тот же setVolume не считается
        self.assertIsNone(self.n.disabled)
        self.sets(lambda: self.n.push(23, True, "key"))  # setVolume без ответа ТВ -> серия обнуляется
        self.sets(lambda: self.n.push(24, True, "key"))
        frame(None)                                      # пульт не читается - не судим
        frame(0.3)                                       # пульт только что трогали - не обратная связь
        self.assertEqual(self.n.streak, 0)
        for v in (25, 26, 27):
            self.sets(lambda: self.n.push(v, True, "key"))
            self.clock.advance(1)
            self.n.feedback(self.clock.t, 5.0, "late")   # через 1 с после setVolume - не ответ на него
        self.assertIsNone(self.n.disabled)
        for v in (28, 29, 30):
            self.sets(lambda: self.n.push(v, True, "key"))
            frame(5.0)
        self.assertIn("TV answers setVolume", self.n.disabled or "")
        self.clock.advance(5)
        with open(self.mounts, "a") as f:
            f.write("tmpfs %s tmpfs rw 0 0\n" % self.qml)
        self.assertFalse(self.n.active(), "после срабатывания защиты - только оверлей")


class NativeSinkLive(unittest.TestCase):
    def test_setvolume_latest_wins_and_rejection(self):
        with tempfile.TemporaryDirectory() as d:
            fake, rec = os.path.join(d, "luna-send"), os.path.join(d, "calls")
            with open(fake, "w") as f:
                f.write('#!/bin/sh\nprintf "%%s %%s\\n" "$3" "$4" >> "%s"\nsleep 0.3\n'
                        'case "$4" in *99*) echo \'{"returnValue":false,"errorText":"x"}\' ;; '
                        '*) echo \'{"returnValue":true}\' ;; esac\n' % rec)
            os.chmod(fake, 0o755)
            b._LOG["path"] = None
            n = b.NativeSink()
            n.luna = fake
            n.tv_state(20, False)
            for v in range(21, 26):
                n.push(v, True, "key")
                time.sleep(0.02)
            time.sleep(1.0)
            self.assertEqual(n.tv_volume, 25)
            n.push(99, True, "key")
            time.sleep(0.6)
            with open(rec) as f:
                calls = f.read().splitlines()
        self.assertEqual(calls, [b.AUDIO_SET_VOLUME + ' {"volume":21}', b.AUDIO_SET_VOLUME + ' {"volume":25}',
                                 b.AUDIO_SET_VOLUME + ' {"volume":99}'], "в полёте один, затем последнее")
        self.assertEqual(n.tv_volume, 25, "отклонённый setVolume не меняет известное значение ТВ")
        self.assertGreater(n.set_done, n.set_start)


    def test_rejections_turn_native_off_and_restore_the_indicator(self):
        with tempfile.TemporaryDirectory() as d:
            fake, rec, osd = os.path.join(d, "luna-send"), os.path.join(d, "calls"), os.path.join(d, "native-osd.sh")
            with open(fake, "w") as f:
                f.write('#!/bin/sh\necho "$4" >> "%s"\necho \'{"returnValue":false,"errorText":"no"}\'\n' % rec)
            with open(osd, "w") as f:
                f.write('echo "$1" >> "%s"; echo reverted\n' % rec)
            os.chmod(fake, 0o755)
            b._LOG["path"] = None
            os.environ["CEC_DIR"] = d
            try:
                n = b.NativeSink()
            finally:
                del os.environ["CEC_DIR"]
            n.luna = fake
            n.tv_state(20, False)
            for v in (21, 22, 23):
                n.push(v, True, "key")
                time.sleep(0.3)
            time.sleep(0.5)
            n.push(24, True, "key")
            n.tv_state(None, True)
            n.push(25, True, "key")
            n.tv_state(None, False)                      # unmute после выключения - тоже ничего
            time.sleep(0.3)
            with open(rec) as f:
                calls = f.read().splitlines()
            no_native = os.path.exists(os.path.join(d, "no-native"))
        self.assertIn("rejects setVolume 3 times", n.disabled or "")
        self.assertEqual(calls, ['{"volume":21}', '{"volume":22}', '{"volume":23}', "revert"],
                         "после 3 отказов: штатный индикатор назад, больше ни одного setVolume")
        self.assertTrue(no_native, "при загрузке патч больше не ставится")
        self.assertEqual(n.tv_volume, 20)


class KeysAndFrames(unittest.TestCase):
    def test_idle_and_level_frame(self):
        k = b.KeyState()
        self.assertIsNone(k.idle_for(time.monotonic()), "пульт не читается")
        k.devices = 1
        k.set(True)
        self.assertEqual(k.idle_for(time.monotonic()), 0.0)
        k.set(False)
        time.sleep(0.05)
        self.assertGreaterEqual(k.idle_for(time.monotonic()), 0.05)
        self.assertEqual(b.classify(bytes.fromhex("057314")), ("level", 0x14))
        self.assertIsNone(b.classify(bytes.fromhex("507314")), "0x73 не от ТВ - не наш случай")


class ReplayTelemetry(unittest.TestCase):
    """Реальная последовательность нажатий/отчётов с OLED65M59LA + HT-A8000 (try.log, режим оверлея)."""

    def feed(self, hub, clock):
        for t, kind, a, src in telemetry():
            clock.t = t
            if kind == "key":
                hub.on_key(a, src)
            else:
                hub.on_report(a, src)

    def test_native_shows_only_real_values_and_never_misses_one(self):
        events = telemetry()
        self.assertEqual((sum(e[1] == "report" for e in events), sum(e[1] == "key" for e in events)), (79, 103))
        b._LOG["path"] = None
        nat = FakeNative()
        hub, out, clock = make_hub(native=nat)
        self.feed(hub, clock)
        self.assertEqual(out, [], "в нативном режиме оверлей не запускается ни разу")
        reports = [(e[2] & 0x7F, bool(e[2] & 0x80)) for e in events if e[1] == "report"]
        real = set(v for v, m in reports if not m)
        self.assertTrue(all(v in real for v, _, _ in nat.pushes), "только значения, присланные саундбаром")
        shown, prev = [], None
        for (v, m) in reports:                           # каждое изменение (не mute) должно дойти до ТВ
            if not m and v != prev:
                shown.append(v)
                prev = v
        pushed, prev = [], None
        for v, _, _ in nat.pushes:
            if v != prev:
                pushed.append(v)
                prev = v
        self.assertEqual(pushed, shown)
        self.assertEqual(nat.pushes[-1][0], 14, "итог совпадает с саундбаром")

    def test_overlay_hides_with_the_native_indicator(self):
        hub, out, clock = make_hub()
        stamps = []
        orig = hub.publish
        hub.publish = lambda p: (stamps.append(clock.t), orig(p))
        presses = [e[0] for e in telemetry() if e[1] == "key" and e[3] == "remote"]
        self.feed(hub, clock)
        starts = [i for i, t in enumerate(stamps) if i == 0 or t - stamps[i - 1] > 1.5]
        self.assertEqual(out[0]["reason"], "report", "до первого отчёта значение неизвестно - показывать нечего")
        keyed_starts = [i for i in starts[1:] if "kt" in out[i]]
        self.assertGreaterEqual(len(keyed_starts), 6)
        for i in keyed_starts:                           # окно открывается в момент нажатия, не ждёт ответа
            self.assertEqual(out[i]["reason"], "key")
            self.assertIn(stamps[i], presses)
        for i in set(starts[1:]) - set(keyed_starts):    # изменения из приложения Sony - по отчёту
            self.assertEqual(out[i]["reason"], "report")
        keyed = [(t, p) for t, p in zip(stamps, out) if "kt" in p]
        self.assertGreater(len(keyed), 50)
        for t, p in keyed:                               # kt - не позже публикации и не старше ttl
            age = t - (p["kt"] / 1000.0 - 1.7e9)
            self.assertTrue(-1e-6 <= age < 1.5, (t, p))
        ext = [p for p in out if "kt" not in p]
        self.assertTrue(all(p["reason"] == "report" for p in ext), "без kt - только изменения из приложения Sony")



class FakeBus(object):
    def __init__(self, running=True, sees_reports=False):
        self.running, self.sees_reports, self.sees_keys = running, sees_reports, False


class Script(object):
    """Подмена eim_query: ответы по очереди (последний повторяется), Exception - выбросить."""
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self):
        self.calls += 1
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


class Eim(unittest.TestCase):
    """--arc-source auto|eim: luna://com.webos.service.eim/getArcVolume."""

    def poller(self, *answers, **kw):
        bus = kw.pop("bus", None)
        hub, out, clock = make_hub(**kw)
        q = Script(*answers)
        return b.EimPoller(hub, hub.cfg, bus, query=q, clock=clock), hub, out, clock, q

    def test_parse(self):
        P = b.parse_arc_volume
        self.assertEqual(P('{"Volume":10}'), (10, None), "ответ OLED65M59LA (10.3.0)")
        self.assertEqual(P('junk\n{"Volume": 0}\n'), (0, None))
        self.assertEqual(P('{"Volume":11}\n{"Volume":12}'), (11, None))
        self.assertEqual(P('{"returnValue":true,"volume":100}'), (100, None))
        self.assertEqual(P('{"Volume":139}'), (11, True), "бит mute, как в байте 0x7A")
        for bad in ('', 'no json', '{"returnValue":false,"errorText":"Unknown method"}', '{"Volume":true}',
                    '{"Volume":"5"}', '{"Volume":101}', '{"Volume":-1}', '{"Volume":229}', '[1]', '{"Volume":',
                    '{"returnValue":false,"Volume":5}'):
            self.assertIsNone(P(bad), bad)

    def test_rates_and_wake(self):
        e, hub, out, clock, q = self.poller('{"Volume":20}', arc_source="eim")
        self.assertEqual(e.step(), b.EIM_FAST, "первое значение - изменение -> часто")
        clock.advance(b.EIM_FAST_FOR + 0.1)
        self.assertEqual(e.step(), b.EIM_IDLE)
        self.assertFalse(e.wake.is_set())
        hub.on_key("up", "remote")
        self.assertTrue(e.wake.is_set(), "нажатие будит опрос сразу")
        self.assertEqual(e.step(), b.EIM_FAST)
        clock.advance(b.EIM_FAST_FOR - 0.1)
        self.assertEqual(e.step(), b.EIM_FAST)
        clock.advance(0.2)
        self.assertEqual(e.step(), b.EIM_IDLE)
        q.answers = ['{"Volume":21}']                     # пульт саундбара: изменение без нажатия на ТВ
        self.assertEqual(e.step(), b.EIM_FAST)
        self.assertEqual(q.calls, 6)

    def test_auto_only_while_the_bus_brings_no_reports(self):
        bus = FakeBus(running=True)
        e, hub, out, clock, q = self.poller('{"Volume":20}', bus=bus)
        logs = []
        orig, b.log = b.log, lambda m, echo=True: logs.append(m)
        try:
            e.step()
            clock.advance(b.EIM_GRACE + 1)
            e.step()
            self.assertEqual(q.calls, 0, "шина работает, нажатий не было - EIM не нужен")
            hub.on_report(0x14, "cec")
            bus.sees_reports = True
            hub.on_key("up", "cec")
            clock.advance(b.EIM_BUS_WAIT + 0.1)
            e.step()
            self.assertEqual(q.calls, 0, "шина приносит 50:7A")
            bus.sees_reports = False                      # шину перезапустили, 50:7A ещё не было
            hub.on_key("up", "cec")
            clock.advance(b.EIM_BUS_WAIT - 0.1)
            e.step()
            self.assertEqual(q.calls, 0, "ответ саундбара ещё может прийти")
            clock.advance(0.2)
            e.step()
            self.assertEqual(q.calls, 1)
            self.assertTrue(e.takes_reports(), "значения из EIM - 50:7A из лога не нужен")
            bus.sees_reports = True
            e.step()
            self.assertEqual(q.calls, 1)
            self.assertFalse(e.takes_reports())
            bus.running, bus.sees_reports = False, False  # монитор закрылся (Unavailable)
            e.step()
            self.assertEqual(q.calls, 2)
        finally:
            b.log = orig
        self.assertEqual([m for m in logs if m.startswith("arc source")],
                         ["arc source: eim getArcVolume (no 50:7A on the CEC bus after a key press)",
                          "arc source: CEC 50:7A (eim polling paused)",
                          "arc source: eim getArcVolume (CEC monitor unavailable)"])

    def test_auto_without_bus_and_bus_race(self):
        e, hub, out, clock, q = self.poller('{"Volume":20}', bus=None)
        e.step()
        self.assertEqual((q.calls, hub.raw), (1, 20), "нет /dev/cec (--no-cec-device) - EIM сразу")
        self.assertTrue(e.takes_reports())
        q.answers = [OSError(2, "No such file or directory")]
        e.step()
        self.assertFalse(e.takes_reports(), "опрос не удался - 50:7A из лога снова показываются")
        bus = FakeBus(running=False)
        e2, hub2, out2, clock2, q2 = self.poller('{"Volume":20}', bus=bus)
        e2.step()
        self.assertEqual(q2.calls, 0, "первые EIM_GRACE с ждём шину")
        clock2.advance(b.EIM_GRACE)

        def answer():                                     # шина заговорила, пока шёл запрос
            bus.running = bus.sees_reports = True
            return '{"Volume":33}'
        e2.query = answer
        e2.step()
        self.assertIsNone(hub2.raw, "значение EIM отброшено: уже есть 50:7A")
        self.assertFalse(e2.takes_reports())

    def test_eim_values_mute_and_settle(self):
        e, hub, out, clock, q = self.poller('{"Volume":20}', arc_source="eim")
        e.step()
        self.assertEqual(out, [], "первое значение - тихая базовая точка")
        hub.on_key("up", "remote")
        self.assertEqual([o["reason"] for o in out], ["key"])
        clock.advance(0.15)
        e.step()                                          # опрос опередил саундбар: то же значение
        self.assertEqual(len(out), 1)
        q.answers = ['{"Volume":21}']
        clock.advance(0.15)
        e.step()
        self.assertEqual((out[-1]["reason"], out[-1]["volume"], out[-1]["mute"]), ("report", 21, False))
        clock.advance(3)
        hub.on_key("up", "remote")                        # граница/шаг: значение не меняется
        self.assertEqual(out[-1]["reason"], "key")
        clock.advance(0.3)
        e.step()
        self.assertEqual(len(out), 3)
        clock.advance(b.EIM_SETTLE)
        e.step()
        self.assertEqual(out[-1]["reason"], "repeat", "то же значение через EIM_SETTLE после нажатия")
        hub.set_tv_state(17, True)                        # muteStatus ТВ (overlay: native нет)
        clock.advance(3)
        e.step()
        self.assertEqual((out[-1]["volume"], out[-1]["mute"]), (21, True), "mute для EIM - от ТВ")
        q.answers = ['{"Volume":150}']                    # 128 + 22: бит mute в ответе
        hub.set_tv_state(None, False)
        e.step()
        self.assertEqual((out[-1]["volume"], out[-1]["mute"]), (22, True))
        hub.forget_tv_state()
        self.assertIsNone(hub.tv_mute)

    def test_failures_make_it_unavailable(self):
        e, hub, out, clock, q = self.poller(
            OSError(2, "No such file or directory"),
            '{"returnValue":false,"errorCode":-1,"errorText":"Unknown method \\"getArcVolume\\""}', arc_source="eim")
        for _ in range(b.EIM_FAILS - 1):
            self.assertEqual(e.step(), b.EIM_IDLE)
        with self.assertRaises(b.Unavailable) as ctx:
            e.step()
        self.assertIn('getArcVolume unavailable ({"returnValue":false,"errorCode":-1,"errorText":"Unknown method',
                      str(ctx.exception))
        q.answers = ['{"Volume":5}']
        e.step()
        self.assertEqual(hub.raw, 5)
        e2, _, _, _, _ = self.poller(subprocess.TimeoutExpired("luna-send", 1.5), arc_source="eim")
        for _ in range(b.EIM_FAILS - 1):
            e2.step()
        self.assertRaisesRegex(b.Unavailable, "timed out", e2.step)

    def test_line_sink_and_bus_in_eim_mode(self):
        class Eim(object):
            on = True

            def takes_reports(self):
                return self.on
        rec, eim = Sink.Rec(), Eim()
        sink = b.LineSink(rec, FakeBus(sees_reports=False), eim)
        sink(["RX507A14", "TX054441"])
        self.assertEqual(rec.calls, [("key", "up")], "50:7A из лога не показывается, пока значения даёт EIM")
        eim.on = False
        sink(["RX507A15"])
        self.assertEqual(rec.calls[-1], ("report", 0x15))
        # --arc-source eim: шина видит 50:7A, но в Hub его не передаёт
        r, w = os.pipe()
        queue = [bytes.fromhex("507A15"), bytes.fromhex("054441")]

        def fake_ioctl(fd, req, buf, *a):
            if req == b.CEC_RECEIVE:
                if not queue:
                    raise OSError(b.errno.EAGAIN, "empty")
                frame = queue.pop(0)
                buf[:] = b.CEC_MSG.pack(0, 0, len(frame), 0, 0, 0, frame, 0, 0, 0, 0, 0, 0, 0)
                return 0
            return 0
        rec2 = Sink.Rec()
        bus = b.CecBus(cfg(arc_source="eim", no_tap_file="/nonexistent", final_query=False), rec2, b.KeyState())
        orig, b.fcntl.ioctl = b.fcntl.ioctl, fake_ioctl
        stop = threading.Event()
        os.write(w, b"x")
        th = threading.Thread(target=bus.loop, args=(stop, r, None))
        th.daemon = True
        try:
            th.start()
            time.sleep(0.3)
        finally:
            stop.set()
            th.join(3)
            b.fcntl.ioctl = orig
            os.close(r)
            os.close(w)
        self.assertEqual(rec2.calls, [("key", "up")])
        self.assertTrue(bus.sees_reports)

    def test_probe(self):
        self.assertEqual(b.probe_eim(lambda: '{"Volume":11}'),
                         ["EIM getArcVolume: Volume 11 - --arc-source auto uses it when the CEC bus brings no 50:7A"])
        self.assertIn("Volume 3 muted", b.probe_eim(lambda: '{"Volume":131}')[0])
        self.assertEqual(b.probe_eim(lambda: '{"returnValue":false,"errorText":"Unknown method"}'),
                         ['EIM getArcVolume: unavailable ({"returnValue":false,"errorText":"Unknown method"})'])
        self.assertEqual(b.probe_eim(lambda: ""), ["EIM getArcVolume: unavailable (no reply)"])

        def boom():
            raise OSError(2, "No such file or directory")
        self.assertEqual(b.probe_eim(boom), ["EIM getArcVolume: unavailable ([Errno 2] No such file or directory)"])
        self.assertTrue(b.short_reply("x" * 200).endswith("..."))

    def test_real_query_and_thread_with_fake_luna_send(self):
        with tempfile.TemporaryDirectory() as d:
            fake, calls = os.path.join(d, "luna-send"), os.path.join(d, "calls")
            with open(fake, "w") as f:
                f.write('#!/bin/sh\necho "$*" >> "%s"\n'
                        '[ -c /dev/stdin ] || exit 0\n'        # как на ТВ: из канала ничего не выводит
                        'echo \'{"Volume":33}\'\n' % calls)
            os.chmod(fake, 0o755)
            self.assertEqual(b.parse_arc_volume(b.eim_query(fake)), (33, None))
            hub, out, clock = make_hub(arc_source="eim")
            e = b.EimPoller(hub, hub.cfg, None, query=lambda: b.eim_query(fake))
            stop = threading.Event()
            th = threading.Thread(target=e, args=(stop,))
            th.daemon = True
            th.start()
            time.sleep(0.5)
            stop.set()
            e.wake.set()
            th.join(3)
            self.assertFalse(th.is_alive())
            self.assertEqual(hub.raw, 33)
            self.assertFalse(e.delivering, "остановлен - лог снова источник")
            with open(calls) as f:
                lines = f.read().splitlines()
        self.assertGreaterEqual(len(lines), 2)
        self.assertEqual(lines[0], "-n 1 luna://com.webos.service.eim/getArcVolume {}")

    def test_cli_flag(self):
        ns = b.build_parser().parse_args([])
        self.assertEqual(ns.arc_source, "auto")
        self.assertEqual(b.build_parser().parse_args(["--arc-source", "eim"]).arc_source, "eim")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            b.build_parser().parse_args(["--arc-source", "master"])


class Rejected(unittest.TestCase):
    def test_logged_once_per_text_and_interval(self):
        with tempfile.TemporaryDirectory() as d:
            b._LOG["path"] = os.path.join(d, "log")
            try:
                p = b.Publisher("com.test.app", {}, dry_run=True)
                err = '{"returnValue":false,"errorText":"app not found"}'
                for i in range(5):
                    p.rejected(err, 100.0 + i)
                p.rejected('{"returnValue":false,"errorText":"other"}', 106.0)
                p.rejected('{"returnValue":false,"errorText":"other"}', 106.0 + b.REJECT_RELOG)
                with open(b._LOG["path"]) as f:
                    lines = [l.split(" ", 2)[2].strip() for l in f]
            finally:
                b._LOG["path"] = None
        self.assertEqual(lines, [
            'launch rejected: %s - is the eArcNG app (com.test.app) installed?' % err,
            'launch rejected: {"returnValue":false,"errorText":"other"} - is the eArcNG app (com.test.app) installed? '
            '[4 repeats not logged]',
            'launch rejected: {"returnValue":false,"errorText":"other"} - is the eArcNG app (com.test.app) installed?'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
