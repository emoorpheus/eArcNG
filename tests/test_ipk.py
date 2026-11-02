# Тест backend/ipk.py: IPK приложения eArcNG в формате ares-package (ar + control.tar.gz + data.tar.gz).
#   python3 -m unittest discover -s tests -p 'test_*.py'
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IPK_PY = os.path.join(ROOT, "backend", "ipk.py")
APP = os.path.join(ROOT, "app")
sys.path.insert(0, os.path.join(ROOT, "backend"))
import ipk  # noqa: E402


def ar_members(data):
    assert data[:8] == b"!<arch>\n", "ar magic"
    pos, out = 8, []
    while pos < len(data):
        h = data[pos:pos + 60]
        assert h[58:60] == b"`\n", "ar header end"
        name, mtime, uid, gid, mode, size = (h[0:16], h[16:28], h[28:34], h[34:40], h[40:48], h[48:58])
        size = int(size)
        out.append({"name": name.decode().rstrip(), "mtime": int(mtime), "uid": int(uid), "gid": int(gid),
                    "mode": mode.decode().strip(), "data": data[pos + 60:pos + 60 + size]})
        pos += 60 + size + size % 2
    assert pos == len(data), "ar: no trailing bytes"
    return out


def run(*args):
    return subprocess.run([sys.executable, IPK_PY] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


class IpkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.info = json.load(open(os.path.join(APP, "appinfo.json")))
        self.app_id, self.version = self.info["id"], self.info["version"]

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def build(self, app_dir=APP, name="a.ipk"):
        out = os.path.join(self.tmp, name)
        r = run("build", app_dir, out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "%s %s" % (self.app_id, self.version))
        with open(out, "rb") as f:
            return f.read()

    def test_app_identity(self):
        self.assertEqual(self.app_id, "com.github.emoorpheus.earcng")
        bridge = open(os.path.join(ROOT, "backend", "cec_volume_bridge.py")).read()
        self.assertEqual(self.version, re.search(r'^VERSION = "(.*)"$', bridge, re.M).group(1))
        self.assertEqual(run("info", APP).stdout.strip(), "%s %s" % (self.app_id, self.version))

    def test_ar_layout(self):
        m = ar_members(self.build())
        self.assertEqual([x["name"] for x in m], ["debian-binary", "control.tar.gz", "data.tar.gz"])
        self.assertEqual(m[0]["data"], b"2.0\n")
        for x in m:
            self.assertEqual((x["mtime"], x["uid"], x["gid"], x["mode"]), (0, 0, 0, "100644"), x["name"])

    def test_control(self):
        control_tgz = ar_members(self.build())[1]["data"]
        with tarfile.open(fileobj=io.BytesIO(control_tgz)) as t:
            self.assertEqual(t.getnames(), [".", "./control"] if t.getnames()[0] == "." else ["./", "./control"])
            text = t.extractfile("./control").read().decode()
        fields = dict(line.split(": ", 1) for line in text.splitlines())
        self.assertEqual(fields["Package"], self.app_id)
        self.assertEqual(fields["Version"], self.version)
        self.assertEqual(fields["Architecture"], "all")
        self.assertEqual(fields["webOS-Package-Format-Version"], "2")
        self.assertTrue(fields["Installed-Size"].isdigit() and int(fields["Installed-Size"]) > 0)
        self.assertIn("emoorpheus", fields["Maintainer"])
        self.assertTrue(text.endswith("\n"))

    def test_data(self):
        data_tgz = ar_members(self.build())[2]["data"]
        self.assertEqual(gzip.decompress(data_tgz)[257:263], b"ustar\x00", "POSIX ustar")
        apps = "./usr/palm/applications/%s/" % self.app_id
        pkgs = "./usr/palm/packages/%s/" % self.app_id
        files = ipk.app_files(APP)
        self.assertIn("index.html", files)
        self.assertIn("appinfo.json", files)
        with tarfile.open(fileobj=io.BytesIO(data_tgz)) as t:
            members = t.getmembers()
            names = [m.name.rstrip("/") or "." for m in members]
            want_dirs = [".", "./usr", "./usr/palm", "./usr/palm/applications", apps.rstrip("/"),
                         "./usr/palm/packages", pkgs.rstrip("/")]
            self.assertEqual([n for n, m in zip(names, members) if m.isdir()], want_dirs)
            self.assertEqual(sorted(n for n, m in zip(names, members) if m.isfile()),
                             sorted([apps + f for f in files] + [pkgs + "packageinfo.json"]))
            for m in members:
                self.assertEqual((m.uid, m.gid, m.mtime), (0, 0, 0), m.name)
                self.assertEqual(m.mode, 0o755 if m.isdir() else 0o644, m.name)
            for f in files:
                with open(os.path.join(APP, f), "rb") as src:
                    self.assertEqual(t.extractfile(apps + f).read(), src.read(), f)
            pkg = json.loads(t.extractfile(pkgs + "packageinfo.json").read().decode())
        self.assertEqual(pkg, {"app": self.app_id, "id": self.app_id, "loc_name": "eArcNG", "package_format_version": 2,
                               "vendor": "emoorpheus", "version": self.version})

    def test_reproducible_and_hidden_files(self):
        a = self.build()
        copy = os.path.join(self.tmp, "app")
        shutil.copytree(APP, copy)
        for junk in (".DS_Store", "._index.html"):
            open(os.path.join(copy, junk), "w").write("junk")
        os.makedirs(os.path.join(copy, ".git"))
        open(os.path.join(copy, ".git", "x"), "w").write("x")
        self.assertEqual(self.build(copy, "b.ipk"), a, "same bytes; macOS ._*/.DS_Store and dot dirs are skipped")

    def test_subdirectories(self):
        copy = os.path.join(self.tmp, "app")
        shutil.copytree(APP, copy)
        os.makedirs(os.path.join(copy, "img", "x"))
        open(os.path.join(copy, "img", "x", "a.png"), "wb").write(b"\x89PNG")
        data_tgz = ar_members(self.build(copy))[2]["data"]
        with tarfile.open(fileobj=io.BytesIO(data_tgz)) as t:
            names = t.getnames()
        apps = "./usr/palm/applications/%s/" % self.app_id
        for n in (apps + "img", apps + "img/x", apps + "img/x/a.png"):
            self.assertTrue(n in names or n + "/" in names, n)
        self.assertLess(names.index((apps + "img") if apps + "img" in names else apps + "img/"),
                        names.index(apps + "img/x/a.png"), "directory before its files")

    def test_same(self):
        inst = os.path.join(self.tmp, "installed")
        shutil.copytree(APP, inst)
        open(os.path.join(inst, "index.html.stock"), "w").write("extra files do not matter")
        self.assertEqual(run("same", APP, inst).returncode, 0)
        with open(os.path.join(inst, "overlay.js"), "a") as f:
            f.write("//")
        self.assertEqual(run("same", APP, inst).returncode, 1, "changed file")
        shutil.copy(os.path.join(APP, "overlay.js"), os.path.join(inst, "overlay.js"))
        os.remove(os.path.join(inst, "icon.png"))
        self.assertEqual(run("same", APP, inst).returncode, 1, "missing file")
        self.assertEqual(run("same", APP, os.path.join(self.tmp, "none")).returncode, 1)

    def test_errors(self):
        bad = os.path.join(self.tmp, "bad")
        os.makedirs(bad)
        r = run("build", bad, os.path.join(self.tmp, "x.ipk"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("appinfo.json", r.stderr)
        json.dump({"id": "a.b", "version": "1.0.0", "title": "T", "main": "index.html"}, open(os.path.join(bad, "appinfo.json"), "w"))
        r = run("build", bad, os.path.join(self.tmp, "x.ipk"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("main file 'index.html' not found", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "x.ipk")))
        json.dump({"id": "a.b", "title": "T", "main": "index.html"}, open(os.path.join(bad, "appinfo.json"), "w"))
        self.assertIn("'version' is missing", run("info", bad).stderr)
        self.assertEqual(run("bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
