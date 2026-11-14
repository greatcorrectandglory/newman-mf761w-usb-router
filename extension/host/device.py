"""Operations on a selected, authorized USB device. No configuration contains node secrets."""
import hashlib
import os
from pathlib import Path
import re
import subprocess

BASE="/etc_rw/mf761w-host-ap"
PORT=15037
DEVICE_FILES=("ap.sh","dhcp.conf","filter.v4","filter.v6","relay")
MARKER="MF761W P005 stock-kernel rootfs candidate 20261012"


def usb_devices(adb, serial=None):
    prefix=[str(adb),"-P",str(PORT)]
    env=dict(os.environ,ADB_LIBUSB="1")
    def cmd(*args):
        q=subprocess.run(prefix+list(args),env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                         timeout=12,creationflags=subprocess.CREATE_NO_WINDOW)
        if q.returncode:
            raise RuntimeError("Dedicated ADB server unavailable")
        return q.stdout.decode("utf-8","replace").replace("\r","")
    start=["--one-device",serial,"start-server"] if serial else ["start-server"]
    cmd(*start)
    if "usb_backend: LIBADBUSB" in cmd("server-status"):
        # Port 15037 is reserved for this product. Preserve active sessions on other devices.
        active=[r for r in cmd("devices").splitlines()[1:] if r.strip()]
        if active:
            raise RuntimeError("USB port 15037 already serves a native-backend device")
        cmd("kill-server")
        cmd(*start)
    return [l.split()[0] for l in cmd("devices","-l").splitlines()
            if len(l.split())>=2 and l.split()[1]=="device" and re.fullmatch(r"[A-Za-z0-9._-]{1,100}",l.split()[0])]


class Device:
    def __init__(self, adb, serial):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", serial):
            raise ValueError("Invalid USB serial")
        self.adb=str(adb)
        self.serial=serial
        self.prefix=[self.adb,"-P",str(PORT),"-s",serial]

    def call(self,*args):
        q=subprocess.run(self.prefix+list(args),env=dict(os.environ,ADB_LIBUSB="1"),stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
        if q.returncode:
            raise RuntimeError("Authorized USB device unavailable")
        return q.stdout.decode("utf-8","replace").replace("\r","").strip()

    def shell(self, command):
        lines=self.call("shell",command+"; echo MFAP_RC=$?").splitlines()
        if not lines or lines[-1]!="MFAP_RC=0":
            if "Other Newman gateway active" in lines:
                raise RuntimeError("Another Newman gateway is active. Stop it with its original controls.")
            raise RuntimeError("Device operation failed ("+command.split()[0].rsplit("/",1)[-1]+")")
        return "\n".join(lines[:-1]).strip()

    def identify(self):
        if self.serial not in usb_devices(self.adb,self.serial):
            raise RuntimeError("Waiting for authorized P005 USB connection")
        if not self.shell("cat /etc/lean-linux-release").startswith(MARKER):
            raise RuntimeError("The connected device is not on the supported P005 firmware")
        if self.shell("id -u") != "0":
            raise RuntimeError("Authorized device administration is required")

    def slot(self):
        return self.pointer("current")

    def pointer(self,kind):
        if kind not in ("current","previous"):
            raise ValueError("Invalid version pointer")
        name=self.shell("if test -f "+BASE+"/"+kind+"; then cat "+BASE+"/"+kind+"; fi")
        if name and not re.fullmatch(r"r-[0-9a-f]{12}",name):
            raise RuntimeError("Unexpected device version marker")
        return name

    def run(self, slot, action):
        if not re.fullmatch(r"r-[0-9a-f]{12}",slot) or action not in ("check","ensure","restore","green","red"):
            raise ValueError("Invalid device operation")
        try:
            return self.shell(BASE+"/"+slot+"/ap.sh "+action)
        except RuntimeError as error:
            raise RuntimeError("Modem "+action+" failed: "+str(error)) from None

    def stage(self,directory):
        directory=Path(directory)
        digest=hashlib.sha256()
        for name in DEVICE_FILES:
            digest.update(name.encode()+b"\0"+(directory/name).read_bytes())
        slot="r-"+digest.hexdigest()[:12]
        dst=BASE+"/"+slot
        if self.shell("if test -f "+dst+"/complete; then cat "+dst+"/complete; fi")==slot:
            for name in DEVICE_FILES:
                if self.shell("md5sum "+dst+"/"+name).split()[0]!=hashlib.md5((directory/name).read_bytes()).hexdigest():
                    raise RuntimeError("Installed modem files failed integrity checking")
            return slot
        self.shell("umask 077; mkdir -p "+dst)
        for name in DEVICE_FILES:
            local=directory/name
            tmp=dst+"/"+name+".new"
            self.call("push",str(local),tmp)
            if self.shell("md5sum "+tmp).split()[0]!=hashlib.md5(local.read_bytes()).hexdigest():
                raise RuntimeError("Modem transfer failed readback")
            mode="700" if name in ("relay","ap.sh") else "600"
            self.shell("chmod "+mode+" "+tmp+" && mv "+tmp+" "+dst+"/"+name)
        try:
            self.shell("sh -n "+dst+"/ap.sh && iptables-restore --test < "+dst+"/filter.v4 && ip6tables-restore --test < "+dst+"/filter.v6 && echo "+slot+" > "+dst+"/complete")
        except RuntimeError:
            self.discard(slot)
            raise RuntimeError("Device shell or firewall rejected the staged extension") from None
        return slot

    def select(self,slot):
        if not re.fullmatch(r"r-[0-9a-f]{12}",slot):
            raise ValueError("Invalid modem slot")
        old=self.slot()
        if old and old!=slot:
            self.shell("echo "+old+" > "+BASE+"/previous.new && mv "+BASE+"/previous.new "+BASE+"/previous")
        self.shell("echo "+slot+" > "+BASE+"/current.new && mv "+BASE+"/current.new "+BASE+"/current")

    def prune(self):
        keep={self.slot(),self.pointer("previous")}
        for name in self.shell("ls "+BASE).split():
            if re.fullmatch(r"r-[0-9a-f]{12}",name) and name not in keep:
                self.discard(name)

    def discard(self,slot):
        if not re.fullmatch(r"r-[0-9a-f]{12}",slot):
            raise ValueError("Invalid modem slot")
        # All names below belong to this extension. Keep neighbouring firmware and user files.
        path=BASE+"/"+slot
        names=[path+"/"+f for f in (*DEVICE_FILES,"complete")]
        self.shell("if test -d "+path+"; then rm -f "+" ".join(names+[f+".new" for f in names])+" && rmdir "+path+"; fi")

    def remove(self, expected_slot=None):
        self.identify()
        exists=self.shell("if test -d "+BASE+"; then echo installed; fi")
        if not exists:
            return
        current=self.slot() or expected_slot
        if current:
            self.run(current,"restore")
        slots=self.shell("if test -d "+BASE+"; then ls "+BASE+"; fi").split()
        for slot in slots:
            if re.fullmatch(r"r-[0-9a-f]{12}",slot):
                self.discard(slot)
        self.shell("rm -f "+BASE+"/current "+BASE+"/current.new "+BASE+"/previous "+BASE+"/previous.new; if test -d "+BASE+"; then rmdir "+BASE+"; fi")

    def forward(self):
        # ADB chooses and returns an unused loopback port. Do not claim another application's mapping.
        value=self.call("forward","tcp:0","tcp:19874")
        if not value.isdigit() or not 1<=int(value)<=65535:
            raise RuntimeError("ADB did not allocate a loopback port")
        return int(value)

    def unforward(self,port):
        if port and 0<int(port)<=65535:
            mapping="tcp:"+str(port)
            if [self.serial,mapping,"tcp:19874"] in [x.split() for x in self.call("forward","--list").splitlines()]:
                self.call("forward","--remove",mapping)
