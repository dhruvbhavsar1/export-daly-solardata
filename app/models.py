"""Data models and in-memory buffer representations for MQTT data collection."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class CollectionStatus(str, Enum):
    """Status outcomes for an MQTT data collection run."""
    SUCCESS = "SUCCESS"
    NO_MESSAGES = "NO_MESSAGES"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    SUBSCRIPTION_FAILED = "SUBSCRIPTION_FAILED"
    DISCONNECTED_EARLY = "DISCONNECTED_EARLY"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class BufferedMessage:
    """
    In-memory representation of a single received MQTT message.

    Preserves the lossless raw payload bytes for subsequent parsing phases,
    along with arrival metadata and diagnostic JSON parsing outcomes.
    """
    index: int
    topic: str
    raw_payload: bytes
    received_at: datetime.datetime
    qos: int = 1
    retain: bool = False
    is_valid_json: bool = False
    parsed_json: Optional[Any] = None
    payload_preview: str = ""

    def __repr__(self) -> str:
        return (
            f"BufferedMessage(index={self.index}, topic={self.topic!r}, "
            f"bytes={len(self.raw_payload)}, valid_json={self.is_valid_json}, "
            f"received_at={self.received_at.isoformat()})"
        )


@dataclass
class CollectionResult:
    """
    Comprehensive result object encapsulating a complete collection window.

    Stores all buffered messages in memory for the lifetime of the run,
    along with detailed metadata, timing, and error tracking.
    """
    status: CollectionStatus
    broker: str
    port: int
    topic: str
    logger_serial: str
    use_tls: bool
    connect_success: bool = False
    connect_error: Optional[str] = None
    subscribe_success: bool = False
    subscribe_error: Optional[str] = None
    start_time: Optional[datetime.datetime] = None
    end_time: Optional[datetime.datetime] = None
    requested_duration: int = 1200
    elapsed_duration: float = 0.0
    message_count: int = 0
    malformed_json_count: int = 0
    latest_message_time: Optional[datetime.datetime] = None
    errors: list[str] = field(default_factory=list)
    messages: list[BufferedMessage] = field(default_factory=list)

    def safe_summary(self) -> dict[str, Any]:
        """Returns non-sensitive metadata for diagnostics and logging without exposing credentials."""
        return {
            "status": self.status.value,
            "broker": self.broker,
            "port": self.port,
            "topic": self.topic,
            "logger_serial": self.logger_serial,
            "use_tls": self.use_tls,
            "connect_success": self.connect_success,
            "connect_error": self.connect_error,
            "subscribe_success": self.subscribe_success,
            "subscribe_error": self.subscribe_error,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "requested_duration_sec": self.requested_duration,
            "elapsed_duration_sec": round(self.elapsed_duration, 2),
            "message_count": self.message_count,
            "malformed_json_count": self.malformed_json_count,
            "latest_message_time": self.latest_message_time.isoformat() if self.latest_message_time else None,
            "errors": list(self.errors),
        }
