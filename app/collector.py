"""MQTT Data Collection Service with In-Memory Buffering.

Collects telemetry from a solar MQTT broker for a configurable duration (default: 20 minutes)
and buffers raw messages in memory for downstream processing.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
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
from app.models import BufferedMessage, CollectionResult, CollectionStatus

logger = logging.getLogger("solar_collector")


class MQTTDataCollector:
    """
    Service responsible for connecting to the MQTT broker, subscribing to the logger topic,
    and buffering incoming messages in memory during the collection window.
    """

    def __init__(
        self,
        config: MQTTConfig,
        duration: int = 1200,
        connect_timeout: float = 10.0,
        subscribe_timeout: float = 10.0,
    ) -> None:
        if duration <= 0:
            raise ValueError("Collection duration must be a positive integer.")

        self.config = config
        self.duration = duration
        self.connect_timeout = connect_timeout
        self.subscribe_timeout = subscribe_timeout

        self.client_id = f"solar-collector-{config.logger_serial}-{uuid.uuid4().hex[:6]}"
        self.client: Optional[mqtt.Client] = None

        # Synchronization primitives
        self.connected_event = threading.Event()
        self.subscribed_event = threading.Event()
        self.stop_event = threading.Event()
        self._buffer_lock = threading.Lock()

        # In-memory buffer and metrics
        self._buffer: list[BufferedMessage] = []
        self._connect_success = False
        self._connect_error: Optional[str] = None
        self._subscribe_success = False
        self._subscribe_error: Optional[str] = None
        self._unexpected_disconnect = False
        self._disconnect_reason: Optional[str] = None
        self._malformed_json_count = 0
        self._latest_message_time: Optional[datetime.datetime] = None
        self._errors: list[str] = []

        self._start_time: Optional[datetime.datetime] = None
        self._end_time: Optional[datetime.datetime] = None

    def _create_client(self) -> mqtt.Client:
        """Instantiates and configures the MQTT client."""
        if hasattr(mqtt, "CallbackAPIVersion"):
            client = mqtt.Client(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.client_id,
            )
        else:
            client = mqtt.Client(client_id=self.client_id)

        # Set credentials safely
        client.username_pw_set(self.config.username, self.config.password)

        # TLS configuration
        if self.config.use_tls:
            context = ssl.create_default_context()
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            client.tls_set_context(context)

        # Bind event callbacks
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
        """Callback executed upon receiving CONNACK from broker."""
        try:
            is_failure = getattr(reason_code, "is_failure", None)
            if is_failure is None:
                is_failure = (reason_code != 0)

            if not is_failure:
                self._connect_success = True
                # Subscribe to target topic with QoS 1
                res, mid = client.subscribe(self.config.topic, qos=1)
                if res != mqtt.MQTT_ERR_SUCCESS:
                    self._subscribe_error = f"Immediate subscription error: code {res}"
                    self._errors.append(self._subscribe_error)
                    self.subscribed_event.set()
            else:
                self._connect_success = False
                self._connect_error = f"Connection refused by broker: {reason_code}"
                self._errors.append(self._connect_error)
        except Exception as e:
            self._errors.append(f"Exception in on_connect callback: {e}")
        finally:
            self.connected_event.set()

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: Any,
        flags_or_rc: Any,
        reason_code: Any = None,
        properties: Any = None,
    ) -> None:
        """Callback executed upon disconnection."""
        try:
            rc = reason_code if reason_code is not None else flags_or_rc
            is_failure = getattr(rc, "is_failure", None)
            if is_failure is None:
                is_failure = (rc != 0)

            # Record unexpected disconnections if not stopped intentionally
            if not self.stop_event.is_set() and is_failure:
                self._unexpected_disconnect = True
                self._disconnect_reason = str(rc)
                self._errors.append(f"Unexpected broker disconnect: {rc}")
        except Exception as e:
            self._errors.append(f"Exception in on_disconnect callback: {e}")

    def _on_subscribe(
        self,
        client: mqtt.Client,
        userdata: Any,
        mid: int,
        reason_codes: Any,
        properties: Any = None,
    ) -> None:
        """Callback executed upon receiving SUBACK from broker."""
        try:
            first_code = reason_codes[0] if isinstance(reason_codes, list) and reason_codes else reason_codes
            is_failure = getattr(first_code, "is_failure", None)
            if is_failure is None:
                is_failure = (first_code >= 128) if isinstance(first_code, int) else False

            if not is_failure:
                self._subscribe_success = True
            else:
                self._subscribe_success = False
                self._subscribe_error = f"Subscription rejected by broker: {first_code}"
                self._errors.append(self._subscribe_error)
        except Exception as e:
            self._errors.append(f"Exception in on_subscribe callback: {e}")
        finally:
            self.subscribed_event.set()

    def _on_message(
        self,
        client: mqtt.Client,
        userdata: Any,
        message: mqtt.MQTTMessage,
    ) -> None:
        """
        Callback executed upon message arrival.

        Preserves lossless raw bytes and evaluates JSON validity without discarding data.
        Never crashes the network thread on unexpected payload structures.
        """
        try:
            received_at = datetime.datetime.now(datetime.timezone.utc)
            raw_payload = bytes(message.payload)
            payload_len = len(raw_payload)

            is_valid_json = False
            parsed_json = None
            preview = ""

            try:
                decoded_str = raw_payload.decode("utf-8")
                try:
                    parsed_json = json.loads(decoded_str)
                    is_valid_json = True
                    preview = decoded_str[:120] + ("..." if len(decoded_str) > 120 else "")
                except json.JSONDecodeError:
                    is_valid_json = False
                    preview = decoded_str[:120] + ("..." if len(decoded_str) > 120 else "")
            except UnicodeDecodeError:
                is_valid_json = False
                preview = f"<binary {raw_payload[:32].hex()}{'...' if payload_len > 32 else ''}>"

            with self._buffer_lock:
                idx = len(self._buffer) + 1
                if not is_valid_json:
                    self._malformed_json_count += 1

                self._latest_message_time = received_at
                buffered_msg = BufferedMessage(
                    index=idx,
                    topic=message.topic,
                    raw_payload=raw_payload,
                    received_at=received_at,
                    qos=message.qos,
                    retain=message.retain,
                    is_valid_json=is_valid_json,
                    parsed_json=parsed_json,
                    payload_preview=preview,
                )
                self._buffer.append(buffered_msg)

        except Exception as e:
            # Shield callback from unhandled crashes
            self._errors.append(f"Exception processing received message: {e}")

    def collect(self) -> CollectionResult:
        """
        Executes the MQTT collection process for the configured duration.

        Returns an in-memory CollectionResult containing all buffered messages and metadata.
        """
        self._start_time = datetime.datetime.now(datetime.timezone.utc)
        self.client = self._create_client()

        # Step 1: Initiate connection
        try:
            self.client.connect(self.config.broker, self.config.port, keepalive=60)
        except ConnectionRefusedError:
            self._connect_error = f"Connection refused by {self.config.broker}:{self.config.port}."
            self._errors.append(self._connect_error)
            return self._build_result(CollectionStatus.CONNECTION_FAILED)
        except TimeoutError:
            self._connect_error = f"Connection timed out reaching {self.config.broker}:{self.config.port}."
            self._errors.append(self._connect_error)
            return self._build_result(CollectionStatus.CONNECTION_FAILED)
        except ssl.SSLError as e:
            self._connect_error = f"TLS handshake failed: {e}"
            self._errors.append(self._connect_error)
            return self._build_result(CollectionStatus.CONNECTION_FAILED)
        except Exception as e:
            self._connect_error = f"Failed to connect: {e}"
            self._errors.append(self._connect_error)
            return self._build_result(CollectionStatus.CONNECTION_FAILED)

        # Start background loop
        self.client.loop_start()

        # Step 2: Await CONNACK with bounded timeout
        if not self.connected_event.wait(timeout=self.connect_timeout):
            self._connect_error = f"Connection acknowledgment timed out after {self.connect_timeout}s."
            self._errors.append(self._connect_error)
            self._cleanup()
            return self._build_result(CollectionStatus.CONNECTION_FAILED)

        if not self._connect_success:
            self._cleanup()
            return self._build_result(CollectionStatus.CONNECTION_FAILED)

        # Step 3: Await SUBACK with bounded timeout
        if not self.subscribed_event.wait(timeout=self.subscribe_timeout):
            self._subscribe_error = f"Subscription acknowledgment timed out after {self.subscribe_timeout}s."
            self._errors.append(self._subscribe_error)
            self._cleanup()
            return self._build_result(CollectionStatus.SUBSCRIPTION_FAILED)

        if not self._subscribe_success:
            self._cleanup()
            return self._build_result(CollectionStatus.SUBSCRIPTION_FAILED)

        # Step 4: Collection loop
        loop_start = time.time()
        cancelled = False
        try:
            while not self.stop_event.is_set():
                elapsed = time.time() - loop_start
                if elapsed >= self.duration:
                    break
                time.sleep(0.5)
        except KeyboardInterrupt:
            cancelled = True
            self.stop_event.set()
        finally:
            self._cleanup()

        # Determine outcome status
        if cancelled:
            status = CollectionStatus.CANCELLED
        elif self._unexpected_disconnect:
            status = CollectionStatus.DISCONNECTED_EARLY
        elif len(self._buffer) > 0:
            status = CollectionStatus.SUCCESS
        else:
            status = CollectionStatus.NO_MESSAGES

        return self._build_result(status)

    def stop(self) -> None:
        """Signals the collection loop to stop gracefully."""
        self.stop_event.set()

    def _cleanup(self) -> None:
        """Disconnects client cleanly and shuts down network loop."""
        self.stop_event.set()
        self._end_time = datetime.datetime.now(datetime.timezone.utc)
        if self.client:
            try:
                if self._connect_success:
                    self.client.disconnect()
            except Exception as e:
                logger.debug("Exception during disconnect: %s", e)
            try:
                self.client.loop_stop()
            except Exception as e:
                logger.debug("Exception stopping loop: %s", e)

    def _build_result(self, status: CollectionStatus) -> CollectionResult:
        """Compiles final CollectionResult containing the in-memory message buffer."""
        if not self._end_time:
            self._end_time = datetime.datetime.now(datetime.timezone.utc)

        start = self._start_time or self._end_time
        elapsed = (self._end_time - start).total_seconds()

        with self._buffer_lock:
            buffered_copy = list(self._buffer)
            count = len(buffered_copy)
            malformed = self._malformed_json_count
            latest = self._latest_message_time

        return CollectionResult(
            status=status,
            broker=self.config.broker,
            port=self.config.port,
            topic=self.config.topic,
            logger_serial=self.config.logger_serial,
            use_tls=self.config.use_tls,
            connect_success=self._connect_success,
            connect_error=self._connect_error,
            subscribe_success=self._subscribe_success,
            subscribe_error=self._subscribe_error,
            start_time=self._start_time,
            end_time=self._end_time,
            requested_duration=self.duration,
            elapsed_duration=elapsed,
            message_count=count,
            malformed_json_count=malformed,
            latest_message_time=latest,
            errors=list(self._errors),
            messages=buffered_copy,
        )


def print_collection_report(result: CollectionResult) -> None:
    """Prints a human-readable, safe summary of the collection run."""
    print("\n" + "=" * 65)
    print("PHASE 2: SOLAR MQTT INGESTION & BUFFERING REPORT")
    print("=" * 65)
    print(f"Collection Status       : {result.status.value}")
    print(f"Broker Host             : {result.broker}")
    print(f"Broker Port             : {result.port}")
    print(f"Target Topic            : {result.topic}")
    print(f"Logger Serial           : {result.logger_serial}")
    print(f"TLS Active              : {result.use_tls}")
    print(f"MQTT Connected          : {'YES' if result.connect_success else 'NO'}")
    if result.connect_error:
        print(f"Connect Error           : {result.connect_error}")
    print(f"Topic Subscribed        : {'YES' if result.subscribe_success else 'NO'}")
    if result.subscribe_error:
        print(f"Subscribe Error         : {result.subscribe_error}")
    print(f"Requested Duration      : {result.requested_duration} seconds ({result.requested_duration // 60} mins)")
    print(f"Actual Elapsed Duration : {result.elapsed_duration:.2f} seconds")
    print(f"Messages Buffered       : {result.message_count}")
    print(f"Malformed JSON Count    : {result.malformed_json_count}")
    if result.latest_message_time:
        print(f"Latest Message Received : {result.latest_message_time.isoformat()}")
    if result.errors:
        print(f"Encountered Warnings/Errors : {len(result.errors)}")
        for err in result.errors[:5]:
            print(f"  - {err}")

    print("-" * 65)
    if result.status == CollectionStatus.SUCCESS:
        print("SUMMARY: Successfully buffered live messages in memory for Phase 3.")
    elif result.status == CollectionStatus.NO_MESSAGES:
        print("SUMMARY: Connected and subscribed successfully, but no messages were")
        print("         published by the logger during the collection window.")
    elif result.status == CollectionStatus.CANCELLED:
        print("SUMMARY: Collection was interrupted by user (Ctrl+C). Clean shutdown performed.")
    else:
        print(f"SUMMARY: Collection ended with status {result.status.value}.")
    print("=" * 65 + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 2: Ingest and buffer solar MQTT messages for a collection window."
    )
    parser.add_argument(
        "--duration",
        "-d",
        type=int,
        default=1200,
        help="Collection window in seconds (default: 1200 seconds / 20 minutes).",
    )
    parser.add_argument(
        "--env-file",
        "-e",
        type=str,
        default=None,
        help="Path to custom .env file (default: loads .env from current directory).",
    )
    parser.add_argument(
        "--tls",
        action="store_true",
        help="Force TLS connection with certificate verification.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        config = load_config(env_path=args.env_file)
        if args.tls:
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
        print(f"[FATAL ERROR] Unexpected config error: {err}", file=sys.stderr)
        return 1

    collector = MQTTDataCollector(config=config, duration=args.duration)

    def handle_sigint(sig, frame):
        print("\n[INFO] Interrupt signal caught. Stopping collection gracefully...")
        collector.stop()

    try:
        signal.signal(signal.SIGINT, handle_sigint)
    except (ValueError, AttributeError):
        pass

    print("=" * 65)
    print("STARTING SOLAR MQTT DATA COLLECTION")
    print("=" * 65)
    print(f"Broker        : {config.broker}:{config.port}")
    print(f"Logger Serial : {config.logger_serial}")
    print(f"Topic         : {config.topic}")
    print(f"Window        : {args.duration}s ({args.duration // 60}m {args.duration % 60}s)")
    print("Press Ctrl+C to stop and report buffered messages early.")
    print("=" * 65)

    result = collector.collect()
    print_collection_report(result)

    # Return 0 for SUCCESS or NO_MESSAGES (network succeeded), non-zero on failure
    if result.status in (CollectionStatus.SUCCESS, CollectionStatus.NO_MESSAGES, CollectionStatus.CANCELLED):
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
