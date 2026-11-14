import datetime as dt
import json
import threading
import time
from pathlib import Path

from app import service as svc
from app import yalgaar_client as yc
from app.html_report import parse_message
from app.store import Store
from tests.fake_yalgaar import MultiBroker

FIX = Path(__file__).parent / "fixtures"
TELE = json.loads((FIX / "sample_telemetry.json").read_text())
FDAY = parse_message(TELE)["t"].date()


def msg(mac, minute_offset=0):
    m = json.loads(json.dumps(TELE))
    m["headerMac"] = mac
    m["timestamp"] = TELE["timestamp"] + 300 * minute_offset
    return json.dumps(m)


def sample(mac, offset=0):
    return parse_message(json.loads(msg(mac, offset)))


def cfg(port):
    return yc.YalgaarConfig(client_key="ck-testkey", uuid="srv", channel="", host="127.0.0.1", port=port, history=0)


def lgs(*specs):
    return [svc.LoggerCfg(serial=s, name=f"Logger {s}", macs=list(m)) for s, m in specs]


def fast(monkeypatch):
    monkeypatch.setattr(svc.ChannelWorker, "BACKOFF_START", 0.3)
    monkeypatch.setattr(svc.ChannelWorker, "BACKOFF_MAX", 0.5)


def now_ist():
    return svc.ist_now()


def settings(tmp_path, **kw):
    st = svc.Settings(out_dir=tmp_path / "out", make_pdf=False, send_email=False)
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def test_one_connection_per_channel_and_devices_are_kept_apart(tmp_path, monkeypatch):
    fast(monkeypatch)
    b = MultiBroker({"CH1": [msg("MAC_A", 0), msg("MAC_A", 1), msg("MAC_B", 0)]})
    store = Store(tmp_path / "t.db")
    svc.collect_window(cfg(b.port), ["CH1", "CH2"], store, now_ist() + dt.timedelta(seconds=5), heartbeat_s=1)
    assert sorted(b.topics) == ["ck-testkey/CH1", "ck-testkey/CH2"]
    assert sorted(b.clients) == ["ck-testkey/srv-CH1", "ck-testkey/srv-CH2"]
    assert store.macs_for_day(FDAY) == {"MAC_A": 2, "MAC_B": 1}


def test_worker_reconnects_after_a_dropped_connection(tmp_path, monkeypatch):
    fast(monkeypatch)
    b = MultiBroker({"CH1": [msg("MAC_A", 0)]}, drop_first=True)
    store = Store(tmp_path / "t.db")
    svc.collect_window(cfg(b.port), ["CH1"], store, now_ist() + dt.timedelta(seconds=6))
    assert b.clients.count("ck-testkey/srv-CH1") >= 2
    assert store.count_for_mac("MAC_A", FDAY) == 1


def test_finalize_maps_devices_to_loggers_and_reports_what_is_missing(tmp_path):
    store = Store(tmp_path / "t.db")
    store.add_samples("CH1", [sample("MAC_A"), sample("MAC_B"), sample("MAC_Z")])
    loggers = lgs(("L1", ["MAC_A"]), ("L2", ["MAC_B"]), ("L3", []), ("L4", ["MAC_X"]))
    rows = {r["serial"]: r for r in svc.finalize_day(FDAY, loggers, store, settings(tmp_path))}
    assert [rows[k]["status"] for k in ("L1", "L2", "L3", "L4")] == ["OK", "OK", "NO DEVICE ID SET", "NO DATA"]
    assert (tmp_path / "out" / f"solar_report_L1_{FDAY}.html").exists()
    assert (tmp_path / "out" / f"solar_report_L2_{FDAY}.html").exists()
    assert not (tmp_path / "out" / f"solar_report_L3_{FDAY}.html").exists()
    assert store.get_summary("L1", FDAY)["mac"] == "MAC_A" and store.get_summary("L3", FDAY) is None
    assert store.is_finalized(FDAY)


def test_each_report_uses_only_its_own_device(tmp_path):
    store = Store(tmp_path / "t.db")
    a, b = sample("MAC_A"), sample("MAC_B")
    b["e_kwh"], b["p_kw"] = 99.0, 9.9                       # a very different device
    store.add_samples("CH1", [a, b])
    svc.finalize_day(FDAY, lgs(("L1", ["MAC_A"])), store, settings(tmp_path))
    assert store.get_summary("L1", FDAY)["energy_kwh"] == a["e_kwh"]


def test_email_goes_to_owner_unless_clients_are_switched_on_and_digest_lists_unassigned(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(svc, "send_mail", lambda subject, text, html, to, att=(), env=None: sent.append((subject, list(to), html, [Path(a).name for a in att])))
    store = Store(tmp_path / "t.db")
    store.add_samples("CH1", [sample("MAC_A"), sample("MAC_Z")])
    lg = [svc.LoggerCfg(serial="L1", name="Client A", macs=["MAC_A"], emails=["client@example.com"])]
    svc.finalize_day(FDAY, lg, store, settings(tmp_path, send_email=True, owner_mail=["owner@example.com"]))
    assert [s[1] for s in sent] == [["owner@example.com"], ["owner@example.com"]]
    assert sent[0][3] == [f"solar_report_L1_{FDAY}.html"]
    assert "MAC_Z" in sent[1][2]                                     # digest names the device nobody owns
    sent.clear()
    svc.finalize_day(FDAY, lg, store, settings(tmp_path, send_email=True, owner_mail=["owner@example.com"], send_to_clients=True))
    assert sent[0][1] == ["client@example.com"] and sent[1][1] == ["owner@example.com"]


def test_a_failing_email_does_not_stop_the_day(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("smtp down")
    monkeypatch.setattr(svc, "send_mail", boom)
    store = Store(tmp_path / "t.db")
    store.add_samples("CH1", [sample("MAC_A")])
    rows = svc.finalize_day(FDAY, lgs(("L1", ["MAC_A"])), store, settings(tmp_path, send_email=True, owner_mail=["o@example.com"]))
    assert rows[0]["status"] == "OK" and store.is_finalized(FDAY) and store.get_summary("L1", FDAY)


def test_main_loop_collects_to_the_end_of_the_window_then_finalizes(tmp_path, monkeypatch):
    fast(monkeypatch)
    b = MultiBroker({"CH1": [msg("MAC_A", 0)]})
    store = Store(tmp_path / "t.db")
    base = dt.datetime.combine(FDAY, dt.time(17, 59, 56))
    t0 = time.time()
    clock = lambda: base + dt.timedelta(seconds=time.time() - t0)
    stop = threading.Event()
    threading.Timer(9, stop.set).start()
    svc.run_forever(cfg(b.port), ["CH1"], lgs(("L1", ["MAC_A"])), store, settings(tmp_path), clock=clock, stop=stop)
    assert store.count_for_mac("MAC_A", FDAY) == 1
    assert store.is_finalized(FDAY) and store.get_summary("L1", FDAY) is not None


def test_assign_moves_a_device_to_one_logger_only(tmp_path, monkeypatch):
    from scripts import devices
    f = tmp_path / "loggers.json"
    f.write_text(json.dumps([{"serial": "L1", "name": "One", "macs": ["MAC_A"]}, {"serial": "L2", "name": "Two", "macs": []}]))
    monkeypatch.setattr(devices, "LOGGERS", f)
    assert devices.assign("L2=MAC_A") == 0
    got = {i["serial"]: i["macs"] for i in json.loads(f.read_text())}
    assert got == {"L1": [], "L2": ["MAC_A"]}
    assert devices.assign("NOPE=MAC_A") == 2
