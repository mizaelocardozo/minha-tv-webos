#!/usr/bin/env python3
"""
Minha TV (baseado no Free My TV for webOS) - Build Script
Packages the webOS application into a distributable .ipk file
"""

import os
import sys
import json
import shutil
import subprocess
import time

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_DIR, "app")
DIST_DIR = os.path.join(REPO_DIR, "dist")

def read_appinfo():
    with open(os.path.join(APP_DIR, "appinfo.json"), "r", encoding="utf-8") as f:
        info = json.load(f)
    return info["id"], info["version"]

def ipk_filename(app_id, version, profile=None):
    suffix = f"_{profile}" if profile else ""
    return f"{app_id}_{version}{suffix}_all.ipk"


def stage_profile(profile):
    """Copia app/ para uma pasta temporária e sobrepõe os arquivos de profiles/<perfil>/
    (profile.js e fotos em assets/). Devolve o caminho da cópia."""
    import tempfile
    src = os.path.join(REPO_DIR, "profiles", profile)
    if not os.path.isfile(os.path.join(src, "profile.js")):
        sys.exit(f"[!] Perfil não encontrado: {src}/profile.js")
    stage = os.path.join(tempfile.mkdtemp(prefix="minhatv-"), "app")
    shutil.copytree(APP_DIR, stage)
    # Fotos de uma TV não vão no pacote da outra.
    for name in os.listdir(os.path.join(stage, "assets")):
        if name.startswith("tuti"):
            os.remove(os.path.join(stage, "assets", name))
    shutil.copytree(src, stage, dirs_exist_ok=True)
    # appinfo.override.json do perfil (ex.: outro nome na lista de apps) entra no appinfo.json.
    override = os.path.join(stage, "appinfo.override.json")
    if os.path.exists(override):
        with open(os.path.join(stage, "appinfo.json"), encoding="utf-8") as f:
            info = json.load(f)
        with open(override, encoding="utf-8") as f:
            info.update(json.load(f))
        with open(os.path.join(stage, "appinfo.json"), "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)
        os.remove(override)
    return stage

def _fix_ipk_timestamps(ipk_path):
    """Rewrite epoch-zero mtimes inside control.tar.gz/data.tar.gz.

    ares-package (current @webosose/ares-cli lineage, including the 2.4.0
    used here) bakes 1970-01-01 mtimes into every tar entry it writes.
    webOS 5.x's on-device pkgverifier shells out to tar to extract those
    archives, and treats GNU tar's "implausibly old time stamp" warning on
    epoch-zero entries as a fatal error ("-5: ipk verified failed"), even
    though older webOS versions installed the exact same package fine.
    Rewriting every entry's mtime to the build time avoids the warning
    without depending on ares-package (or any other tool) fixing it upstream.
    """
    import tarfile
    import io
    import time

    with open(ipk_path, "rb") as f:
        raw = f.read()
    if raw[:8] != b"!<arch>\n":
        return  # Not a standard ar archive; leave untouched.

    now = int(time.time())
    offset = 8
    entries = []
    while offset < len(raw):
        header = raw[offset:offset + 60]
        if len(header) < 60:
            break
        name = header[0:16].decode("ascii").strip()
        size = int(header[48:58].decode("ascii").strip())
        content_start = offset + 60
        content = raw[content_start:content_start + size]
        entries.append((name, content))
        offset = content_start + size
        if offset % 2 == 1:
            offset += 1

    def retar(gz_bytes):
        src = tarfile.open(fileobj=io.BytesIO(gz_bytes), mode="r:gz")
        out_buf = io.BytesIO()
        with tarfile.open(fileobj=out_buf, mode="w:gz", format=tarfile.GNU_FORMAT) as dst:
            for member in src.getmembers():
                member.mtime = now
                if member.isfile():
                    dst.addfile(member, src.extractfile(member))
                else:
                    dst.addfile(member)
        return out_buf.getvalue()

    with open(ipk_path, "wb") as f:
        f.write(b"!<arch>\n")
        for name, content in entries:
            if name in ("control.tar.gz", "data.tar.gz"):
                content = retar(content)
            hdr = f"{name:<16}{now:<12}{'0':<6}{'0':<6}{'100644':<8}{len(content):<10}`\n".encode("latin1")
            f.write(hdr)
            f.write(content)
            if len(content) % 2 != 0:
                f.write(b"\n")

def build(profile=None):
    global APP_DIR
    os.makedirs(DIST_DIR, exist_ok=True)
    app_id, version = read_appinfo()
    if profile:
        APP_DIR = stage_profile(profile)
    print(f"[*] Packaging Free My TV from {APP_DIR}...")
    ipk_path = os.path.join(DIST_DIR, ipk_filename(app_id, version, profile))

    # Check if ares-package is available (it names the file itself, so profiles use the Python packager)
    has_ares = shutil.which("ares-package") is not None and not profile
    built = False
    if has_ares:
        cmd = f'ares-package "{APP_DIR}" -o "{DIST_DIR}"'
        print(f"[*] Running: {cmd}")
        res = subprocess.run(cmd, shell=True)
        if res.returncode == 0:
            built = True
        else:
            print("[!] ares-package failed, trying fallback packager...")

    if built:
        print("[*] Fixing epoch-zero tar timestamps (webOS 5.x install fix)...")
        _fix_ipk_timestamps(ipk_path)
        print(f"[+] Successfully built package in {DIST_DIR}!")
        return True

    # Fallback pure-python packager if ares-package is missing
    print("[*] Using built-in Python IPK packager...")
    import tarfile
    import io

    control_content = f"""Package: {app_id}
Version: {version}
Section: misc
Priority: optional
Architecture: all
Maintainer: Mizael
Description: A fast, ad-free 60fps home screen for LG webOS TVs.
"""
    # Mesmo formato do ares-package: tar clássico (GNU, sem cabeçalhos PAX), caminhos "./",
    # diretórios incluídos e dono root. webOS antigos (ex.: webOS 3.x / TV Lite) recusam o
    # pacote em "service installing" quando o tar foge disso.
    now = int(time.time())

    def tar_entry(tar, name, data=None, is_dir=False):
        ti = tarfile.TarInfo(name=name)
        ti.mtime = now
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = "root"
        if is_dir:
            ti.type = tarfile.DIRTYPE
            ti.mode = 0o755
            tar.addfile(ti)
        else:
            ti.mode = 0o644
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))

    # 1. control.tar.gz
    control_buf = io.BytesIO()
    with tarfile.open(fileobj=control_buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        tar_entry(tar, "./", is_dir=True)
        tar_entry(tar, "./control", control_content.encode("utf-8"))
    control_bytes = control_buf.getvalue()

    # 2. data.tar.gz
    with open(os.path.join(APP_DIR, "appinfo.json"), encoding="utf-8") as f:
        title = json.load(f).get("title", app_id)
    packageinfo = json.dumps({"app": app_id, "id": app_id, "loc_name": title, "package_format_version": 2,
                              "vendor": "Mizael", "version": version}, indent=2).encode("utf-8")
    data_buf = io.BytesIO()
    with tarfile.open(fileobj=data_buf, mode="w:gz", format=tarfile.GNU_FORMAT) as tar:
        app_base = f"./usr/palm/applications/{app_id}"
        pkg_base = f"./usr/palm/packages/{app_id}"
        for d in ["./", "./usr/", "./usr/palm/", "./usr/palm/applications/", app_base + "/",
                  "./usr/palm/packages/", pkg_base + "/"]:
            tar_entry(tar, d, is_dir=True)
        tar_entry(tar, pkg_base + "/packageinfo.json", packageinfo)
        for root, dirs, files in os.walk(APP_DIR):
            dirs.sort()
            rel_root = os.path.relpath(root, APP_DIR).replace(os.sep, "/")
            if rel_root != ".":
                tar_entry(tar, f"{app_base}/{rel_root}/", is_dir=True)
            for file in sorted(files):
                rel_p = os.path.relpath(os.path.join(root, file), APP_DIR).replace(os.sep, "/")
                with open(os.path.join(root, file), "rb") as f:
                    tar_entry(tar, f"{app_base}/{rel_p}", f.read())
    data_bytes = data_buf.getvalue()

    # 3. debian-binary
    debian_binary = b"2.0\n"

    # 4. Standard ar archive
    with open(ipk_path, "wb") as f:
        f.write(b"!<arch>\n")
        def write_ar_entry(name, data):
            hdr = f"{name:<16}{'0':<12}{'0':<6}{'0':<6}{'100644':<8}{len(data):<10}`\n".encode("latin1")
            f.write(hdr)
            f.write(data)
            if len(data) % 2 != 0:
                f.write(b"\n")
        write_ar_entry("debian-binary", debian_binary)
        write_ar_entry("control.tar.gz", control_bytes)
        write_ar_entry("data.tar.gz", data_bytes)

    _fix_ipk_timestamps(ipk_path)
    print(f"[+] Successfully built fallback IPK: {ipk_path}")
    return True

if __name__ == "__main__":
    # python scripts/build.py                     -> esta TV (app/profile.js)
    # python scripts/build.py --profile alexania  -> perfil em profiles/alexania/
    args = sys.argv[1:]
    build(args[args.index("--profile") + 1] if "--profile" in args else None)
