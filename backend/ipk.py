#!/usr/bin/env python3
# ipk.py - eArcNG: сборка IPK приложения eArcNG из каталога app/ (на ТВ при установке, на ПК для релиза и тестов).
#   python3 ipk.py build <APP_DIR> <OUT.ipk> [<BACKEND_DIR>]   собрать (печатает "<id> <version>")
#   python3 ipk.py info <APP_DIR>                             id и версия из appinfo.json
#   python3 ipk.py same <APP_DIR> <DIR> [<BACKEND_DIR>]       код 0, если все файлы пакета в DIR побайтно такие же
# BACKEND_DIR: файлы бэкенда (BACKEND_FILES) кладутся в пакет как backend/ - приложение из Homebrew Channel
# ставит мост само (remote-setup.sh activate с экрана настроек).
# Формат как у ares-package (webOS CLI): ar-архив debian-binary + control.tar.gz + data.tar.gz,
# файлы в ./usr/palm/applications/<id>/, packageinfo.json в ./usr/palm/packages/<id>/.
# Только стандартная библиотека; сборка воспроизводима (mtime 0, владелец 0:0, порядок по имени).
import gzip
import io
import json
import os
import sys
import tarfile

MAINTAINER = "emoorpheus <https://github.com/emoorpheus/earcng>"
BACKEND_FILES = ("95-cec-volume-bridge", "cec_volume_bridge.py", "ipk.py", "native-osd.sh", "remote-setup.sh")


def read_appinfo(app_dir):
    with open(os.path.join(app_dir, "appinfo.json"), "rb") as f:
        info = json.loads(f.read().decode("utf-8"))
    for key in ("id", "version", "title", "main"):
        if not isinstance(info.get(key), str) or not info[key]:
            raise ValueError("appinfo.json: '%s' is missing" % key)
    return info


def app_files(app_dir):
    # все обычные файлы, кроме скрытых (.DS_Store, ._* из macOS); пути через "/"
    out = []
    for root, dirs, files in os.walk(app_dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.startswith("."):
                continue
            path = os.path.join(root, name)
            if os.path.isfile(path) and not os.path.islink(path):
                out.append(os.path.relpath(path, app_dir).replace(os.sep, "/"))
    return sorted(out)


def package_files(app_dir, backend_dir=None):
    # [(путь в пакете, путь на диске)]: файлы app/ + backend/<BACKEND_FILES>
    out = [(rel, os.path.join(app_dir, rel)) for rel in app_files(app_dir)]
    if backend_dir is not None:
        if any(rel.startswith("backend/") for rel, _ in out):
            raise ValueError("%s already has a backend/ directory" % app_dir)
        for name in BACKEND_FILES:
            path = os.path.join(backend_dir, name)
            if not os.path.isfile(path):
                raise ValueError("backend file '%s' not found in %s" % (name, backend_dir))
            out.append(("backend/" + name, path))
    return sorted(out)


def _tar_gz(entries):
    # entries: [(имя, None | bytes)] - None = каталог
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in entries:
            ti = tarfile.TarInfo(name)
            ti.mtime = 0
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            if data is None:
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                tar.addfile(ti)
            else:
                ti.mode = 0o644
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as gz:
        gz.write(raw.getvalue())
    return out.getvalue()


def _ar_member(name, data):
    header = "%-16s%-12s%-6s%-6s%-8s%-10s`\n" % (name, "0", "0", "0", "100644", len(data))
    return header.encode("ascii") + data + (b"\n" if len(data) % 2 else b"")


def build(app_dir, out_path, backend_dir=None):
    info = read_appinfo(app_dir)
    app_id, version = info["id"], info["version"]
    if info["main"] not in app_files(app_dir):
        raise ValueError("appinfo.json: main file '%s' not found" % info["main"])
    payload = []
    for rel, path in package_files(app_dir, backend_dir):
        with open(path, "rb") as f:
            payload.append((rel, f.read()))
    files = [rel for rel, _ in payload]
    package_info = {
        "app": app_id,
        "id": app_id,
        "loc_name": info["title"],
        "package_format_version": 2,
        "vendor": info.get("vendor", ""),
        "version": version,
    }
    pkg_json = (json.dumps(package_info, indent=4, sort_keys=True) + "\n").encode("utf-8")
    apps = "./usr/palm/applications/%s/" % app_id
    pkgs = "./usr/palm/packages/%s/" % app_id
    entries = [("./", None), ("./usr/", None), ("./usr/palm/", None), ("./usr/palm/applications/", None), (apps, None)]
    subdirs = sorted({rel.rsplit("/", 1)[0] for rel in files if "/" in rel})
    for d in subdirs:
        parts = d.split("/")
        for i in range(1, len(parts) + 1):
            entry = (apps + "/".join(parts[:i]) + "/", None)
            if entry not in entries:
                entries.append(entry)
    entries += [(apps + rel, data) for rel, data in payload]
    entries += [("./usr/palm/packages/", None), (pkgs, None), (pkgs + "packageinfo.json", pkg_json)]
    size_kb = (sum(len(d) for _, d in payload) + len(pkg_json) + 1023) // 1024
    control = "".join("%s: %s\n" % kv for kv in (
        ("Package", app_id),
        ("Version", version),
        ("Section", "misc"),
        ("Priority", "optional"),
        ("Architecture", "all"),
        ("Installed-Size", str(size_kb)),
        ("Maintainer", MAINTAINER),
        ("Description", info.get("appDescription") or info["title"]),
        ("webOS-Package-Format-Version", "2"),
    )).encode("utf-8")
    ipk = b"!<arch>\n" + _ar_member("debian-binary", b"2.0\n") \
        + _ar_member("control.tar.gz", _tar_gz([("./", None), ("./control", control)])) \
        + _ar_member("data.tar.gz", _tar_gz(entries))
    tmp = out_path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(ipk)
    os.chmod(tmp, 0o644)
    os.rename(tmp, out_path)
    return app_id, version


def same(app_dir, installed_dir, backend_dir=None):
    for rel, path in package_files(app_dir, backend_dir):
        try:
            with open(path, "rb") as a, open(os.path.join(installed_dir, rel), "rb") as b:
                if a.read() != b.read():
                    return False
        except OSError:
            return False
    return True


def main(argv):
    try:
        if len(argv) in (4, 5) and argv[1] == "build":
            print("%s %s" % build(argv[2], argv[3], argv[4] if len(argv) == 5 else None))
            return 0
        if len(argv) == 3 and argv[1] == "info":
            info = read_appinfo(argv[2])
            print("%s %s" % (info["id"], info["version"]))
            return 0
        if len(argv) in (4, 5) and argv[1] == "same":
            return 0 if same(argv[2], argv[3], argv[4] if len(argv) == 5 else None) else 1
    except (OSError, ValueError) as e:
        print("ipk: %s" % e, file=sys.stderr)
        return 1
    print("usage: ipk.py build <APP_DIR> <OUT.ipk> [<BACKEND_DIR>] | info <APP_DIR> | "
          "same <APP_DIR> <DIR> [<BACKEND_DIR>]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
