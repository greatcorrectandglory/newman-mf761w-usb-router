"""Operate a previously authorized MF761W over USB. Node contents are never printed."""
import argparse
import hashlib
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

BASE = Path(__file__).resolve().parents[1]
LOCAL = BASE / ".local"
APP = "/etc_rw/newman-usb"
RAM = "/tmp/newman-usb"
ALIASES = ("US3", "US2", "TEST1", "SPEED")
SCRIPTS = ("manager.sh", "service.sh", "network.sh", "dhcp.sh", "resolve.sh", "firewall.v4", "firewall.v6")
BINARIES = ("check", "forward", "zram")
STOP = LOCAL / "stopped"


def check_core(path):
    with path.open("rb") as stream:
        header = stream.read(52)
    if len(header) != 52 or header[:6] != b"\x7fELF\x01\x01" or header[18:20] != b"\x28\x00":
        raise ValueError("Expected a 32-bit little-endian ARM core")


def validate_profiles(directory):
    paths = {}
    for name in ALIASES:
        path = directory / name
        if not path.is_file() or path.stat().st_size > 4096:
            raise ValueError(name + " profile missing or oversized")
        try:
            text = path.read_bytes().decode("ascii")
            if any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in text):
                raise ValueError()
            uri = urlsplit(text)
            UUID(uri.username or "")
            query = parse_qs(uri.query, keep_blank_values=True)
            if (uri.scheme != "vless" or uri.password is not None or not uri.hostname
                    or not re.fullmatch(r"[a-zA-Z0-9.-]+", uri.hostname) or not uri.port
                    or uri.path or uri.fragment or query.get("security") != ["reality"]
                    or not query.get("sni", [""])[0] or not query.get("pbk", [""])[0]
                    or query.get("type", ["tcp"])[0] not in ("tcp", "raw")
                    or query.get("transport", ["tcp"])[0] not in ("tcp", "raw")
                    or query.get("flow", [""])[0] not in ("", "xtls-rprx-vision")):
                raise ValueError()
        except (ValueError, UnicodeError, IndexError):
            raise ValueError(name + " must be a single-line IPv4/hostname VLESS REALITY TCP URI") from None
        paths[name] = path
    return paths


class Gateway:
    def __init__(self, adb, serial, port):
        binary = adb or shutil.which("adb")
        if not binary:
            raise ValueError("Set MF761W_ADB or put Android platform-tools on PATH")
        self.base = [str(binary), "-P", str(port)]
        self.device = self.base + ["-s", serial]

    def call(self, *args, timeout=35):
        result = subprocess.run(self.device + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        if result.returncode:
            raise RuntimeError("USB command failed; check Android Platform Tools and the specified device")
        return result.stdout.decode(errors="replace").replace("\r", "").strip()

    def shell(self, command):
        txt = self.call("shell", command + "; r=$?; echo MF761W_RC=$r")
        lines = txt.splitlines()
        if not lines or lines[-1] != "MF761W_RC=0":
            raise RuntimeError("The gateway rejected an operation")
        return "\n".join(lines[:-1]).strip()

    def identify(self):
        marker = self.shell("cat /etc/lean-linux-release")
        if not marker.startswith("MF761W P005 stock-kernel rootfs candidate 20261012"):
            raise RuntimeError("Expected the documented MF761W P005 base image")

    def push(self, path, destination, mode):
        tmp = destination + ".new"
        self.call("push", str(path), tmp)
        if self.shell("md5sum " + tmp).split()[0] != hashlib.md5(path.read_bytes()).hexdigest():
            raise RuntimeError("USB readback checksum mismatch")
        self.shell("chmod " + mode + " " + tmp + " && mv " + tmp + " " + destination)

    def status(self):
        return self.shell(APP + "/service.sh status")

    def activate(self, core):
        check_core(core)
        self.identify()
        status = self.status()
        if not status.startswith("offline - stopped"):
            return status
        now = datetime.now(timezone.utc)
        if abs(int(self.shell("date -u +%s")) - now.timestamp()) > 90:
            self.shell("date -u -s '" + now.strftime("%Y-%m-%d %H:%M:%S") + "'")
        self.shell("mkdir -p " + RAM + " && chmod 700 " + RAM)
        current = self.shell("if test -x " + RAM + "/core; then md5sum " + RAM + "/core; fi")
        if current.split()[:1] != [hashlib.md5(core.read_bytes()).hexdigest()]:
            self.push(core, RAM + "/core", "700")
        self.shell(APP + "/service.sh start")
        return self.status()

    def install(self, core, nodes):
        files = validate_profiles(nodes)
        check_core(core)
        self.identify()
        self.shell("umask 077; mkdir -p " + APP + "/nodes && chmod 700 " + APP + " " + APP + "/nodes")
        STOP.touch()
        self.shell("if test -x " + APP + "/service.sh; then " + APP + "/service.sh stop; fi")
        for name in SCRIPTS:
            self.push(BASE / "device" / name, APP + "/" + name, "600" if name.startswith("firewall.") else "700")
        for name in BINARIES:
            self.push(BASE / "device/bin" / name, APP + "/" + name, "700")
        for name, path in files.items():
            self.push(path, APP + "/nodes/" + name, "600")
        self.shell("iptables-restore --test < " + APP + "/firewall.v4 && ip6tables-restore --test < " + APP + "/firewall.v6")
        STOP.unlink(missing_ok=True)
        return self.activate(core)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action", choices=("validate", "install", "start", "status", "stop", "restore", "watch"))
    cli.add_argument("--serial", default=os.environ.get("MF761W_SERIAL"))
    cli.add_argument("--adb", default=os.environ.get("MF761W_ADB"))
    cli.add_argument("--port", type=int, default=15037)
    cli.add_argument("--core", type=Path, default=LOCAL / "core-arm")
    cli.add_argument("--nodes", type=Path, default=LOCAL / "nodes")
    args = cli.parse_args()
    os.environ.setdefault("ANDROID_USER_HOME", str(LOCAL / "adb-state"))
    if args.action == "validate":
        validate_profiles(args.nodes)
        check_core(args.core)
        print("Four profiles and the ARM runtime passed local checks")
        return
    if not args.serial:
        cli.error("Set MF761W_SERIAL to the intended authorized device or pass --serial")
    LOCAL.mkdir(exist_ok=True)
    gateway = Gateway(args.adb, args.serial, args.port)
    if args.action == "watch":
        while True:
            if not STOP.exists():
                try:
                    gateway.activate(args.core)
                except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
                    pass
            time.sleep(8)
    elif args.action == "install":
        print(gateway.install(args.core, args.nodes))
    elif args.action == "start":
        gateway.identify()
        STOP.unlink(missing_ok=True)
        print(gateway.activate(args.core))
    else:
        gateway.identify()
        if args.action == "status":
            print(gateway.status())
        else:
            STOP.touch()
            print(gateway.shell(APP + "/service.sh stop"))
            if args.action == "restore":
                gateway.shell(APP + "/network.sh restore")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
