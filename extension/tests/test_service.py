"""Host regression tests for personal data, USB loss and orderly rollback."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HOST=Path(__file__).resolve().parents[1]/"host"
sys.path.insert(0,str(HOST))
import profiles
import service
from worker_job import WorkerJob

# Construct deliberately non-routable test inputs. Never load an installed user's profiles.
def link(index):
    import base64
    from uuid import UUID
    key=base64.urlsafe_b64encode(bytes([index])*32).decode().rstrip("=")
    return "vless://"+str(UUID(int=index))+"@node.invalid:443?security=reality&sni=site.invalid&pbk="+key+"&type=tcp&sid=aa&flow=xtls-rprx-vision"

class RuntimeTests(unittest.TestCase):
    def test_order_crlf_bom_and_dpapi(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"nodes"
            p.write_bytes(("\ufeff"+link(1)+"#label\r\n"+link(2)+"\r\n").encode())
            nodes=profiles.from_file(p)
            self.assertEqual([x["id"][-1] for x in nodes],["1","2"])
            blob=profiles.encrypt(nodes)
            self.assertNotIn("node.invalid",blob)
            self.assertEqual(profiles.decrypt(blob),nodes)

    def test_bad_inputs_and_private_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"profiles"
            cases=["",link(1)+"\n"+link(1),link(1)+"&type=ws",link(1)+"&unsupported=secret",
                   link(1).replace("node.invalid","127.0.0.1:bad"),"\n".join(link(n) for n in range(1,6))]
            for case in cases:
                p.write_text(case)
                with self.subTest(index=cases.index(case)),self.assertRaises(ValueError) as got:
                    profiles.from_file(p)
                msg=str(got.exception)
                self.assertNotIn("vless://",msg)
                self.assertNotIn("secret",msg)

    def test_usb_does_not_rotate(self):
        self.assertEqual(service.after_probe(1,1,"waiting-usb",3),(1,0))
        self.assertEqual(service.after_probe(1,1,"online",3),(1,0))
        self.assertEqual(service.after_probe(1,0,"upstream-failed",3),(1,1))
        self.assertEqual(service.after_probe(1,1,"upstream-failed",3),(2,0))
        self.assertEqual(service.after_probe(2,1,"upstream-failed",3),(0,0))
        self.assertEqual(service.after_probe(0,1,"upstream-failed",1),(0,0))

    def test_worker_ownership(self):
        worker=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            job=WorkerJob()
            job.attach(worker)
            job.close()
            self.assertIsNotNone(worker.wait(4))
        finally:
            service.stop_worker(worker)

    def test_atomic_settings_and_missing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"settings.json"
            self.assertIsNone(service.load(p))
            service.write_json(p,{"a":1});service.write_json(p,{"a":2})
            self.assertEqual(service.load(p),{"a":2})
            self.assertFalse(p.with_name("settings.json.new").exists())

    def test_fresh_worker_counter_required(self):
        class Running:
            pid=5
            def poll(self):return None
        with tempfile.TemporaryDirectory() as tmp,patch.object(service,"HOME",Path(tmp)),patch("time.time",return_value=100):
            p=Path(tmp)/"traffic.json"
            for row in ({"pid":4,"timestamp":100},{"pid":5,"timestamp":90}):
                service.write_json(p,row)
                self.assertIsNone(service.data_state(Running()))
            service.write_json(p,{"pid":5,"timestamp":99})
            self.assertEqual(service.data_state(Running())["pid"],5)

if __name__=="__main__":
    unittest.main()
