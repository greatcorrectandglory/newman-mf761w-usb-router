"""Rebuild Windows x64 and ARM binaries and make an allowlisted, deterministic extension ZIP."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
EXT=ROOT/"extension"
FILES=("VERSION","Hotspot.ps1","Start-Here.cmd","Setup.ps1","README.md","host/service.py","host/device.py",
       "host/profiles.py","host/worker_job.py","device/ap.sh","device/dhcp.conf","device/filter.v4","device/filter.v6")
NOTICES=("Go","tun2socks","gVisor","btree","x-exp","x-sys","x-time","musl")
def digest(b):
    return hashlib.sha256(b).hexdigest()

def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--go",default="go")
    cli.add_argument("--zig",default="zig")
    cli.add_argument("--output",required=True,type=Path)
    args=cli.parse_args()
    dest=args.output.resolve()
    dest.mkdir(exist_ok=True,parents=True)
    version=(EXT/"VERSION").read_text().strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+",version):
        raise ValueError("Invalid version file")
    payload={n:(EXT/n).read_bytes() for n in (*FILES,*("notices/"+x+".txt" for x in NOTICES))}
    with tempfile.TemporaryDirectory(prefix="newman-release-") as temp:
        binary=Path(temp)/"gateway.exe"
        env=dict(os.environ,CGO_ENABLED="0",GOOS="windows",GOARCH="amd64",GOWORK="off")
        for cmd in (["mod","verify"],["test","./..."],["vet","./..."],
                    ["build","-trimpath","-buildvcs=false","-ldflags=-s -w","-o",str(binary),"."]):
            subprocess.run([args.go,*cmd],env=env,cwd=EXT/"gateway",check=True)
        payload["gateway.exe"]=binary.read_bytes()
        relay=Path(temp)/"relay"
        subprocess.run([args.zig,"cc","-target","arm-linux-musleabi","-mcpu=arm1176jz_s","-mfloat-abi=soft",
                        "-Oz","-fno-sanitize=all","-static","-s","-Wl,--build-id=none","device/relay.c","-o",str(relay)],
                        cwd=EXT,check=True)
        payload["device/relay"]=relay.read_bytes()
    if payload["device/relay"][:6]!=b"\x7fELF\x01\x01" or payload["device/relay"][18:20]!=b"\x28\x00":
        raise ValueError("Unexpected target architecture")
    pe=payload["gateway.exe"]
    offset=int.from_bytes(pe[60:64],"little")
    if pe[:2]!=b"MZ" or pe[offset:offset+6]!=b"PE\0\0\x64\x86":
        raise ValueError("Expected x64 PE")
    manifest={"format":1,"version":version,"files":[{"path":n,"sha256":digest(data)} for n,data in sorted(payload.items())]}
    payload["MANIFEST.json"]=(json.dumps(manifest,indent=2)+"\n").encode()
    artifact=dest/("newman-hotspot-windows-x64-v"+version+".zip")
    with zipfile.ZipFile(artifact,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for n,data in sorted(payload.items()):
            item=zipfile.ZipInfo("NewmanHotspot/"+n,(2026,11,14,0,0,0))
            item.create_system=3
            item.external_attr=(0o100755 if n in ("device/relay","device/ap.sh") else 0o100644)<<16
            z.writestr(item,data,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    with zipfile.ZipFile(artifact) as z:
        if z.testzip() or {n.split("/",1)[1]:z.read(n) for n in z.namelist()}!=payload:
            raise RuntimeError("Release readback failed")
    (dest/"SHA256SUMS.txt").write_bytes((digest(artifact.read_bytes())+"  "+artifact.name+"\n").encode("ascii"))
    print(artifact.name,artifact.stat().st_size,digest(artifact.read_bytes()))

if __name__=="__main__":
    main()
