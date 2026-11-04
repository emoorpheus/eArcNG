#!/usr/bin/env python3
# make-release.py - eArcNG: release files for GitHub and Homebrew Channel (Python 3.6+, stdlib only).
#   python3 tools/make-release.py [vX.Y.Z] [--out DIR] [--draft] [--base-url URL]
# Writes into DIR (default dist/):
#   com.github.emoorpheus.earcng_X.Y.Z_all.ipk     the app with backend/ inside (Homebrew Channel installs it)
#   com.github.emoorpheus.earcng.manifest.json     Homebrew Channel manifest: ipkUrl, ipkHash.sha256, ipkSize
#   earcng-X.Y.Z.zip                               git archive of the tag vX.Y.Z (top folder earcng-X.Y.Z/:
#                                                  Safari unzips downloads, a new folder for every version)
#   SHA256SUMS
# A release is built from the tag vX.Y.Z (not from the working tree), so the IPK and the zip are the same code.
# Checks: the version in the three places, the tag, CHANGELOG.md "## [X.Y.Z]", docs/release-notes/vX.Y.Z.md.
# --draft     test build without a tag: from the working tree, no zip, no CHANGELOG / release notes checks
# --base-url  ipkUrl = URL/<ipk> instead of the GitHub release, and DIR/repo.json for it
#             (test repository for Homebrew Channel, e.g. python3 -m http.server on a PC in the same network)
# repo.json in the repository root is not written here: its manifestUrl is .../releases/latest/download/...
import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "https://github.com/emoorpheus/earcng"
RAW = "https://raw.githubusercontent.com/emoorpheus/earcng/main"
ICON_URI = RAW + "/app/largeIcon.png"
DESCRIPTION_URI = RAW + "/docs/hb-description.html"  # Homebrew Channel package page (fullDescriptionUrl)


class Fail(Exception):
    pass


def read(path):
    with open(path, "rb") as f:
        return f.read().decode("utf-8")


def versions(src):
    """Версия в трёх местах дерева src -> (appinfo, VERSION моста, подвал index.html)."""
    info = json.loads(read(os.path.join(src, "app", "appinfo.json")))
    m = re.search(r'^VERSION = "([^"]+)"$', read(os.path.join(src, "backend", "cec_volume_bridge.py")), re.M)
    f = re.search(r'<div class="setup-credit">[^<]*<b>[^<]*</b>[^<]*· ([^<\s]+)</div>',
                  read(os.path.join(src, "app", "index.html")))
    return info, info.get("version"), m and m.group(1), f and f.group(1)


def git(*args, **kw):
    p = subprocess.Popen(("git", "-C", ROOT) + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate()
    if p.returncode != 0 and kw.get("check", True):
        raise Fail("git %s: %s" % (" ".join(args), err.decode("utf-8", "replace").strip()))
    return p.returncode, out.decode("utf-8", "replace").strip()


def tag_exists(tag):
    try:
        rc, top = git("rev-parse", "--show-toplevel", check=False)
    except OSError:
        return False                          # git не установлен
    if rc != 0 or os.path.realpath(top) != os.path.realpath(ROOT):
        return False
    return git("rev-parse", "-q", "--verify", "refs/tags/" + tag, check=False)[0] == 0


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(text.encode("utf-8"))
    os.rename(tmp, path)


def make(args):
    info, *found = versions(ROOT)
    tree = ROOT
    app_id = info.get("id")
    if len(set(found)) != 1 or not found[0]:
        raise Fail("versions differ: app/appinfo.json %s, backend VERSION %s, app/index.html footer %s"
                   % tuple(found))
    version = found[0]
    tag = "v" + version
    if args.tag and args.tag != tag:
        raise Fail("tag %s, but the version in the files is %s" % (args.tag, version))
    if not args.draft:
        if not re.search(r"^## \[%s\]" % re.escape(version), read(os.path.join(ROOT, "CHANGELOG.md")), re.M):
            raise Fail("CHANGELOG.md has no '## [%s]' section (use --draft for a test build)" % version)
        if not os.path.isfile(os.path.join(ROOT, "docs", "release-notes", tag + ".md")):
            raise Fail("docs/release-notes/%s.md not found (use --draft for a test build)" % tag)
        if not tag_exists(tag):
            raise Fail("git tag %s not found: git tag -a %s -m \"eArcNG %s\" (or --draft for a test build)"
                       % (tag, tag, version))
    out = os.path.abspath(args.out or os.path.join(ROOT, "dist"))
    os.makedirs(out, exist_ok=True)
    ipk_name = "%s_%s_all.ipk" % (app_id, version)
    manifest_name = "%s.manifest.json" % app_id
    zip_name = "earcng-%s.zip" % version
    for name in (ipk_name, manifest_name, zip_name, "SHA256SUMS", "repo.json"):
        if os.path.exists(os.path.join(out, name)):
            os.remove(os.path.join(out, name))
    files = [ipk_name, manifest_name]
    tmp = tempfile.mkdtemp(prefix="earcng-release-")
    try:
        if not args.draft:
            prefix = "earcng-%s" % version
            git("archive", "--format=zip", "--prefix=%s/" % prefix, "-o", os.path.join(out, zip_name), tag)
            files.append(zip_name)
            with zipfile.ZipFile(os.path.join(out, zip_name)) as z:
                z.extractall(tmp)
            tree = os.path.join(tmp, prefix)
            info, *found = versions(tree)
            if found != [version] * 3:
                raise Fail("the tag %s has other versions: %s" % (tag, ", ".join(map(str, found))))
        sys.dont_write_bytecode = True                 # без __pycache__ в backend/
        sys.path.insert(0, os.path.join(tree, "backend"))
        import ipk
        ipk_path = os.path.join(out, ipk_name)
        ipk.build(os.path.join(tree, "app"), ipk_path, os.path.join(tree, "backend"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    base = args.base_url.rstrip("/") if args.base_url else "%s/releases/download/%s" % (REPO, tag)
    manifest = {
        "id": app_id,
        "version": version,
        "type": "web",
        "title": info["title"],
        "appDescription": info.get("appDescription", info["title"]),
        "iconUri": ICON_URI,
        "sourceUrl": REPO,
        "rootRequired": True,
        "ipkUrl": "%s/%s" % (base, ipk_name),
        "ipkHash": {"sha256": sha256(ipk_path)},
        "ipkSize": os.path.getsize(ipk_path),
    }
    write(os.path.join(out, manifest_name), json.dumps(manifest, indent=2) + "\n")
    if args.base_url:
        write(os.path.join(out, "repo.json"), json.dumps(repo_json(info, "%s/%s" % (base, manifest_name)), indent=2) + "\n")
        files.append("repo.json")
    sums = "".join("%s  %s\n" % (sha256(os.path.join(out, n)), n) for n in files)
    write(os.path.join(out, "SHA256SUMS"), sums)
    print("eArcNG %s%s -> %s" % (version, " (draft: working tree)" if args.draft else " (from tag %s)" % tag, out))
    for n in files + ["SHA256SUMS"]:
        print("  %-48s %8d bytes" % (n, os.path.getsize(os.path.join(out, n))))
    if args.base_url:
        print("Homebrew Channel test repository: %s/repo.json" % base)
    elif not args.draft:
        print("Attach these files to the GitHub release %s (text: docs/release-notes/%s.md)." % (tag, tag))
    return 0


def repo_json(info, manifest_url):
    return {
        "paging": {"page": 1, "count": 1, "maxPage": 1, "itemsTotal": 1},
        "packages": [{
            "id": info["id"],
            "title": info["title"],
            "iconUri": ICON_URI,
            "manifestUrl": manifest_url,
            "shortDescription": info.get("appDescription", info["title"]),
            "fullDescriptionUrl": DESCRIPTION_URI,
        }],
    }


def main(argv):
    p = argparse.ArgumentParser(prog="make-release.py", description="eArcNG release files (IPK, manifest, zip).")
    p.add_argument("tag", nargs="?", help="vX.Y.Z - must match the version in the files")
    p.add_argument("--out", help="output directory (default: dist/ in the repository)")
    p.add_argument("--draft", action="store_true", help="test build from the working tree: no tag, no zip")
    p.add_argument("--base-url", help="ipkUrl base for a test repository (also writes repo.json)")
    args = p.parse_args(argv[1:])
    try:
        return make(args)
    except (Fail, OSError, ValueError) as e:
        print("make-release: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
