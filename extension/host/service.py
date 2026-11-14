"""Windows per-user hotspot supervisor and lifecycle commands."""
import argparse
from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time

from device import Device, usb_devices
import profiles
from worker_job import WorkerJob

APP = Path(__file__).resolve().parents[1]
HOME = Path(os.environ["LOCALAPPDATA"]) / "NewmanHotspot"
CONFIG = HOME / "settings.json"
PENDING = HOME / "pending-settings.json"
STATE = HOME / "state.json"
ENABLED = HOME / "enabled"
SESSION_LOCK = "Local\\MF761WHotspotWorker"
FAILURES_BEFORE_SWITCH = 2
HANDLED = (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired)
BUILD = (APP/"VERSION").read_text(encoding="ascii").strip()
kernel = c.WinDLL("kernel32", use_last_error=True)
kernel.CreateMutexW.argtypes = [c.c_void_p, w.BOOL, w.LPCWSTR]
kernel.CreateMutexW.restype = w.HANDLE
kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
kernel.ReleaseMutex.argtypes = [w.HANDLE]
kernel.CloseHandle.argtypes = [w.HANDLE]


@contextmanager
def lock(name, timeout=0):
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise OSError("Cannot open Windows hotspot lock")
    wait = kernel.WaitForSingleObject(handle, timeout)
    owned = wait in (0, 128)
    try:
        yield owned
    finally:
        if owned:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def worker_active():
    with lock(SESSION_LOCK) as acquired:
        return not acquired


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+".new")
    temp.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
    for n in range(8):
        try:
            os.replace(temp,path)
            return
        except PermissionError:
            if n==7:
                raise
            time.sleep(.05)


def load(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def role(index):
    return "primary" if index==0 else "backup-"+str(index)


def after_probe(index, failures, outcome, count):
    """USB failure is independent of upstream health and preserves the selected node."""
    if outcome!="upstream-failed":
        return index, 0
    failures+=1
    if failures>=FAILURES_BEFORE_SWITCH:
        return (index+1)%count, 0
    return index, failures


def stop_worker(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(4)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(4)


def available_port():
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        sock.bind(("127.0.0.1",0))
        return sock.getsockname()[1]


def launch_proxy(binary, profile, port, job):
    reality = profile["reality"]
    user = {"id":profile["id"],"encryption":"none","flow":profile["flow"]}
    conf={"log":{"loglevel":"none"},
          "inbounds":[{"listen":"127.0.0.1","port":port,"protocol":"socks","settings":{"auth":"noauth","udp":False}}],
          "outbounds":[{"protocol":"vless","settings":{"vnext":[{"address":profile["address"],"port":profile["port"],"users":[user]}]},
                        "streamSettings":{"network":"tcp","security":"reality","realitySettings":reality}}]}
    proc=subprocess.Popen([str(binary),"run","-format","json","-config","stdin:"],stdin=subprocess.PIPE,
                          stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        job.attach(proc)
        proc.stdin.write(json.dumps(conf).encode())
        proc.stdin.close()
        for _ in range(35):
            if proc.poll() is not None:
                raise RuntimeError("Encrypted upstream worker exited")
            try:
                with socket.create_connection(("127.0.0.1",port),.3):
                    if proc.poll() is None:
                        return proc
            except OSError:
                pass
            time.sleep(.1)
        raise RuntimeError("Encrypted upstream worker did not start")
    except BaseException:
        stop_worker(proc)
        raise


def exact(sock, count):
    data=bytearray()
    while len(data)<count:
        part=sock.recv(count-len(data))
        if not part:
            raise OSError("Closed SOCKS connection")
        data.extend(part)
    return bytes(data)


def probe(port, context):
    # Use system trust roots and verify an actual HTTP response through this proxy.
    for host,path,code in (("www.gstatic.com","/generate_204",204),("example.com","/",200)):
        try:
            with socket.create_connection(("127.0.0.1",port),3) as sock:
                sock.settimeout(6)
                sock.sendall(b"\x05\x01\x00")
                if exact(sock,2)!=b"\x05\x00":
                    continue
                name=host.encode("ascii")
                sock.sendall(bytes((5,1,0,3,len(name)))+name+b"\x01\xbb")
                ver,status,_,kind=exact(sock,4)
                if ver!=5 or status!=0 or kind not in (1,3,4):
                    continue
                size=exact(sock,1)[0] if kind==3 else (4 if kind==1 else 16)
                exact(sock,size+2)
                with context.wrap_socket(sock,server_hostname=host) as tls:
                    tls.sendall(("GET "+path+" HTTP/1.1\r\nHost: "+host+"\r\nConnection: close\r\n\r\n").encode())
                    with tls.makefile("rb") as response:
                        parts=response.readline(256).split()
                    if len(parts)>=2 and parts[1]==str(code).encode():
                        return True
        except (OSError, ValueError):
            continue
        except EOFError:
            continue
    return False


def data_state(proc):
    if not proc or proc.poll() is not None:
        return None
    try:
        state=load(HOME/"traffic.json",{})
        if state.get("pid")==proc.pid and abs(time.time()-state["timestamp"])<=6:
            return state
    except (OSError, KeyError, ValueError):
        pass
    return None


def quiesce():
    ENABLED.unlink(missing_ok=True)
    deadline=time.monotonic()+80
    while worker_active() and time.monotonic()<deadline:
        time.sleep(.2)
    if worker_active():
        raise RuntimeError("Supervisor is still stopping; retry after the USB command finishes")


def lifecycle_enabled():
    ENABLED.parent.mkdir(parents=True,exist_ok=True)
    ENABLED.touch()


def run():
    with lock(SESSION_LOCK) as owner:
        if not owner or not ENABLED.exists():
            return
        cfg=load(CONFIG)
        if not cfg:
            raise ValueError("Configure the extension before starting")
        dev=Device(cfg["adb"],cfg["serial"])
        nodes=profiles.decrypt(cfg["nodes"])
        context=ssl.create_default_context()
        job=WorkerJob()
        bridge=proxy=None
        usb_port=0
        socks=available_port()
        index=failures=0
        last=None
        def publish(phase, traffic=None):
            nonlocal last
            stamp=int(time.time())
            report={"phase":phase,"role":role(index),"time":stamp,"supervisor":os.getpid(),"version":BUILD}
            if traffic:
                report["counts"]={key:traffic[key] for key in ("clients","tcp","dns","socks_errors","dns_errors")}
            write_json(STATE,report)
            marker=(phase,index)
            if marker!=last:
                log=HOME/"events.log"
                if log.is_file() and log.stat().st_size>65536:
                    os.replace(log,HOME/"events.previous.log")
                with log.open("a",encoding="ascii") as stream:
                    stream.write(time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(stamp))+" "+phase+" "+role(index)+"\n")
                last=marker
        try:
            publish("starting")
            while ENABLED.exists():
                healthy=False
                traffic=data_state(bridge)
                if not traffic:
                    stop_worker(bridge)
                    bridge=None
                    try:
                        dev.identify()
                        if dev.slot()!=cfg["slot"]:
                            raise RuntimeError("Modem extension version differs")
                        dev.run(cfg["slot"],"ensure")
                        if usb_port:
                            dev.unforward(usb_port)
                        usb_port=dev.forward()
                        bridge=subprocess.Popen([str(APP/"gateway.exe"),"-usb-port",str(usb_port),"-socks",str(socks),
                                       "-state",str(HOME/"traffic.json")],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                        job.attach(bridge)
                        for _ in range(24):
                            traffic=data_state(bridge)
                            if traffic or bridge.poll() is not None:
                                break
                            time.sleep(.25)
                    except HANDLED:
                        traffic=None
                if traffic:
                    try:
                        if proxy is None or proxy.poll() is not None:
                            proxy=launch_proxy(cfg["xray"],nodes[index],socks,job)
                        healthy=probe(socks,context)
                    except HANDLED:
                        healthy=False
                    try:
                        # DHCP and relay are supervised even when the upstream is down.
                        dev.run(cfg["slot"],"ensure")
                        dev.run(cfg["slot"],"green" if healthy else "red")
                    except HANDLED:
                        stop_worker(bridge)
                        bridge=None
                    traffic=data_state(bridge)
                phase=("online" if healthy else "upstream-failed") if traffic else "waiting-usb"
                publish(phase,traffic)
                if phase=="upstream-failed" and failures+1>=FAILURES_BEFORE_SWITCH:
                    stop_worker(proxy)
                    proxy=None
                index,failures=after_probe(index,failures,phase,len(nodes))
                for _ in range(24 if traffic else 6):
                    if not ENABLED.exists():
                        break
                    time.sleep(.5)
        finally:
            try:
                stop_worker(bridge)
                stop_worker(proxy)
            finally:
                job.close()
            try:
                dev.unforward(usb_port)
                if dev.slot()==cfg["slot"]:
                    dev.run(cfg["slot"],"red")
            except HANDLED:
                pass
            publish("stopped")


def setup(args):
    old=load(CONFIG,{})
    adb=Path(args.adb or old.get("adb","adb.exe")).resolve()
    xray=Path(args.xray or old.get("xray","xray.exe")).resolve()
    if not adb.is_file() or not xray.is_file() or not (APP/"gateway.exe").is_file():
        raise ValueError("Select existing ADB, Xray and the extracted Windows x64 release")
    if args.nodes:
        cipher=profiles.encrypt(profiles.from_file(args.nodes))
    elif old.get("nodes"):
        cipher=old["nodes"]
        profiles.decrypt(cipher)
    else:
        raise ValueError("Provide --nodes with your own REALITY TCP links")
    serial=args.serial or old.get("serial")
    if serial is None:
        found=[]
        for candidate in usb_devices(adb):
            d=Device(adb,candidate)
            try:
                if d.shell("cat /etc/lean-linux-release").startswith("MF761W P005 stock-kernel rootfs candidate 20261012"):
                    found.append(candidate)
            except HANDLED:
                continue
        if len(found)!=1:
            raise ValueError("Connect one authorized P005 or specify its USB serial")
        serial=found[0]
    if old and old["serial"]!=serial:
        raise ValueError("Uninstall the current modem before selecting another")
    dev=Device(adb,serial)
    dev.identify()
    previous=dev.slot()
    if previous:
        dev.prune()
    slot=dev.stage(APP/"device")
    new_config={"adb":str(adb),"xray":str(xray),"serial":serial,"nodes":cipher,"slot":slot}
    changed=False
    try:
        dev.run(slot,"check")
        test_job=WorkerJob()
        test_proxy=None
        try:
            test_proxy=launch_proxy(xray,profiles.decrypt(cipher)[0],available_port(),test_job)
        finally:
            stop_worker(test_proxy)
            test_job.close()
        write_json(PENDING,new_config)
        changed=True
        dev.run(slot,"ensure")
        dev.select(slot)
        write_json(CONFIG,new_config)
        PENDING.unlink(missing_ok=True)
        # A subsequent update only needs the current and preceding modem binaries.
        try:
            dev.prune()
        except HANDLED:
            pass
    except BaseException:
        if changed:
            if previous:
                dev.run(previous,"ensure")
                dev.select(previous)
            else:
                dev.run(slot,"restore")
                dev.shell("rm -f /etc_rw/mf761w-host-ap/current")
        if previous!=slot:
            dev.discard(slot)
        PENDING.unlink(missing_ok=True)
        raise


def status():
    cfg=load(CONFIG,{})
    state=load(STATE,{})
    if not cfg:
        phase="not-installed"
    elif not ENABLED.exists():
        phase="stopped"
    elif not worker_active() or abs(time.time()-state.get("time",0))>100:
        phase="waiting-supervisor"
    else:
        phase=state.get("phase","starting")
    print(json.dumps({"phase":phase,"role":state.get("role"),"version":state.get("version",BUILD),
                      "counts":state.get("counts",{}) if phase=="online" else {}},ensure_ascii=True))
    return phase


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("action",choices=("install","run","start","stop","status","remove","doctor","rollback-device"))
    cli.add_argument("--adb")
    cli.add_argument("--xray")
    cli.add_argument("--serial")
    cli.add_argument("--nodes",type=Path)
    cli.add_argument("--slot")
    args=cli.parse_args()
    if args.action=="run":
        run()
    elif args.action=="status":
        status()
    elif args.action=="doctor":
        cfg=load(CONFIG,{})
        d=Device(cfg["adb"],cfg["serial"])
        d.identify()
        slot=d.slot()
        if slot!=cfg["slot"]:
            raise RuntimeError("Installed modem version mismatch")
        d.run(slot,"check")
        verified=d.shell("test -s /tmp/mf761w-host-ap/dhcp.pid && grep -q ':0043 ' /proc/net/udp && echo ready")
        print("USB identity, modem slot, firewall and local DHCP: "+verified)
        limit=d.shell("if test -r /etc_rw/wifi/hostapd.conf; then sed -n 's/^max_num_sta=//p' /etc_rw/wifi/hostapd.conf; fi")
        print("Wi-Fi station limit: "+(limit if limit.isdigit() else "not reported"))
        status()
    elif args.action=="stop":
        quiesce()
    elif args.action=="start":
        if not CONFIG.is_file():
            raise ValueError("No installed configuration")
        lifecycle_enabled()
    elif args.action=="install":
        setup(args)
    elif args.action=="rollback-device":
        cfg=load(CONFIG,{})
        d=Device(cfg["adb"],cfg["serial"])
        d.identify()
        d.run(cfg["slot"],"ensure")
        d.select(cfg["slot"])
    else:
        cfg=load(CONFIG) or load(PENDING,{})
        quiesce()
        if cfg:
            Device(cfg["adb"],cfg["serial"]).remove(cfg["slot"])


if __name__=="__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.TimeoutExpired) as exc:
        if isinstance(exc, (ValueError,RuntimeError)) and type(exc) in (ValueError,RuntimeError):
            print(str(exc),file=sys.stderr)
        else:
            print("Hotspot command failed ("+type(exc).__name__+"). Check USB, installation and task status.",file=sys.stderr)
        sys.exit(1)
