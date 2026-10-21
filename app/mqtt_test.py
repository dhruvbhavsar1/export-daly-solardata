"""Solar Daily Report Automation - Phase 1: MQTT Connection Test.

Connects to the solar MQTT broker, validates credentials and topics,
and listens for real-time messages from the solar logger.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import os
import signal
import ssl
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import paho.mqtt.client as mqtt

from app.config import ConfigError, MQTTConfig, load_config

# Setup safe logger that never logs passwords
logger = logging.getLogger("solar_mqtt_test")


class SolarMQTTTester:
    """Manages MQTT connection test lifecycle and metrics."""

    def __init__(self, config: MQTTConfig, duration: int = 60) -> None:
        self.config = config
        self.duration = duration

        self.client_id = f"solar-test-{config.logger_serial}-{uuid.uuid4().hex[:6]}"
        self.client: Optional[mqtt.Client] = None

        # Synchronization events
        self.connected_event = threading.Event()
        self.subscribed_event = threading.Event()
        self.stop_event = threading.Event()

        # Metrics and state
        self.connect_success = False
        self.connect_error_message: Optional[str] = None
        self.subscribe_success = False
        self.subscribe_error_message: Optional[str] = None
        self.unexpected_disconnect = False
        self.disconnect_reason: Optional[str] = None

        self.messages_received: list[dict[str, Any]] = []
        self.start_time: Optional[datetime.datetime] = None
        self.end_time: Optional[datetime.datetime] = None

    def _create_client(self) -> mqtt.Client:
        """Instantiates and configures the Paho MQTT client."""
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.client_id,
            )
        else:
            client = mqtt.Client(client_id=self.client_id)

        # Configure authentication
        client.username_pw_set(self.config.username, self.config.password)

        # Configure TLS if explicitly required
        if self.config.use_tls:
            context = ssl.create_default_context()
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            client.tls_set_context(context)

        # Wire callbacks
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_subscribe = self._on_subscribe
        client.on_message = self._on_message

        return client

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags: Any,
        reason_code: Any,
        properties: Any = None,
    ) -> None:
        """Handles connection acknowledgment from broker."""
        # For Paho v2, reason_code has .is_failure; for v1 it is integer 0 on success
        is_failure = getattr(reason_code, "is_failure", None)
        if is_failure is None:
            is_failure = (reason_code != 0)

        if not is_failure:
            self.connect_success = True
            timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
            print(f"[{timestamp}] [INFO] Connected to broker successfully ({reason_code}).")
            print(f"[{timestamp}] [INFO] Subscribing to topic: '{self.config.topic}' (QoS=1)...")
            res, mid = client.subscribe(self.config.topic, qos=1)
            if res != mqtt.MQTT_ERR_SUCCESS:
                self.subscribe_error_message = f"Immediate subscription error: code {res}"
                self.subscribed_event.set()
        else:
            self.connect_success = False
            self.connect_error_message = f"Broker refused connection: {reason_code}"
            print(f"[ERROR] Connection rejected by broker: {reason_code}")

        self.connected_event.set()

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags_or_rc: Any,
        reason_code: Any = None,
        properties: Any = None,
    ) -> None:
        """Handles disconnection events."""
        rc = reason_code if reason_code is not None else flags_or_rc
        is_failure = getattr(rc, "is_failure", None)
        if is_failure is None:
            is_failure = (rc != 0)

        # If we didn't initiate stop and rc indicates an error
        if not self.stop_event.is_set() and is_failure:
            self.unexpected_disconnect = True
            self.disconnect_reason = str(rc)
            print(f"[WARNING] Unexpected disconnect from broker: {rc}")

    def _on_subscribe(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        reason_codes: Any,
        properties: Any = None,
    ) -> None:
        """Handles subscription confirmation."""
        # reason_codes can be a list of ReasonCode objects (v2) or list of granted QoS (v1)
        first_code = reason_codes[0] if isinstance(reason_codes, list) and reason_codes else reason_codes
        is_failure = getattr(first_code, "is_failure", None)
        if is_failure is None:
            is_failure = (first_code >= 128) if isinstance(first_code, int) else False

        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not is_failure:
            self.subscribe_success = True
            print(f"[{timestamp}] [INFO] Subscription confirmed for '{self.config.topic}' (granted: {first_code}).")
        else:
            self.subscribe_success = False
            self.subscribe_error_message = f"Subscription rejected by broker: {first_code}"
            print(f"[{timestamp}] [ERROR] Subscription rejected: {first_code}")

        self.subscribed_event.set()

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        """Handles incoming MQTT messages safely without leaking secrets."""
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        timestamp_iso = now_dt.isoformat()
        raw_payload = message.payload
        payload_len = len(raw_payload)

        # Attempt decoding
        is_utf8 = True
        try:
            decoded_text = raw_payload.decode("utf-8")
        except UnicodeDecodeError:
            is_utf8 = False
            decoded_text = f"<Binary data: {raw_payload[:64].hex()}{'...' if payload_len > 64 else ''}>"

        parsed_json = None
        if is_utf8:
            try:
                parsed_json = json.loads(decoded_text)
            except json.JSONDecodeError:
                parsed_json = None

        record = {
            "index": len(self.messages_received) + 1,
            "timestamp": timestamp_iso,
            "topic": message.topic,
            "qos": message.qos,
            "retain": message.retain,
            "length_bytes": payload_len,
            "payload_text": decoded_text if not parsed_json else json.dumps(parsed_json),
        }
        self.messages_received.append(record)

        print("\n" + "=" * 60)
        print(f" [MESSAGE #{record['index']}] Received at {timestamp_iso}")
        print(f" Topic   : {message.topic}")
        print(f" QoS     : {message.qos} | Retain: {message.retain} | Size: {payload_len} bytes")
        if parsed_json:
            print(" Payload (JSON):")
            print(json.dumps(parsed_json, indent=2))
        else:
            print(f" Payload : {decoded_text}")
        print("=" * 60 + "\n")

    def run(self) -> dict[str, Any]:
        """
        Executes the MQTT connection test.

        Returns a dictionary summary of test execution.
        """
        self.start_time = datetime.datetime.now(datetime.timezone.utc)
        print("=" * 60)
        print("SOLAR MQTT CONNECTION TEST (PHASE 1)")
        print("=" * 60)
        print(f"Broker Host   : {self.config.broker}")
        print(f"Broker Port   : {self.config.port}")
        print(f"TLS Security  : {'Enabled (Strict CA verification)' if self.config.use_tls else 'Disabled (Plain TCP)'}")
        print(f"Client ID     : {self.client_id}")
        print(f"Logger Serial : {self.config.logger_serial}")
        print(f"Target Topic  : {self.config.topic}")
        print(f"Listen Window : {self.duration} seconds")
        print("=" * 60)

        self.client = self._create_client()

        # Step 1: Connect to broker
        print(f"\n[INFO] Connecting to {self.config.broker}:{self.config.port}...")
        try:
            self.client.connect(self.config.broker, self.config.port, keepalive=60)
        except ConnectionRefusedError:
            self.connect_error_message = (
                f"Connection refused by {self.config.broker}:{self.config.port}. "
                "Check that the broker is running and accepting TCP connections."
            )
            print(f"[ERROR] {self.connect_error_message}")
            return self._build_summary()
        except TimeoutError:
            self.connect_error_message = (
                f"Connection timed out reaching {self.config.broker}:{self.config.port}. "
                "Check firewall, routing, or broker availability."
            )
            print(f"[ERROR] {self.connect_error_message}")
            return self._build_summary()
        except ssl.SSLError as e:
            self.connect_error_message = f"TLS handshake failed: {e}"
            print(f"[ERROR] {self.connect_error_message}")
            return self._build_summary()
        except Exception as e:
            self.connect_error_message = f"Failed to initiate connection: {e}"
            print(f"[ERROR] {self.connect_error_message}")
            return self._build_summary()

        # Start network loop in background thread
        self.client.loop_start()

        # Step 2: Await connection acknowledgment (up to 10 seconds)
        if not self.connected_event.wait(timeout=10.0):
            self.connect_error_message = "Connection acknowledgment timed out after 10s."
            print(f"[ERROR] {self.connect_error_message}")
            self._cleanup()
            return self._build_summary()

        if not self.connect_success:
            print(f"[ERROR] Connection failed: {self.connect_error_message}")
            self._cleanup()
            return self._build_summary()

        # Step 3: Await subscription acknowledgment (up to 10 seconds)
        if not self.subscribed_event.wait(timeout=10.0):
            self.subscribe_error_message = "Subscription acknowledgment timed out after 10s."
            print(f"[ERROR] {self.subscribe_error_message}")
            self._cleanup()
            return self._build_summary()

        if not self.subscribe_success:
            print(f"[ERROR] Subscription failed: {self.subscribe_error_message}")
            self._cleanup()
            return self._build_summary()

        # Step 4: Listen for incoming messages
        print(f"\n[INFO] Successfully subscribed to topic '{self.config.topic}'.")
        print(f"[INFO] Listening for messages for {self.duration} seconds (Press Ctrl+C to stop early)...")

        listen_start = time.time()
        try:
            while not self.stop_event.is_set():
                elapsed = time.time() - listen_start
                remaining = self.duration - elapsed
                if remaining <= 0:
                    print(f"\n[INFO] Configured test window ({self.duration}s) completed.")
                    break
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n[INFO] Interrupt received (Ctrl+C). Terminating listening test cleanly...")
        finally:
            self._cleanup()

        return self._build_summary()

    def _cleanup(self) -> None:
        """Gracefully disconnects and stops the client loop."""
        self.stop_event.set()
        self.end_time = datetime.datetime.now(datetime.timezone.utc)
        if self.client:
            try:
                if self.connect_success:
                    print("[INFO] Disconnecting from MQTT broker...")
                    self.client.disconnect()
            except Exception as e:
                logger.debug("Exception during disconnect: %s", e)
            try:
                self.client.loop_stop()
            except Exception as e:
                logger.debug("Exception stopping loop: %s", e)

    def _build_summary(self) -> dict[str, Any]:
        """Compiles test results into a structured dictionary."""
        if not self.end_time:
            self.end_time = datetime.datetime.now(datetime.timezone.utc)

        return {
            "broker": self.config.broker,
            "port": self.config.port,
            "topic": self.config.topic,
            "logger_serial": self.config.logger_serial,
            "use_tls": self.config.use_tls,
            "connect_success": self.connect_success,
            "connect_error": self.connect_error_message,
            "subscribe_success": self.subscribe_success,
            "subscribe_error": self.subscribe_error_message,
            "unexpected_disconnect": self.unexpected_disconnect,
            "disconnect_reason": self.disconnect_reason,
            "message_count": len(self.messages_received),
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "messages": self.messages_received,
        }


def print_summary_report(summary: dict[str, Any]) -> None:
    """Displays human-readable summary of the test."""
    print("\n" + "=" * 60)
    print("PHASE 1 MQTT TEST REPORT")
    print("=" * 60)
    print(f"Broker Host            : {summary['broker']}")
    print(f"Broker Port            : {summary['port']}")
    print(f"Target Topic           : {summary['topic']}")
    print(f"TLS Active             : {summary['use_tls']}")
    print(f"MQTT Connected         : {'YES' if summary['connect_success'] else 'NO'}")
    if summary['connect_error']:
        print(f"Connection Error       : {summary['connect_error']}")
    print(f"Topic Subscribed       : {'YES' if summary['subscribe_success'] else 'NO'}")
    if summary['subscribe_error']:
        print(f"Subscription Error     : {summary['subscribe_error']}")
    print(f"Unexpected Disconnect  : {'YES' if summary['unexpected_disconnect'] else 'NO'}")
    print(f"Messages Received      : {summary['message_count']}")

    if summary['connect_success'] and summary['subscribe_success']:
        if summary['message_count'] > 0:
            print("\nSTATUS: SUCCESSFUL. Connection, subscription, and live data reception confirmed.")
        else:
            print(
                "\nSTATUS: PARTIAL SUCCESS. MQTT connection and subscription succeeded, "
                "but 0 messages were received.\n"
                "Note: The solar logger may publish periodically (e.g. every 5–15 mins) "
                "or be offline/outside daylight hours."
            )
    else:
        print("\nSTATUS: FAILED. Check network reachability, broker credentials, or port settings.")
    print("=" * 60 + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 1: Solar MQTT Broker Connection and Verification Test."
    )
    parser.add_argument(
        "--duration",
        "-d",
        type=int,
        default=60,
        help="Listening duration in seconds (default: 60).",
    )
    parser.add_argument(
        "--env-file",
        "-e",
        type=str,
        default=None,
        help="Path to custom .env file (default: looks for .env in current directory).",
    )
    parser.add_argument(
        "--tls",
        action="store_true",
        help="Force TLS connection with certificate verification.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Step 1: Safe config loading
    try:
        config = load_config(env_path=args.env_file)
        if args.tls:
            # Override use_tls if requested via CLI flag
            config = MQTTConfig(
                broker=config.broker,
                port=config.port,
                username=config.username,
                password=config.password,
                logger_serial=config.logger_serial,
                topic=config.topic,
                use_tls=True,
            )
    except ConfigError as err:
        print(f"[FATAL CONFIG ERROR] {err}", file=sys.stderr)
        return 1
    except Exception as err:
        print(f"[FATAL ERROR] Unexpected configuration error: {err}", file=sys.stderr)
        return 1

    # Register signal handler for clean shutdown
    tester = SolarMQTTTester(config=config, duration=args.duration)

    def handle_sigint(sig, frame):
        print("\n[INFO] Interrupt signal caught. Stopping...")
        tester.stop_event.set()

    try:
        signal.signal(signal.SIGINT, handle_sigint)
    except (ValueError, AttributeError):
        pass  # On non-main thread or some windows environments

    summary = tester.run()
    print_summary_report(summary)

    if summary["connect_success"] and summary["subscribe_success"]:
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
