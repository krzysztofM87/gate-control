from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint

from app.database import Base


def now_utc():
    return datetime.utcnow()


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(120), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=True)
    secret = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc, nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)


class AccessToken(Base):
    __tablename__ = "access_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token_value = Column(String(255), unique=True, index=True, nullable=False)

    label = Column(String(255), nullable=True)

    # Pola wyglądu pilota / etykiet przycisków
    pilot_title = Column(String(255), nullable=True)
    button_1_label = Column(String(120), nullable=True)
    button_2_label = Column(String(120), nullable=True)
    button_both_label = Column(String(120), nullable=True)

    device_id = Column(String(120), index=True, nullable=False)

    gate_target = Column(String(40), default="open_1", nullable=False)
    status = Column(String(40), default="active", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    valid_from = Column(DateTime(timezone=True), nullable=False)
    valid_to = Column(DateTime(timezone=True), nullable=False)
    valid_forever = Column(Boolean, default=False, nullable=False)

    max_uses = Column(Integer, nullable=True)
    max_uses_per_client = Column(Integer, nullable=True)
    client_validity_hours = Column(Integer, nullable=True)
    used_count = Column(Integer, default=0, nullable=False)
    open_cooldown_seconds = Column(Integer, default=5, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class TokenClientUsage(Base):
    __tablename__ = "token_client_usages"
    __table_args__ = (
        UniqueConstraint("token_id", "client_key", name="uq_token_client_usage"),
    )

    id = Column(Integer, primary_key=True, index=True)
    token_id = Column(Integer, index=True, nullable=False)
    client_key = Column(String(64), nullable=False)
    used_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)


class Command(Base):
    __tablename__ = "commands"

    id = Column(Integer, primary_key=True, index=True)
    command_id = Column(String(80), unique=True, index=True, nullable=False)

    device_id = Column(String(120), index=True, nullable=False)
    token_id = Column(Integer, nullable=True)

    command = Column(String(40), nullable=False)
    status = Column(String(40), default="pending", nullable=False)

    relay_time_ms = Column(Integer, default=700, nullable=False)
    delivered_count = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    ack_at = Column(DateTime(timezone=True), nullable=True)

    message = Column(Text, nullable=True)


class CommandLog(Base):
    __tablename__ = "command_logs"

    id = Column(Integer, primary_key=True, index=True)

    event_type = Column(String(80), index=True, nullable=False)
    status = Column(String(80), nullable=True)
    message = Column(Text, nullable=True)

    token_id = Column(Integer, nullable=True)
    token_value_prefix = Column(String(40), nullable=True)

    command_id = Column(String(80), index=True, nullable=True)
    device_id = Column(String(120), index=True, nullable=True)

    ip_address = Column(String(80), nullable=True)
    user_agent = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_utc, nullable=False)

