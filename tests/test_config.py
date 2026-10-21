"""Tests for environment configuration loading and validation."""

import os
from pathlib import Path
import pytest
from app.config import ConfigError, MQTTConfig, load_config


def test_missing_env_file(tmp_path: Path):
    non_existent = tmp_path / ".env.doesnotexist"
    with pytest.raises(ConfigError) as exc_info:
        load_config(env_path=non_existent)
    assert "not found" in str(exc_info.value).lower()


def test_missing_required_variables(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("MQTT_BROKER=127.0.0.1\nMQTT_PORT=1883\n", encoding="utf-8")

    # Clear any conflicting environment variables for isolation
    for var in ["MQTT_BROKER", "MQTT_PORT", "MQTT_USERNAME", "MQTT_PASSWORD", "LOGGER_SERIAL", "MQTT_TOPIC"]:
        os.environ.pop(var, None)

    with pytest.raises(ConfigError) as exc_info:
        load_config(env_path=env_file)
    err = str(exc_info.value)
    assert "missing required environment variables" in err.lower()
    assert "MQTT_USERNAME" in err
    assert "MQTT_PASSWORD" in err
    assert "LOGGER_SERIAL" in err
    assert "MQTT_TOPIC" in err


@pytest.mark.parametrize("invalid_port", ["abc", "-5", "0", "65536", "99999", "88.3"])
def test_invalid_port_values(tmp_path: Path, invalid_port: str):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"MQTT_BROKER=127.0.0.1\n"
        f"MQTT_PORT={invalid_port}\n"
        f"MQTT_USERNAME=testuser\n"
        f"MQTT_PASSWORD=testpass\n"
        f"LOGGER_SERIAL=TEST123456\n"
        f"MQTT_TOPIC=solar/test\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config(env_path=env_file)
    assert "invalid mqtt_port" in str(exc_info.value).lower()


def test_valid_config_loading(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MQTT_BROKER=mqtt.example.com\n"
        "MQTT_PORT=8883\n"
        "MQTT_USERNAME=my_user\n"
        "MQTT_PASSWORD=super_secret_password\n"
        "LOGGER_SERIAL=SERIAL999\n"
        "MQTT_TOPIC=solar/data/SERIAL999\n"
        "MQTT_USE_TLS=true\n",
        encoding="utf-8",
    )
    config = load_config(env_path=env_file)
    assert config.broker == "mqtt.example.com"
    assert config.port == 8883
    assert config.username == "my_user"
    assert config.password == "super_secret_password"
    assert config.logger_serial == "SERIAL999"
    assert config.topic == "solar/data/SERIAL999"
    assert config.use_tls is True


def test_password_never_exposed_in_repr_or_summary():
    config = MQTTConfig(
        broker="mqtt.example.com",
        port=8883,
        username="admin",
        password="TopSecretPassword123!",
        logger_serial="SERIAL001",
        topic="solar/001",
        use_tls=False,
    )
    # repr must not contain the real password
    repr_str = repr(config)
    assert "TopSecretPassword123!" not in repr_str
    assert "password='***'" in repr_str

    # summary must not contain password
    summary = config.safe_summary()
    assert "TopSecretPassword123!" not in str(summary)
    assert summary["password_set"] is True
