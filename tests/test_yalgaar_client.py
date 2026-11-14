"""Tests for app/yalgaar_client.py. A small fake broker speaks just enough MQTT 3.1.1 to check that
we log in with client id <key>/<uuid> (no username), subscribe to <key>/<channel>, read raw Yalgaar
error numbers, and parse messages."""
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from app import yalgaar_client as yc

FIX = Path(__file__).parent / "fixtures"
TELE = json.loads((FIX / "sample_telemetry.json").read_text())
FAULT = json.loads((FIX / "sample_faults.json").read_text())


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_packet(conn):
    first = conn.recv(1)
    if not first:
        return None, b""
    mult, length = 1, 0
    while True:
        b = conn.recv(1)[0]
        length += (b & 0x7F) * mult
        if not b & 0x80:
            break
        mult *= 128
    body = b""
    while len(body) < length:
        body += conn.recv(length - len(body))
    return first[0], body


class FakeBroker(threading.Thread):
    def __init__(self, connack=0, suback=(1, 0), publish=()):
        super().__init__(daemon=True)
        self.connack, self.suback, self.publish = connack, list(suback), list(publish)
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.seen = {"client_id": None, "username": None, "topics": [], "clean": None}
        self.start()

    def run(self):
        conn, _ = self.srv.accept()
        conn.settimeout(15)
        n_sub = 0
        try:
            while True:
                t, body = _read_packet(conn)
                if t is None:
                    break
                if t == 0x10:                                   # CONNECT
                    flags = body[7]
                    off = 10
                    cl = int.from_bytes(body[off:off + 2], "big")
                    self.seen["client_id"] = body[off + 2:off + 2 + cl].decode()
                    self.seen["username"] = bool(flags & 0x80)
                    self.seen["clean"] = bool(flags & 0x02)
                    conn.sendall(bytes([0x20, 0x02, 0x00, self.connack]))
                    if self.connack:
                        time.sleep(0.5)
                        break
                elif t == 0x82:                                 # SUBSCRIBE
                    mid = body[:2]
                    tl = int.from_bytes(body[2:4], "big")
                    self.seen["topics"].append(body[4:4 + tl].decode())
                    code = self.suback[min(n_sub, len(self.suback) - 1)]
                    conn.sendall(bytes([0x90, 0x03]) + mid + bytes([code]))
                    if n_sub == 0 and code < 100:
                        for payload in self.publish:
                            topic = self.seen["topics"][0].encode()
                            data = len(topic).to_bytes(2, "big") + topic + payload.encode()
                            conn.sendall(bytes([0x30]) + _varint(len(data)) + data)
                    n_sub += 1
                elif t == 0xC0:
                    conn.sendall(bytes([0xD0, 0x00]))
                elif t == 0xE0:
                    break
        except Exception:
            pass
        finally:
            conn.close()
            self.srv.close()


def cfg(port, **kw):
    base = dict(client_key="ck-testkey", uuid="gha-test", channel="1380242000046",
                host="127.0.0.1", port=port, history=100)
    base.update(kw)
    return yc.YalgaarConfig(**base)


def test_login_uses_key_slash_uuid_without_username_and_reads_messages():
    b = FakeBroker(connack=0, suback=(1, 112),
                   publish=[json.dumps(TELE), json.dumps(FAULT), "this is not json"])
    samples, st = yc.collect(cfg(b.port), quiet_s=2, max_s=20, log=lambda *a, **k: None)
    assert b.seen["client_id"] == "ck-testkey/gha-test"
    assert b.seen["username"] is False
    assert b.seen["clean"] is False                              # persistent session requested
    assert b.seen["topics"][0] == "ck-testkey/1380242000046"
    assert b.seen["topics"][1] == "ck-testkey/gha-test/1380242000046$100$history"
    assert st["connected"] and st["subscribed"]
    assert len(samples) == 1 and samples[0]["p_kw"] > 3.0        # telemetry parsed, fault message ignored
    assert st["unparsed"] == 1                                    # "this is not json"
    assert "Storage is not enabled" in st["history_error"]


def test_login_refused_shows_yalgaar_error_number():
    b = FakeBroker(connack=102)
    samples, st = yc.collect(cfg(b.port), quiet_s=1, max_s=10, log=lambda *a, **k: None)
    assert samples == [] and st["connected"] is False and st["connack"] == 102
    assert "Invalid ClientId" in yc.explain(st["connack"])


def test_subscribe_refused_for_uuid():
    b = FakeBroker(connack=0, suback=(125,))
    samples, st = yc.collect(cfg(b.port), quiet_s=1, max_s=10, log=lambda *a, **k: None)
    assert st["connected"] and not st["subscribed"]
    assert st["sub_codes"] == [125]                               # raw Yalgaar number survived
    assert "disabled for this UUID" in yc.explain(125)


def test_extract_messages_accepts_common_wrappers():
    for wrapped in (TELE, json.dumps(TELE), [TELE, TELE], {"messages": [json.dumps(TELE)]},
                    {"channel": "x", "data": {"message": TELE}}):
        assert len(yc.extract_messages(wrapped)) >= 1
    assert yc.extract_messages("hello") == []
    assert yc.extract_messages({"a": 1}) == []


def test_describe_shape_never_prints_values():
    s = yc.describe_shape(json.dumps({"secretname": "SECRETVALUE", "n": 5}))
    assert "SECRETVALUE" not in s and "secretname" in s


def test_config_from_env():
    assert yc.config_from_env({"LOGGER_SERIAL": "1"}) is None
    c = yc.config_from_env({"YALGAAR_CLIENT_KEY": " ck-x ", "YALGAAR_UUID": "u1", "LOGGER_SERIAL": "123"})
    assert c.client_id == "ck-x/u1" and c.topic == "ck-x/123" and c.port == 1883 and c.persistent
    s = yc.config_from_env({"YALGAAR_CLIENT_KEY": "k", "YALGAAR_UUID": "u", "YALGAAR_CHANNEL": "c", "YALGAAR_SSL": "true"})
    assert s.secure and s.port == 8883
    with pytest.raises(ValueError):
        yc.config_from_env({"YALGAAR_CLIENT_KEY": "k", "LOGGER_SERIAL": "1"})
    assert yc.config_from_env({"YALGAAR_CLIENT_KEY": "k", "YALGAAR_UUID": "u", "LOGGER_SERIAL": "1", "YALGAAR_HISTORY": "500"}).history == 100


def test_service_config_needs_no_single_channel():
    c = yc.config_from_env({"YALGAAR_CLIENT_KEY": "k", "YALGAAR_UUID": "u"}, require_channel=False)
    assert c.client_id == "k/u" and c.channel == ""
