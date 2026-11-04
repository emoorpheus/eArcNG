# Тест tools/make-release.py: IPK + манифест Homebrew Channel + zip из git-тега + SHA256SUMS; repo.json в корне.
#   python3 -m unittest discover -s tests -p 'test_*.py'
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ipk  # noqa: E402
from test_ipk import ar_members  # noqa: E402

APP_ID = "com.github.emoorpheus.earcng"
IPK_NAME = APP_ID + "_%s_all.ipk"
MANIFEST = APP_ID + ".manifest.json"
REPO = "https://github.com/emoorpheus/earcng"
ICON = "https://raw.githubusercontent.com/emoorpheus/earcng/main/app/largeIcon.png"
GIT = ("git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
       "-c", "tag.gpgsign=false", "-c", "init.defaultBranch=main")


def version():
    with open(os.path.join(ROOT, "app", "appinfo.json")) as f:
        return json.load(f)["version"]


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def ipk_names(path):
    with open(path, "rb") as f:
        data = {m["name"]: m["data"] for m in ar_members(f.read())}["data.tar.gz"]
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        return t.getnames()


class Release(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="earcng-release-test-")
        self.addCleanup(shutil.rmtree, self.tmp)
        self.v = version()

    def tree(self, name="repo"):
        """Копия нужной части репозитория (без __pycache__)."""
        dst = os.path.join(self.tmp, name)
        ign = shutil.ignore_patterns("__pycache__", "*.pyc", "out")
        for d in ("app", "backend", "tools", "docs"):
            shutil.copytree(os.path.join(ROOT, d), os.path.join(dst, d), ignore=ign)
        for f in ("CHANGELOG.md", ".gitattributes", "repo.json"):
            shutil.copy(os.path.join(ROOT, f), dst)
        return dst

    def release_ready(self, root):
        """CHANGELOG с разделом версии и release notes - как перед тегом."""
        with open(os.path.join(root, "CHANGELOG.md"), "a") as f:
            f.write("\n## [%s] - 2026-11-10\n\n### Added\n- test\n" % self.v)
        notes = os.path.join(root, "docs", "release-notes", "v%s.md" % self.v)
        if not os.path.exists(notes):
            with open(notes, "w") as f:
                f.write("test\n")

    def git(self, root, *args):
        subprocess.check_call(GIT + args, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def make(self, root, *args):
        return subprocess.run([sys.executable, os.path.join(root, "tools", "make-release.py")] + list(args),
                              cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE=""))

    def manifest(self, out):
        with open(os.path.join(out, MANIFEST)) as f:
            return json.load(f)

    def check_sums(self, out, names):
        with open(os.path.join(out, "SHA256SUMS")) as f:
            lines = f.read().splitlines()
        self.assertEqual([l.split("  ", 1)[1] for l in lines], names)
        for line in lines:
            h, n = line.split("  ", 1)
            self.assertEqual(h, sha256(os.path.join(out, n)), n)

    def test_draft_from_the_working_tree(self):
        root = self.tree()
        out = os.path.join(self.tmp, "out")
        r = self.make(root, "--draft", "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("eArcNG %s (draft: working tree)" % self.v, r.stdout)
        ipk_path = os.path.join(out, IPK_NAME % self.v)
        m = self.manifest(out)
        self.assertEqual(m, {
            "id": APP_ID, "version": self.v, "type": "web", "title": "eArcNG",
            "appDescription": "Real eARC/ARC soundbar volume on the TV screen (HDMI-CEC).",
            "iconUri": ICON, "sourceUrl": REPO, "rootRequired": True,
            "ipkUrl": "%s/releases/download/v%s/%s" % (REPO, self.v, IPK_NAME % self.v),
            "ipkHash": {"sha256": sha256(ipk_path)}, "ipkSize": os.path.getsize(ipk_path)})
        self.assertEqual(sorted(os.listdir(out)), sorted([IPK_NAME % self.v, MANIFEST, "SHA256SUMS"]), "draft - без zip")
        self.check_sums(out, [IPK_NAME % self.v, MANIFEST])
        names = ipk_names(ipk_path)
        for f in ipk.BACKEND_FILES:
            self.assertIn("./usr/palm/applications/%s/backend/%s" % (APP_ID, f), names)
        same = os.path.join(self.tmp, "same.ipk")
        ipk.build(os.path.join(root, "app"), same, os.path.join(root, "backend"))
        with open(same, "rb") as a, open(ipk_path, "rb") as b:
            self.assertEqual(a.read(), b.read(), "тот же IPK, что собирает ТВ (ipk.build с бэкендом)")
        self.assertFalse(os.path.exists(os.path.join(root, "backend", "__pycache__")), "без __pycache__ в backend/")
        r = self.make(root, "--draft", "--out", out)                          # повтор: старые файлы заменяются
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(out)), sorted([IPK_NAME % self.v, MANIFEST, "SHA256SUMS"]))

    def test_release_is_built_from_the_tag(self):
        if not shutil.which("git"):
            self.skipTest("git not found")
        root = self.tree()
        self.release_ready(root)
        self.git(root, "init", "-q")
        self.git(root, "add", "-A")
        self.git(root, "commit", "-q", "-m", "release")
        self.git(root, "tag", "-a", "v" + self.v, "-m", "eArcNG " + self.v)
        tagged = os.path.join(self.tmp, "tagged.ipk")
        ipk.build(os.path.join(root, "app"), tagged, os.path.join(root, "backend"))
        with open(os.path.join(root, "app", "overlay.js"), "a") as f:
            f.write("// after the tag\n")                                      # рабочее дерево ушло вперёд
        r = self.make(root, "v" + self.v)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = os.path.join(root, "dist")
        self.assertIn("(from tag v%s)" % self.v, r.stdout)
        self.assertIn("Attach these files to the GitHub release v%s" % self.v, r.stdout)
        zname = "earcng-%s.zip" % self.v
        self.assertEqual(sorted(os.listdir(out)), sorted([IPK_NAME % self.v, MANIFEST, zname, "SHA256SUMS"]))
        self.check_sums(out, [IPK_NAME % self.v, MANIFEST, zname])
        with open(tagged, "rb") as a, open(os.path.join(out, IPK_NAME % self.v), "rb") as b:
            self.assertEqual(a.read(), b.read(), "IPK из тега, а не из рабочего дерева")
        with zipfile.ZipFile(os.path.join(out, zname)) as z:
            names = z.namelist()
            top = "earcng-%s/" % self.v
            self.assertTrue(all(n.startswith(top) for n in names), "одна своя верхняя папка (Safari распаковывает)")
            self.assertIn(top + "tools/make-release.py", names)
            self.assertNotIn(b"after the tag", z.read(top + "app/overlay.js"))
        self.assertEqual(self.manifest(out)["ipkHash"]["sha256"], sha256(tagged))

    def test_refusals(self):
        root = self.tree()
        r = self.make(root, "v9.9.9", "--draft")
        self.assertEqual(r.returncode, 1)
        self.assertIn("tag v9.9.9, but the version in the files is %s" % self.v, r.stderr)
        with open(os.path.join(root, "CHANGELOG.md"), "w") as f:
            f.write("# Changelog\n\n## [Unreleased]\n\n### Added\n- test\n")
        r = self.make(root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("CHANGELOG.md has no '## [%s]' section (use --draft for a test build)" % self.v, r.stderr)
        self.release_ready(root)
        r = self.make(root)
        self.assertEqual(r.returncode, 1)
        self.assertIn("git tag v%s not found" % self.v, r.stderr, "не git-репозиторий")
        self.assertFalse(os.path.exists(os.path.join(root, "dist")), "отказ - без пустого dist/")
        if shutil.which("git"):
            self.git(root, "init", "-q")
            self.git(root, "add", "-A")
            self.git(root, "commit", "-q", "-m", "no tag")
            r = self.make(root)
            self.assertEqual(r.returncode, 1)
            self.assertIn("git tag v%s not found" % self.v, r.stderr)
        os.remove(os.path.join(root, "docs", "release-notes", "v%s.md" % self.v))
        r = self.make(root)
        self.assertIn("docs/release-notes/v%s.md not found" % self.v, r.stderr)
        html = os.path.join(root, "app", "index.html")
        with open(html) as f:
            text = f.read()
        with open(html, "w") as f:
            f.write(text.replace("· %s</div>" % self.v, "· 0.0.1</div>"))
        r = self.make(root, "--draft")
        self.assertEqual(r.returncode, 1)
        self.assertIn("versions differ: app/appinfo.json %s, backend VERSION %s, app/index.html footer 0.0.1"
                      % (self.v, self.v), r.stderr)
        self.assertFalse(os.path.exists(os.path.join(root, "dist", IPK_NAME % self.v)))

    def test_test_repository_for_homebrew_channel(self):
        root = self.tree()
        out = os.path.join(self.tmp, "out")
        r = self.make(root, "--draft", "--out", out, "--base-url", "http://192.0.2.10:8000/")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Homebrew Channel test repository: http://192.0.2.10:8000/repo.json", r.stdout)
        self.assertEqual(self.manifest(out)["ipkUrl"], "http://192.0.2.10:8000/" + IPK_NAME % self.v)
        with open(os.path.join(out, "repo.json")) as f:
            test_repo = json.load(f)
        self.assertEqual(test_repo["packages"][0]["manifestUrl"], "http://192.0.2.10:8000/" + MANIFEST)
        self.check_sums(out, [IPK_NAME % self.v, MANIFEST, "repo.json"])
        # repo.json в корне репозитория - тот же формат, manifestUrl - последний релиз (без правки при релизе)
        with open(os.path.join(ROOT, "repo.json")) as f:
            repo = json.load(f)
        test_repo["packages"][0]["manifestUrl"] = "%s/releases/latest/download/%s" % (REPO, MANIFEST)
        self.assertEqual(repo, test_repo)
        self.assertEqual(repo["packages"][0]["iconUri"], self.manifest(out)["iconUri"])
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "app", "largeIcon.png")), "iconUri указывает на файл в main")
        r = self.make(root, "--draft", "--out", out)                          # потом обычная сборка в тот же каталог
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(os.listdir(out)), sorted([IPK_NAME % self.v, MANIFEST, "SHA256SUMS"]),
                         "repo.json тестового репозитория удалён")
        self.assertEqual(self.manifest(out)["ipkUrl"], "%s/releases/download/v%s/%s" % (REPO, self.v, IPK_NAME % self.v))


if __name__ == "__main__":
    unittest.main()
