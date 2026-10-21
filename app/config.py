"""Configuration loader and validator for Solar MQTT connection."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when environment configuration is missing, malformed, or invalid."""
    pass


REQUIRED_VARS = [
    "MQTT_BROKER",
    "MQTT_PORT",
    "MQTT_USERNAME",
    "MQTT_PASSWORD",
    "LOGGER_SERIAL",
    "MQTT_TOPIC",
]


@dataclass(frozen=True)
class MQTTConfig:
    """Validated MQTT configuration container."""
    broker: str
    port: int
    username: str
    password: str
    logger_serial: str
    topic: str
    use_tls: bool = False

    def __repr__(self) -> str:
        # Guarantee that password is never exposed in logs or string representations
        return (
            f"MQTTConfig(broker={self.broker!r}, port={self.port}, "
            f"username={self.username!r}, password='***', "
            f"logger_serial={self.logger_serial!r}, topic={self.topic!r}, "
            f"use_tls={self.use_tls})"
        )

    def safe_summary(self) -> dict[str, str | int | bool]:
        """Returns non-sensitive metadata for diagnostics and logging."""
        return {
            "broker": self.broker,
            "port": self.port,
            "username_set": bool(self.username),
            "password_set": bool(self.password),
            "logger_serial": self.logger_serial,
            "topic": self.topic,
            "use_tls": self.use_tls,
        }


def load_config(env_path: Optional[Path | str] = None) -> MQTTConfig:
    """
    Loads and strictly validates MQTT configuration from the given .env file or environment.

    Raises ConfigError with clear, safe error messages if configuration is missing or invalid.
    Never exposes passwords or sensitive credentials in error messages.
    """
    target_path = Path(env_path) if env_path else Path(".env")

    if env_path is not None or target_path.exists():
        if not target_path.exists():
            raise ConfigError(
                f"Configuration file '{target_path}' not found. "
                "Please create one based on '.env.example'."
            )
        load_dotenv(dotenv_path=target_path, override=True)

    # Check for missing or empty required variables
    missing_vars = [
        var_name for var_name in REQUIRED_VARS
        if not os.getenv(var_name) or not os.getenv(var_name, "").strip()
    ]

    if missing_vars:
        raise ConfigError(
            f"Missing required environment variables in configuration: {', '.join(missing_vars)}. "
            "Please check your .env file."
        )

    # Validate and parse MQTT_PORT
    raw_port = os.getenv("MQTT_PORT", "").strip()
    try:
        port = int(raw_port)
        if not (1 <= port <= 65535):
            raise ValueError()
    except (ValueError, TypeError):
        raise ConfigError(
            f"Invalid MQTT_PORT: '{raw_port}'. Port must be an integer between 1 and 65535."
        )

    broker = os.getenv("MQTT_BROKER", "").strip()
    username = os.getenv("MQTT_USERNAME", "").strip()
    password = os.getenv("MQTT_PASSWORD", "").strip()
    logger_serial = os.getenv("LOGGER_SERIAL", "").strip()
    topic = os.getenv("MQTT_TOPIC", "").strip()

    # Optional TLS setting (defaults to False unless explicitly configured)
    use_tls_raw = os.getenv("MQTT_USE_TLS", "false").strip().lower()
    use_tls = use_tls_raw in ("true", "1", "yes")

    return MQTTConfig(
        broker=broker,
        port=port,
        username=username,
        password=password,
        logger_serial=logger_serial,
        topic=topic,
        use_tls=use_tls,
    )
