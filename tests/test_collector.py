"""Automated unit and mocked tests for Phase 2 MQTT Data Collector and Buffering."""

import datetime
import json
from unittest.mock import MagicMock, patch
import pytest
import paho.mqtt.client as mqtt

from app.config import MQTTConfig
from app.collector import MQTTDataCollector
from app.models import BufferedMessage, CollectionResult, CollectionStatus


def get_mock_config() -> MQTTConfig:
    return MQTTConfig(
        broker="mqtt.testbroker.org",
        port=1883,
        username="solar_collector_user",
        password="CollectorSecretPassword987!",
        logger_serial="SERIAL_TEST_007",
        topic="solar/SERIAL_TEST_007",
        use_tls=False,
    )


def test_collector_configurable_duration_and_short_windows():
    config = get_mock_config()
    # Default duration must be 1200 seconds (20 minutes)
    default_collector = MQTTDataCollector(config=config)
    assert default_collector.duration == 1200

    # Custom short window for development/testing
    short_collector = MQTTDataCollector(config=config, duration=5)
    assert short_collector.duration == 5

    # Invalid duration should raise ValueError
    with pytest.raises(ValueError):
        MQTTDataCollector(config=config, duration=0)
    with pytest.raises(ValueError):
        MQTTDataCollector(config=config, duration=-10)


def test_collector_default_session_is_persistent():
    """By default the collector uses stable client ID solar_raw_{serial} and clean_session=False."""
    config = get_mock_config()
    collector = MQTTDataCollector(config=config)

    # Default: stable deterministic client ID matching mqtt_raw_capture.py
    assert collector.client_id == f"solar_raw_{config.logger_serial}"
    assert collector.fresh_session is False

    # fresh_session mode: random UUID suffix is appended
    fresh_collector = MQTTDataCollector(config=config, fresh_session=True)
    assert fresh_collector.client_id.startswith(f"solar_raw_{config.logger_serial}-")
    # The suffix must be exactly 6 hex chars
    suffix = fresh_collector.client_id.split("-")[-1]
    assert len(suffix) == 6
    assert all(c in "0123456789abcdef" for c in suffix)
    assert fresh_collector.fresh_session is True


def test_collector_client_setup_matches_reference():
    """Verifies that _create_client() sets client_id, clean_session=False, and callbacks."""
    config = get_mock_config()
    collector = MQTTDataCollector(config=config)
    client = collector._create_client()

    assert client._clean_session is False
    assert collector.client_id == f"solar_raw_{config.logger_serial}"
    assert client.on_connect == collector._on_connect
    assert client.on_disconnect == collector._on_disconnect
    assert client.on_subscribe == collector._on_subscribe
    assert client.on_message == collector._on_message


def test_collector_successful_connect_and_subscribe():
    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=5)
    mock_client = MagicMock()
    mock_client.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    collector.client = mock_client

    # Simulate successful CONNACK
    mock_reason = MagicMock()
    mock_reason.is_failure = False
    mock_reason.__str__ = lambda self: "Success"
    collector._on_connect(mock_client, None, {}, mock_reason)

    assert collector._connect_success is True
    assert collector.connected_event.is_set()
    mock_client.subscribe.assert_called_once_with(config.topic, qos=1)

    # Simulate successful SUBACK
    mock_sub_reason = MagicMock()
    mock_sub_reason.is_failure = False
    mock_sub_reason.__str__ = lambda self: "Granted QoS 1"
    collector._on_subscribe(mock_client, None, 1, [mock_sub_reason])

    assert collector._subscribe_success is True
    assert collector.subscribed_event.is_set()


def test_collector_receipt_and_ordering_of_multiple_messages():
    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=5)

    # Send 5 sequential messages
    for i in range(1, 6):
        msg = MagicMock()
        msg.topic = config.topic
        msg.qos = 1
        msg.retain = False
        msg.payload = json.dumps({"seq": i, "val": i * 100}).encode("utf-8")
        collector._on_message(None, None, msg)

    result = collector._build_result(CollectionStatus.SUCCESS)
    assert result.message_count == 5
    assert len(result.messages) == 5

    # Check order preservation
    for i, buffered in enumerate(result.messages, start=1):
        assert buffered.index == i
        assert buffered.is_valid_json is True
        assert buffered.parsed_json["seq"] == i
        assert buffered.parsed_json["val"] == i * 100
        assert isinstance(buffered.received_at, datetime.datetime)


def test_collector_preserves_raw_payload_bytes_lossless():
    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=5)

    raw_binary_data = b"\x00\x01\x02\xff\xfe\xfa\xba\xbe"
    msg = MagicMock()
    msg.topic = config.topic
    msg.qos = 1
    msg.retain = False
    msg.payload = raw_binary_data

    collector._on_message(None, None, msg)
    result = collector._build_result(CollectionStatus.SUCCESS)

    assert result.message_count == 1
    buffered = result.messages[0]
    # Raw bytes must be preserved exactly without modification
    assert buffered.raw_payload == raw_binary_data
    assert isinstance(buffered.raw_payload, bytes)
    assert buffered.is_valid_json is False
    assert buffered.parsed_json is None


def test_collector_invalid_json_and_unexpected_encoding():
    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=5)

    # 1. Valid JSON
    msg1 = MagicMock()
    msg1.topic = config.topic
    msg1.qos = 1
    msg1.retain = False
    msg1.payload = b'{"temperature": 45.2, "status": "ok"}'

    # 2. Plain text (not valid JSON)
    msg2 = MagicMock()
    msg2.topic = config.topic
    msg2.qos = 1
    msg2.retain = False
    msg2.payload = b"PLAIN_TEXT_TELEMETRY_LINE"

    # 3. Invalid UTF-8 raw binary bytes
    msg3 = MagicMock()
    msg3.topic = config.topic
    msg3.qos = 1
    msg3.retain = False
    msg3.payload = b"\x80\x81\x82\x90\xff"

    collector._on_message(None, None, msg1)
    collector._on_message(None, None, msg2)
    collector._on_message(None, None, msg3)

    result = collector._build_result(CollectionStatus.SUCCESS)

    assert result.message_count == 3
    assert result.malformed_json_count == 2
    assert result.messages[0].is_valid_json is True
    assert result.messages[0].parsed_json["temperature"] == 45.2
    assert result.messages[1].is_valid_json is False
    assert result.messages[1].parsed_json is None
    assert result.messages[1].raw_payload == b"PLAIN_TEXT_TELEMETRY_LINE"
    assert result.messages[2].is_valid_json is False
    assert result.messages[2].parsed_json is None
    assert result.messages[2].raw_payload == b"\x80\x81\x82\x90\xff"


def test_collector_empty_collection():
    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=5)
    collector._connect_success = True
    collector._subscribe_success = True

    # Build result when no messages were received during duration
    result = collector._build_result(CollectionStatus.NO_MESSAGES)
    assert result.status == CollectionStatus.NO_MESSAGES
    assert result.message_count == 0
    assert result.messages == []
    assert result.latest_message_time is None


@patch("paho.mqtt.client.Client")
def test_collector_connection_timeout(mock_client_class):
    mock_instance = MagicMock()
    mock_instance.connect.side_effect = TimeoutError("Timed out")
    mock_client_class.return_value = mock_instance

    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=5)
    result = collector.collect()

    assert result.status == CollectionStatus.CONNECTION_FAILED
    assert result.connect_success is False
    assert "timed out" in result.connect_error.lower()


@patch("paho.mqtt.client.Client")
def test_collector_connection_refused(mock_client_class):
    mock_instance = MagicMock()
    mock_instance.connect.side_effect = ConnectionRefusedError("Connection refused")
    mock_client_class.return_value = mock_instance

    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=5)
    result = collector.collect()

    assert result.status == CollectionStatus.CONNECTION_FAILED
    assert result.connect_success is False
    assert "connection refused" in result.connect_error.lower()


def test_collector_subscription_failure():
    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=5)
    mock_client = MagicMock()
    collector.client = mock_client

    # Simulate SUBACK failure (QoS code 128 / failure reason code)
    mock_sub_reason = MagicMock()
    mock_sub_reason.is_failure = True
    mock_sub_reason.__str__ = lambda self: "Subscription not authorized"

    collector._on_subscribe(mock_client, None, 1, [mock_sub_reason])
    assert collector._subscribe_success is False
    assert "Subscription rejected" in collector._subscribe_error

    result = collector._build_result(CollectionStatus.SUBSCRIPTION_FAILED)
    assert result.status == CollectionStatus.SUBSCRIPTION_FAILED
    assert result.subscribe_success is False


def test_collector_unexpected_disconnect():
    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=5)

    # Broker disconnects unexpectedly during session
    mock_rc = MagicMock()
    mock_rc.is_failure = True
    mock_rc.__str__ = lambda self: "Broker connection lost"

    collector._on_disconnect(None, None, {}, mock_rc)
    assert collector._unexpected_disconnect is True
    assert any("Broker connection lost" in err for err in collector._errors)


def test_collector_cleanup_on_success_and_failure():
    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=5)
    mock_client = MagicMock()
    collector.client = mock_client
    collector._connect_success = True

    collector._cleanup()

    assert collector.stop_event.is_set()
    mock_client.disconnect.assert_called_once()
    mock_client.loop_stop.assert_called_once()


def test_collector_callback_exceptions_shielded():
    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=5)

    # Pass an object that causes an exception when accessing topic/payload
    bad_msg = MagicMock()
    type(bad_msg).payload = property(lambda self: (_ for _ in ()).throw(RuntimeError("Unexpected payload failure")))

    collector._on_message(None, None, bad_msg)

    # Collector should not raise exception, but record error in _errors
    assert len(collector._errors) == 1
    assert "Unexpected payload failure" in collector._errors[0]


def test_collector_correct_metadata_and_secret_masking():
    config = get_mock_config()
    collector = MQTTDataCollector(config=config, duration=1200)

    # Config repr never reveals secret
    assert "CollectorSecretPassword987!" not in repr(config)
    assert "password='***'" in repr(config)

    # Summary dictionary never reveals secret
    result = collector._build_result(CollectionStatus.NO_MESSAGES)
    summary = result.safe_summary()

    assert "CollectorSecretPassword987!" not in str(summary)
    assert "solar_collector_user" not in str(summary)  # safe summary only exposes non-credentials
    assert summary["status"] == "NO_MESSAGES"
    assert summary["requested_duration_sec"] == 1200
    assert summary["message_count"] == 0
