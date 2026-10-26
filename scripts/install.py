#!/usr/bin/env python3
"""
Minha TV (baseado no Free My TV for webOS) - One-Command Installer
Automates discovery, pairing, and installation onto your LG Smart TV
"""

import os
import sys
import json
import socket
import urllib.request
import subprocess
import shutil

# Windows' default console codepage can't encode the emoji in the success
# message below; UTF-8 output works on every platform this script runs on.
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Chave TMDB fora do repositório: o app recebe nos parâmetros de abertura e guarda na TV.
TMDB_KEY_FILE = os.path.expanduser("~/.minhatv/tmdb_key")

def read_tmdb_key():
    key = os.environ.get("MINHATV_TMDB_KEY", "").strip()
    if not key and os.path.exists(TMDB_KEY_FILE):
        with open(TMDB_KEY_FILE, "r", encoding="utf-8") as f:
            key = f.read().strip()
    return key

with open(os.path.join(REPO_DIR, "app", "appinfo.json"), "r", encoding="utf-8") as _f:
    _appinfo = json.load(_f)
APP_ID = _appinfo["id"]
DIST_IPK = os.path.join(REPO_DIR, "dist", f"{APP_ID}_{_appinfo['version']}_all.ipk")


def profile_ipk(profile):
    return os.path.join(REPO_DIR, "dist", f"{APP_ID}_{_appinfo['version']}_{profile}_all.ipk")

def discover_lg_tv():
    print("[*] Procurando a TV LG (Web TV NANO9TSA) na rede local...")
    msg = (
        'M-SEARCH * HTTP/1.1\r\n'
        'HOST: 239.255.255.250:1900\r\n'
        'MAN: "ssdp:discover"\r\n'
        'MX: 2\r\n'
        'ST: urn:schemas-upnp-org:device:MediaRenderer:1\r\n\r\n'
    ).encode('utf-8')
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.settimeout(2.5)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    try:
        sock.sendto(msg, ('239.255.255.250', 1900))
        while True:
            data, addr = sock.recvfrom(4096)
            ip = addr[0]
            # Probe dev port 9922
            s = socket.socket()
            s.settimeout(0.3)
            if s.connect_ex((ip, 9922)) == 0:
                s.close()
                sock.close()
                return ip
            s.close()
    except Exception:
        pass
    sock.close()
    return None

def install_app(tv_ip, passphrase=None, profile=None):
    ipk = profile_ipk(profile) if profile else DIST_IPK
    if not os.path.exists(ipk):
        print("[!] Pacote não encontrado. Gerando primeiro...")
        build_script = os.path.join(REPO_DIR, "scripts", "build.py")
        subprocess.run([sys.executable, build_script] + (["--profile", profile] if profile else []))

    ssh_dir = os.path.expanduser("~/.ssh")
    os.makedirs(ssh_dir, exist_ok=True)
    # Uma chave por TV: cada TV gera a sua no Developer Mode.
    raw_key = os.path.join(ssh_dir, f"webos_tv_{tv_ip}_rsa")
    dec_key = os.path.join(ssh_dir, f"webos_tv_{tv_ip}_rsa_decrypted")

    # Download key from TV if not present
    if not os.path.exists(dec_key):
        if not passphrase:
            passphrase = input(f"Digite a senha de 6 caracteres mostrada no Developer Mode da TV ({tv_ip}): ").strip()

        print(f"[*] Baixando a chave de autenticação de http://{tv_ip}:9991/webos_rsa ...")
        try:
            with urllib.request.urlopen(f"http://{tv_ip}:9991/webos_rsa", timeout=5) as resp:
                key_data = resp.read()
                fd = os.open(raw_key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(key_data)
                os.chmod(raw_key, 0o600)
        except Exception as e:
            print(f"[!] Falha ao obter a chave: {e}")
            print("[!] Confira se o 'Key Server' está ligado (ON) no app Developer Mode da TV.")
            return False

        # Decrypt key
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_private_key(key_data, password=passphrase.encode())
        unenc = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )
        # Private keys must be owner-only: other local users could otherwise read
        # them, and OpenSSH refuses to use a key with group/other permissions.
        fd = os.open(dec_key, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(unenc)
        os.chmod(dec_key, 0o600)
        print("[+] Chave SSH salva com sucesso.")

    print(f"[*] Enviando a Minha TV para a TV ({tv_ip})...")
    # List-form args (no shell=True) throughout: on Windows, shell=True runs
    # the command through cmd.exe regardless of which shell launched Python,
    # and cmd.exe mangles the embedded double quotes in the JSON payloads
    # below before ssh.exe ever sees them. Passing argv as a list sidesteps
    # every local shell's quoting rules; the single remote command string
    # still reaches the TV's real POSIX shell exactly as written.
    ssh_opts = ["-o", "StrictHostKeyChecking=no", "-o", "HostKeyAlgorithms=+ssh-rsa", "-o", "PubkeyAcceptedKeyTypes=+ssh-rsa"]
    scp_args = ["scp", *ssh_opts, "-P", "9922", "-i", dec_key, ipk, f"prisoner@{tv_ip}:/media/developer/temp/app.ipk"]
    res = subprocess.run(scp_args)
    if res.returncode != 0:
        print("[!] Falha no envio (SCP).")
        return False

    # luna-send-pub silently prints nothing at all without a controlling TTY
    # (ssh -tt), and dev/install streams ~15-20 progress replies before the
    # final "installed" status, so a generous -n bound is needed. It's also
    # capped with `timeout` because luna-send-pub keeps the SSH session open
    # waiting for more replies even once the service stops sending any.
    def run_luna(remote_cmd, force_pty=True):
        tty_flags = ["-tt"] if force_pty else []
        args = ["ssh", *tty_flags, *ssh_opts, "-p", "9922", "-i", dec_key, f"prisoner@{tv_ip}", remote_cmd]
        res = subprocess.run(args, capture_output=True, text=True)
        # Some TVs' sshd rejects forced PTY allocation outright ("PTY
        # allocation request failed on channel 0"); ssh then continues the
        # session without one, which starves luna-send-pub of output. Retry
        # once without forcing a PTY so the command itself still goes through.
        if force_pty and "PTY allocation request failed" in (res.stderr or ""):
            print("[*] A TV recusou o PTY; tentando de novo sem ele...")
            # Sem PTY, o luna-send-pub só responde com a entrada vinda de /dev/null e a saída num
            # arquivo; e o BusyBox antigo (webOS 3.x) usa "timeout -t SEGUNDOS".
            out = "/media/developer/temp/luna-out.txt"
            no_pty_cmd = ('if timeout -t 1 true 2>/dev/null; then TO="timeout -t"; else TO="timeout"; fi; '
                          + remote_cmd.replace("timeout ", "$TO ", 1) + f" < /dev/null > {out} 2>&1; cat {out}")
            res = subprocess.run(["ssh", *ssh_opts, "-p", "9922", "-i", dec_key, f"prisoner@{tv_ip}", no_pty_cmd], capture_output=True, text=True)
        return res

    print("[*] Instalando o pacote na TV...")
    install_payload = json.dumps({"id": APP_ID, "ipkUrl": "/media/developer/temp/app.ipk", "subscribe": True})
    install_remote_cmd = f"timeout 45 luna-send-pub -n 30 luna://com.webos.appInstallService/dev/install '{install_payload}'"
    install_res = run_luna(install_remote_cmd)
    install_out = install_res.stdout
    if '"state":"installed"' not in install_out:
        print("[!] A instalação não foi concluída. Saída:")
        print(install_out or "(vazio)")
        if install_res.stderr:
            print(install_res.stderr)
        return False

    print("[*] Abrindo a Minha TV...")
    launch_payload = {"id": APP_ID}
    tmdb_key = read_tmdb_key()
    # Only plain key characters go into the single-quoted remote shell command.
    if tmdb_key and all(c.isalnum() or c in "._-" for c in tmdb_key):
        launch_payload["params"] = {"tmdbKey": tmdb_key}
        print("[*] Enviando a chave TMDB para o catálogo...")
    # Álbum compartilhado do iCloud para as fotos da família (fica fora do repositório).
    album_file = os.path.expanduser("~/.minhatv/family_album.json")
    if not profile and os.path.exists(album_file):
        with open(album_file, encoding="utf-8") as f:
            album = json.load(f)
        if all(c.isalnum() or c in "._-" for c in album.get("key", "") + album.get("host", "")):
            launch_payload.setdefault("params", {})["familyAlbum"] = {"key": album["key"], "host": album["host"]}
            print("[*] Enviando o álbum de fotos da família...")
    launch_payload = json.dumps(launch_payload)
    launch_remote_cmd = f"timeout 15 luna-send-pub -n 1 luna://com.webos.applicationManager/launch '{launch_payload}'"
    launch_res = run_luna(launch_remote_cmd)
    if '"returnValue":true' not in launch_res.stdout:
        print("[!] Instalou, mas não abriu. Saída:")
        print(launch_res.stdout or "(vazio)")
        return False

    print("\n[+] PRONTO! A Minha TV está instalada e rodando na sua LG! 🎉")
    return True

if __name__ == "__main__":
    # python scripts/install.py [IP] [senha] [--profile nome]
    args = sys.argv[1:]
    profile = None
    if "--profile" in args:
        i = args.index("--profile")
        profile = args[i + 1]
        del args[i:i + 2]
    ip = args[0] if len(args) > 0 else None
    passphrase = args[1] if len(args) > 1 else None

    if not ip:
        ip = discover_lg_tv()
        if not ip:
            ip = input("Digite o IP da TV (aparece no app Developer Mode): ").strip()

    install_app(ip, passphrase, profile)
