"""Mocked unit tests for SolarMQTTTester covering failure scenarios and message handling."""

import json
from unittest.mock import MagicMock, patch
import paho.mqtt.client as mqtt
from app.config import MQTTConfig
from app.mqtt_test import SolarMQTTTester


def get_dummy_config() -> MQTTConfig:
    return MQTTConfig(
        broker="mock.broker.local",
        port=1883,
        username="mock_user",
        password="mock_password",
        logger_serial="SERIAL_MOCK_123",
        topic="solar/SERIAL_MOCK_123",
        use_tls=False,
    )


def test_client_initialization():
    config = get_dummy_config()
    tester = SolarMQTTTester(config=config, duration=10)

    assert "SERIAL_MOCK_123" in tester.client_id
    assert tester.duration == 10
    assert not tester.connect_success
    assert not tester.subscribe_success
    assert len(tester.messages_received) == 0


@patch("paho.mqtt.client.Client")
def test_mocked_connection_timeout(mock_client_class):
    mock_instance = MagicMock()
    mock_instance.connect.side_effect = TimeoutError("Connection timed out")
    mock_client_class.return_value = mock_instance

    config = get_dummy_config()
    tester = SolarMQTTTester(config=config, duration=5)
    summary = tester.run()

    assert summary["connect_success"] is False
    assert "timed out" in summary["connect_error"].lower()


@patch("paho.mqtt.client.Client")
def test_mocked_connection_refused_socket(mock_client_class):
    mock_instance = MagicMock()
    mock_instance.connect.side_effect = ConnectionRefusedError("Connection refused")
    mock_client_class.return_value = mock_instance

    config = get_dummy_config()
    tester = SolarMQTTTester(config=config, duration=5)
    summary = tester.run()

    assert summary["connect_success"] is False
    assert "connection refused" in summary["connect_error"].lower()


def test_mocked_broker_auth_failure():
    """Simulates broker responding with an auth failure code."""
    config = get_dummy_config()
    tester = SolarMQTTTester(config=config, duration=5)

    mock_client = MagicMock()
    tester.client = mock_client

    # Simulate Paho v2 ReasonCode or v1 error code 135 / 5 (Not authorized / Bad user or pass)
    mock_reason = MagicMock()
    mock_reason.is_failure = True
    mock_reason.__str__ = lambda self: "Bad user name or password"

    tester._on_connect(mock_client, None, {}, mock_reason)

    assert tester.connect_success is False
    assert "Bad user name or password" in tester.connect_error_message
    assert tester.connected_event.is_set()


def test_mocked_successful_connection_and_subscription():
    """Simulates successful connect followed by successful subscribe confirmation."""
    config = get_dummy_config()
    tester = SolarMQTTTester(config=config, duration=5)

    mock_client = MagicMock()
    mock_client.subscribe.return_value = (mqtt.MQTT_ERR_SUCCESS, 1)
    tester.client = mock_client

    # Connect success (code 0 / is_failure=False)
    mock_reason = MagicMock()
    mock_reason.is_failure = False
    mock_reason.__str__ = lambda self: "Success"

    tester._on_connect(mock_client, None, {}, mock_reason)
    assert tester.connect_success is True
    mock_client.subscribe.assert_called_once_with("solar/SERIAL_MOCK_123", qos=1)

    # Subscribe confirmation (granted QoS 1)
    mock_sub_reason = MagicMock()
    mock_sub_reason.is_failure = False
    mock_sub_reason.__str__ = lambda self: "Granted QoS 1"

    tester._on_subscribe(mock_client, None, 1, [mock_sub_reason])
    assert tester.subscribe_success is True
    assert tester.subscribed_event.is_set()


def test_mocked_message_reception():
    """Simulates receiving valid JSON, text, and binary payloads."""
    config = get_dummy_config()
    tester = SolarMQTTTester(config=config, duration=5)

    # 1. JSON payload
    msg_json = MagicMock()
    msg_json.topic = config.topic
    msg_json.qos = 1
    msg_json.retain = 0
    payload_dict = {"voltage": 230.5, "current": 12.1, "power": 2789}
    msg_json.payload = json.dumps(payload_dict).encode("utf-8")

    tester._on_message(None, None, msg_json)

    assert len(tester.messages_received) == 1
    first = tester.messages_received[0]
    assert first["index"] == 1
    assert first["topic"] == config.topic
    assert "voltage" in first["payload_text"]

    # 2. Malformed / Binary payload
    msg_bin = MagicMock()
    msg_bin.topic = config.topic
    msg_bin.qos = 1
    msg_bin.retain = 0
    msg_bin.payload = b"\xff\xfe\x00\x01\x80"  # Invalid UTF-8

    tester._on_message(None, None, msg_bin)
    assert len(tester.messages_received) == 2
    second = tester.messages_received[1]
    assert "Binary data" in second["payload_text"]


def test_mocked_unexpected_disconnect():
    """Simulates broker unexpectedly disconnecting during active session."""
    config = get_dummy_config()
    tester = SolarMQTTTester(config=config, duration=5)

    mock_rc = MagicMock()
    mock_rc.is_failure = True
    mock_rc.__str__ = lambda self: "Connection lost / Keepalive timeout"

    tester._on_disconnect(None, None, {}, mock_rc)

    assert tester.unexpected_disconnect is True
    assert "Connection lost" in tester.disconnect_reason


def test_graceful_cleanup():
    """Verifies cleanup initiates disconnect and stops loop."""
    config = get_dummy_config()
    tester = SolarMQTTTester(config=config, duration=5)
    mock_client = MagicMock()
    tester.client = mock_client
    tester.connect_success = True

    tester._cleanup()

    assert tester.stop_event.is_set()
    mock_client.disconnect.assert_called_once()
    mock_client.loop_stop.assert_called_once()
