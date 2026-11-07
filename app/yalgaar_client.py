"""Yalgaar collector (no username/password).

How Yalgaar works (read from the official pyYalgaar 1.0.6 SDK source):
  * It is plain MQTT on api.yalgaar.io (1883, or 8883 with TLS).
  * Login = the MQTT *client id*, written  <clientKey>/<uuid>  . No MQTT user name/password.
  * A channel is the MQTT topic  <clientKey>/<channel>.
  * History = subscribe to  <clientKey>/<uuid>/<channel>$<count>$history  (count max 100,
    needs "storage" enabled on the account).
  * Errors come back as numeric CONNACK / SUBACK codes (101..126). A normal MQTT library shows
    them all as "Unspecified error"; here we read the raw number and print Yalgaar's own text.

Not verified against the live broker from our build machine (it cannot reach api.yalgaar.io):
the message layout that arrives on the channel and the history reply layout. The extractor is
therefore tolerant, and anything it cannot read is counted and sampled (see `unparsed_raw`).
"""
from __future__ import annotations

import json
import os
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Optional

from app.html_report import parse_message

# Yalgaar error codes (copied from the SDK).
ERRORS = {
    0: "Connection successful",
    101: "ClientId should not be null",
    102: "Invalid ClientId (client id must be <clientKey>/<uuid>)",
    103: "Invalid ClientKey. ClientKey is not registered",
    104: "Invalid Uuid. Only letters, digits, - @ _ allowed, max length 50",
    105: "ClientKey is not active",
    106: "SSL is not enabled for this account",
    107: "The maximum connection limit has been reached (another client is using the key?)",
    108: "Invalid subscribe channel",
    109: "Invalid subscribe channel. ClientKey does not match",
    110: "Multiple subscribe channels are not allowed",
    111: "Invalid subscribe channel name",
    112: "Storage is not enabled on this account (history not available)",
    113: "Presence is not enabled",
    114: "History channel has not been subscribed first",
    115: "Message can not be null",
    116: "Invalid publish channel",
    117: "Invalid publish channel. ClientKey does not match",
    118: "Message count exceeds the maximum limit (daily message limit of the plan?)",
    119: "Message size exceeds the maximum limit",
    120: "Invalid publish channel name",
    121: "Invalid unsubscribe channel",
    122: "Invalid unsubscribe channel. ClientKey does not match",
    123: "Invalid unsubscribe channel name",
    125: "Subscribe is disabled for this UUID",
    126: "Publish is disabled for this UUID",
    # standard MQTT 3.1.1 CONNACK codes
    1: "Unacceptable protocol version",
    2: "Client id rejected",
    3: "Server unavailable",
    4: "Bad user name or password",
    5: "Not authorized",
}


def explain(code: Any) -> str:
    try:
        n = int(code)
    except (TypeError, ValueError):
        return str(code)
    return f"{n} = {ERRORS.get(n, 'unknown code')}"


def _allow_yalgaar_suback_codes() -> bool:
    """paho 2.x refuses SUBACK codes it does not know (Yalgaar sends 108-126 as errors) and hangs up
    with 'protocol error'. This keeps the raw numbers instead. Needs paho-mqtt 2.2.x (pinned)."""
    try:
        from paho.mqtt import _packet
    except Exception:
        return False
    if getattr(_packet.decode_suback, "_yalgaar_patched", False):
        return True
    original = _packet.decode_suback

    def tolerant(header, packet, protocol):
        try:
            return original(header, packet, protocol)
        except Exception:
            raw = bytes(packet)
            return _packet.SubackPacket(mid=int.from_bytes(raw[:2], "big"), reason_codes=[],
                                        granted_qos=tuple(raw[2:]), properties=None)
    tolerant._yalgaar_patched = True      # type: ignore[attr-defined]
    _packet.decode_suback = tolerant
    return True


@dataclass
class YalgaarConfig:
    client_key: str
    uuid: str
    channel: str
    host: str = "api.yalgaar.io"
    port: int = 1883
    secure: bool = False
    persistent: bool = True      # clean_session=False + QoS 1: ask the broker to keep messages for us
    history: int = 100           # 0 = do not ask for history; max 100 per Yalgaar

    @property
    def client_id(self) -> str:
        return f"{self.client_key}/{self.uuid}"

    @property
    def topic(self) -> str:
        return f"{self.client_key}/{self.channel}"

    @property
    def history_topic(self) -> str:
        return f"{self.client_key}/{self.uuid}/{self.channel}${self.history}$history"


def _flag(v: Optional[str], default: bool) -> bool:
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def config_from_env(env: Optional[dict] = None) -> Optional[YalgaarConfig]:
    """Returns None when no client key is configured (caller then uses the old username/password path)."""
    e = env if env is not None else os.environ
    g = lambda k, d="": (e.get(k) or d).strip()
    key = g("YALGAAR_CLIENT_KEY")
    if not key:
        return None
    secure = _flag(e.get("YALGAAR_SSL"), False)
    channel = g("YALGAAR_CHANNEL") or g("LOGGER_SERIAL")
    uuid = g("YALGAAR_UUID")
    if not uuid:
        raise ValueError("YALGAAR_UUID is empty. Use a name like solarpulse-gha (letters, digits, - @ _).")
    if not channel:
        raise ValueError("YALGAAR_CHANNEL (or LOGGER_SERIAL) is empty.")
    hist = int(g("YALGAAR_HISTORY", "100") or 0)
    return YalgaarConfig(
        client_key=key, uuid=uuid, channel=channel,
        host=g("YALGAAR_HOST") or g("MQTT_BROKER") or "api.yalgaar.io",
        port=int(g("YALGAAR_PORT") or (8883 if secure else 1883)),
        secure=secure, persistent=_flag(e.get("YALGAAR_PERSISTENT"), True),
        history=max(0, min(hist, 100)),
    )


# ---------------------------------------------------------------- message extraction
def extract_messages(obj: Any, _depth: int = 0) -> list[dict]:
    """Find logger messages ({timestamp, payload:{functionId, functionPayload}}) inside any wrapper:
    a plain message, a JSON string, a list of them, or a dict that holds them under any key."""
    if _depth > 6:
        return []
    if isinstance(obj, (bytes, bytearray)):
        obj = obj.decode("utf-8", errors="replace")
    if isinstance(obj, str):
        s = obj.strip()
        if s[:1] in "{[\"":
            try:
                return extract_messages(json.loads(s), _depth + 1)
            except ValueError:
                return []
        return []
    if isinstance(obj, list):
        out: list[dict] = []
        for x in obj:
            out += extract_messages(x, _depth + 1)
        return out
    if isinstance(obj, dict):
        pl = obj.get("payload")
        if isinstance(pl, dict) and "functionPayload" in pl and "timestamp" in obj:
            return [obj]
        out = []
        for v in obj.values():
            if isinstance(v, (dict, list, str)):
                out += extract_messages(v, _depth + 1)
        return out
    return []


def describe_shape(text: str) -> str:
    """Structure only (key names / types), never values. Safe to print in logs."""
    try:
        o = json.loads(text)
    except ValueError:
        return f"not JSON, {len(text)} chars"

    def shape(x: Any, d: int = 0) -> str:
        if d > 2:
            return "..."
        if isinstance(x, dict):
            return "{" + ", ".join(f"{k}: {shape(v, d + 1)}" for k, v in list(x.items())[:8]) + "}"
        if isinstance(x, list):
            return f"list[{len(x)}] of " + (shape(x[0], d + 1) if x else "empty")
        return type(x).__name__
    return shape(o)


# ---------------------------------------------------------------- collection
def collect(cfg: YalgaarConfig, quiet_s: int = 45, max_s: int = 300,
            log: Callable[..., None] = print) -> tuple[list[dict], dict]:
    """Connect, subscribe to the channel (and ask for history), collect until quiet. Returns (samples, stats)."""
    import paho.mqtt.client as mqtt

    if not _allow_yalgaar_suback_codes():
        log("WARNING: paho-mqtt is not 2.2.x; Yalgaar subscribe errors may show as a plain disconnect", flush=True)
    stats: dict[str, Any] = {
        "connected": False, "connack": None, "subscribed": False, "sub_codes": [],
        "live": 0, "history_msgs": 0, "history_replies": 0, "samples": 0,
        "unparsed": 0, "unparsed_shapes": [], "unparsed_raw": [], "history_error": None,
    }
    samples: list[dict] = []
    lock = threading.Lock()
    last = [time.time()]
    done = threading.Event()
    state = {"sub_mid": None, "hist_mid": None, "hist_sent": False}

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)      # VERSION1 gives us the raw Yalgaar number
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=cfg.client_id,
                        clean_session=not cfg.persistent)
    if cfg.secure:
        c.tls_set()

    def handle_text(text: str, kind: str) -> None:
        found = extract_messages(text)
        with lock:
            last[0] = time.time()
            stats["history_msgs" if kind == "history" else "live"] += (len(found) if kind == "history" else 1)
            good = 0
            for m in found:
                s = parse_message(m)
                if s:
                    samples.append(s); good += 1
            if kind == "history":
                stats["history_replies"] += 1
            if not good and (kind == "live" or not found):
                # a live message we could not read as telemetry (fault flags are fine, they are ignored on purpose)
                fid = None
                if found:
                    fid = found[0].get("payload", {}).get("functionId")
                if fid not in (15,):
                    stats["unparsed"] += 1
                    if len(stats["unparsed_raw"]) < 3:
                        stats["unparsed_raw"].append(text[:4000])
                        stats["unparsed_shapes"].append(describe_shape(text))

    def on_connect(cl, ud, flags, rc):
        stats["connack"] = rc
        stats["connected"] = (rc == 0)
        log(f"broker answer to login: {explain(rc)}  (host={cfg.host}:{cfg.port} client_id={cfg.uuid} persistent={cfg.persistent})", flush=True)
        if rc != 0:
            done.set(); return
        _, state["sub_mid"] = cl.subscribe(cfg.topic, qos=1 if cfg.persistent else 0)

    def on_subscribe(cl, ud, mid, granted):
        codes = list(granted)
        if mid == state["sub_mid"]:
            bad = [x for x in codes if x >= 100]
            stats["sub_codes"] = codes
            if bad:
                log("ERROR subscribing to the channel: " + "; ".join(explain(x) for x in bad), flush=True)
                done.set(); return
            stats["subscribed"] = True; last[0] = time.time()
            log(f"subscribed to channel {cfg.channel} (granted {codes})", flush=True)
            if cfg.history and not state["hist_sent"]:
                state["hist_sent"] = True
                _, state["hist_mid"] = cl.subscribe(cfg.history_topic, qos=0)
        elif mid == state["hist_mid"]:
            bad = [x for x in codes if x >= 100]
            if bad:
                stats["history_error"] = "; ".join(explain(x) for x in bad)
                log("history not available: " + stats["history_error"], flush=True)
            else:
                log("history requested", flush=True)
            last[0] = time.time()

    def on_message(cl, ud, msg):
        text = msg.payload.decode("utf-8", errors="replace")
        handle_text(text, "history" if msg.topic.endswith("$history") else "live")

    def on_disconnect(cl, ud, rc):
        if rc != 0 and not done.is_set():
            log(f"disconnected by the broker or network (paho code {rc})", flush=True)

    c.on_connect, c.on_subscribe, c.on_message, c.on_disconnect = on_connect, on_subscribe, on_message, on_disconnect
    try:
        c.connect(cfg.host, cfg.port, keepalive=60)
    except Exception as ex:  # DNS / refused / timeout
        log(f"ERROR: TCP connect failed: {type(ex).__name__}: {ex}", flush=True)
        return [], stats
    c.loop_start()
    t0 = time.time()
    try:
        while not done.is_set():
            time.sleep(0.5)
            now = time.time()
            if now - t0 > max_s:
                break
            if not stats["connected"] and now - t0 > 60:
                log("ERROR: no login answer from the broker within 60 s", flush=True); break
            if stats["subscribed"]:
                waiting_history = bool(cfg.history) and not stats["history_error"] and stats["history_replies"] == 0 and now - t0 < 30
                if not waiting_history and now - last[0] > quiet_s:
                    break
    finally:
        c.loop_stop()
        c.disconnect()          # a persistent session stays on the broker
    stats["samples"] = len(samples)
    return samples, stats
