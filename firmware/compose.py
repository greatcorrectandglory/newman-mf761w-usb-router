"""Build the public P005 root filesystem overlay and apply it to a verified stock MTD4."""
import argparse
import copy
import hashlib
import io
import json
import os
import re
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STOCK_SHA = "b21b331da0a6376b18c1af97eb7785838e2695ad0ae85db474ca15cf8312c17f"
SIZE = 3244032
EPOCH = 1686905016
PATCH = ROOT / "firmware" / "mf761w-p005-usb-router.mfp"
MARKER = b"MF761W P005 stock-kernel rootfs candidate 20261012 (public USB overlay v1)\n"
SCRIPTS = ("dhcp.sh", "manager.sh", "network.sh", "resolve.sh", "service.sh", "firewall.v4", "firewall.v6")
EXECUTABLES = ("check", "forward", "zram")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def content():
    files = {"etc/lean-linux-release": (MARKER, 0o644),
             "etc_ro/web/index.html": ((ROOT / "firmware/index.html").read_bytes(), 0o644)}
    for name in SCRIPTS:
        files["usr/share/newman-usb/" + name] = ((ROOT / "device" / name).read_bytes(),
                                                 0o600 if name.startswith("firewall.") else 0o700)
    for name in EXECUTABLES:
        files["usr/share/newman-usb/" + name] = ((ROOT / "device/bin" / name).read_bytes(), 0o700)
    return files


def make_patch():
    # The public archive contains only project-owned changes and the three device utilities.
    # Factory system files are read from the owner's local stock backup at composition time.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tar:
        for path, (data, mode) in sorted(content().items()):
            member = tarfile.TarInfo(path)
            member.size, member.mode, member.mtime = len(data), mode, EPOCH
            tar.addfile(member, io.BytesIO(data))
    import lzma
    return lzma.compress(buf.getvalue(), preset=9)


def unpack_patch(path):
    expected = content()
    with tarfile.open(path, "r:xz") as tar:
        members = tar.getmembers()
        if len(members) != len(expected) or {m.name for m in members} != set(expected):
            raise ValueError("Unexpected firmware overlay entries")
        for m in members:
            data, mode = expected[m.name]
            if not m.isfile() or m.uid or m.gid or m.mode != mode or tar.extractfile(m).read() != data:
                raise ValueError("Overlay readback differs for " + m.name)
    return expected


def tool(directory, name):
    exe = name + (".exe" if os.name == "nt" else "")
    path = directory / exe if directory else shutil.which(exe)
    if not path or not Path(path).is_file():
        raise ValueError("Missing squashfs-tools-ng executable " + exe)
    return str(path)


def normalized(name):
    return name[2:] if name.startswith("./") else name


def index(path):
    entries = {}
    with tarfile.open(path) as tar:
        for m in tar:
            n = normalized(m.name)
            if n.startswith("/") or ".." in n.split("/") or n in entries:
                raise ValueError("Invalid archive member")
            entries[n] = (m.type, m.mode, m.uid, m.gid, m.mtime, m.linkname, m.devmajor, m.devminor,
                          digest(tar.extractfile(m).read()) if m.isfile() else None)
    return entries


def convert(toolname, archive_path, target):
    with target.open("wb") as stream:
        subprocess.run([toolname, "-r", ".", "--no-skip", str(archive_path)], stdout=stream, check=True)


PUBLIC_EMPTY = set("""
at_wifi_mac wifi_mac m_wifi_mac ex_mac serialnumber serial_tsp usb_serial_num_debug usb_serial_num_user
usb_serial_num_factory usb_serial_num_amt zwifikey ssid1 ex_ssid1 m_ssid zssid wps_ssid
admin_password root_password current_password key1str1 key2str1 key3str1 key4str1 m_key1str1
m_key2str1 m_key3str1 m_key4str1 wpapsk1_encode wpapsk1_aes m_wpapsk1_encode m_wpapsk1_aes
ex_wpapsk1 ex_wepkey appkeymobile fota_token_rs fota_token_zx fota_token_gs fota_product_secret
fota_product_id lpa_appid lpa_appsecret apn_auto_config apn_config0 lpa_bpp_iccid
""".split())


def public_entry(name, data):
    # The USB routing feature reads none of these factory provisioning credentials.
    if name == "etc/shadow":
        return re.sub(br"(?m)^([^:\r\n]+):[^:\r\n]*:", br"\1:!:", data)
    if name in ("etc_ro/pap-secrets", "etc_ro/chap-secrets"):
        return b"# PPP credentials are provisioned locally.\n"
    if name in ("etc_ro/default/default_parameter_user", "etc_ro/default/default_parameter_sys"):
        lines = []
        for line in data.splitlines(keepends=True):
            key, sep, rest = line.partition(b"=")
            label = key.strip().decode("ascii", errors="ignore").lower()
            if sep and (label in PUBLIC_EMPTY or any(term in label for term in ("passwd", "password", "secret", "fota_token"))):
                line = key + b"=" + (b"\n" if rest.endswith(b"\n") else b"")
            lines.append(line)
        return b"".join(lines)
    return data


def compose(stock, overlay, directory, output, public_image=False):
    raw = stock.read_bytes()
    if len(raw) != SIZE or digest(raw) != STOCK_SHA:
        raise ValueError("Expected the documented P005 stock MTD4 length and SHA-256")
    if output.exists():
        raise FileExistsError("Choose a new output path")
    additions = unpack_patch(overlay)
    unpacker, packer = (tool(directory, name) for name in ("sqfs2tar", "tar2sqfs"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as work:
        tmp = Path(work)
        original, updated, back, image = (tmp / n for n in ("stock.tar", "candidate.tar", "readback.tar", "rootfs.squashfs"))
        convert(unpacker, stock, original)
        baseline = index(original)
        with tarfile.open(original) as src, tarfile.open(updated, "w", format=tarfile.PAX_FORMAT) as dst:
            for m in src:
                n = normalized(m.name)
                if n.startswith("etc_ro/web/") or public_image and n in (
                    "etc_ro/config/auto_apn/auto_apn.db", "etc_ro/ap_userdata.img", "etc/firmware/aic_macconfig.txt"
                ):
                    continue
                record = copy.copy(m)
                data = src.extractfile(m).read() if m.isfile() else None
                if public_image and data is not None:
                    data = public_entry(n, data)
                    record.size = len(data)
                dst.addfile(record, io.BytesIO(data) if data is not None else None)
            for n in ("usr", "usr/share", "usr/share/newman-usb"):
                if n in baseline:
                    if baseline[n][0] != tarfile.DIRTYPE:
                        raise ValueError("Unexpected type for " + n)
                    continue
                m = tarfile.TarInfo("./" + n)
                m.type, m.mode, m.mtime = tarfile.DIRTYPE, 0o755, EPOCH
                dst.addfile(m)
            for n, (data, mode) in sorted(additions.items()):
                if n in baseline and not n.startswith("etc_ro/web/"):
                    raise ValueError("Unexpected pre-existing file " + n)
                m = tarfile.TarInfo("./" + n)
                m.size, m.mode, m.mtime = len(data), mode, EPOCH
                dst.addfile(m, io.BytesIO(data))
        with updated.open("rb") as stream:
            subprocess.run([packer, "--no-skip", "--no-xattr", "--exportable", "-c", "xz", "-X",
                            "dictsize=131072,level=9", "-b", "131072", "-j", "2", "-Q", "4", str(image)],
                           stdin=stream, stdout=subprocess.DEVNULL, env=dict(os.environ, SOURCE_DATE_EPOCH=str(EPOCH)), check=True)
        payload = image.read_bytes()
        if payload[:4] != b"hsqs" or not 0 < struct.unpack_from("<Q", payload, 40)[0] <= len(payload) <= SIZE:
            raise ValueError("Invalid or oversized MTD4 filesystem")
        convert(unpacker, image, back)
        if index(updated) != index(back):
            raise ValueError("Full SquashFS readback differs from the intended archive")
        image.replace(output)
    return {"variant": "public" if public_image else "owner-local", "image_bytes": len(payload),
            "image_sha256": digest(payload), "stock_sha256": STOCK_SHA,
            "partition_bytes": SIZE, "overlay_sha256": digest(overlay.read_bytes()), "roundtrip": True}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="action", required=True)
    sub.add_parser("pack")
    sub.add_parser("check")
    cmd = sub.add_parser("compose")
    cmd.add_argument("--stock", type=Path, required=True)
    cmd.add_argument("--tools", type=Path)
    cmd.add_argument("--output", type=Path, required=True)
    cmd.add_argument("--public-image", action="store_true", help="Omit factory credentials and cellular provisioning")
    args = cli.parse_args()
    if args.action == "pack":
        PATCH.write_bytes(make_patch())
    elif args.action == "compose":
        print(json.dumps(compose(args.stock, PATCH, args.tools, args.output, args.public_image), indent=2))
        return
    files = unpack_patch(PATCH)
    print("Verified", len(files), "entries; SHA-256", digest(PATCH.read_bytes()))


if __name__ == "__main__":
    main()
