"""Create the v1.0.0 release from the committed tree and the validated public image."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "newman-mf761w-p005-usb-router-v1.0.0"
IMAGE = "mf761w-p005-mtd4-public.squashfs"
PATCH = "mf761w-p005-usb-router.mfp"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--revision", default="HEAD")
    cli.add_argument("--image", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    manifest = {}
    snapshot = subprocess.check_output(["git", "-C", str(ROOT), "archive", "--format=tar", args.revision])
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as tar:
        for member in tar:
            if not member.isfile():
                continue
            name = PurePosixPath(member.name)
            if name.is_absolute() or set(name.parts) & {"..", ".git", ".local", "private", "__pycache__"}:
                raise ValueError("Unexpected path in the source revision")
            manifest[member.name] = tar.extractfile(member).read()
    expected = json.loads(manifest["evidence/firmware.json"])["public_rootfs"]
    image = args.image.read_bytes()
    if sha(image) != expected["image_sha256"] or len(image) != expected["image_bytes"]:
        raise ValueError("Public firmware image differs from the tested release manifest")
    patch = manifest["firmware/" + PATCH]
    if sha(patch) != expected["overlay_sha256"]:
        raise ValueError("Public overlay checksum differs")
    manifest["firmware/" + IMAGE] = image
    checks = "".join(sha(b) + "  " + n + "\n" for n, b in sorted(manifest.items()))
    manifest["SHA256SUMS.txt"] = checks.encode("utf-8")
    args.output.mkdir(parents=True, exist_ok=True)
    bundle = args.output / (PREFIX + ".zip")
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in sorted(manifest.items()):
            item = zipfile.ZipInfo(PREFIX + "/" + name, (2026, 11, 13, 0, 0, 0))
            item.create_system = 3
            executable = name.startswith("device/bin/") or name.endswith(".sh")
            item.external_attr = (0o100755 if executable else 0o100644) << 16
            z.writestr(item, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(bundle) as z:
        if z.testzip() or {n.split("/", 1)[1]: z.read(n) for n in z.namelist()} != manifest:
            raise RuntimeError("Public release ZIP readback failed")
    for name, data in ((IMAGE, image), (PATCH, patch)):
        (args.output / name).write_bytes(data)
    sums = "".join(sha((args.output / n).read_bytes()) + "  " + n + "\n" for n in (bundle.name, IMAGE, PATCH))
    (args.output / "SHA256SUMS.txt").write_bytes(sums.encode("ascii"))
    print("Verified", len(manifest), "files in", bundle.name)


if __name__ == "__main__":
    main()
