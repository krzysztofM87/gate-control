import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request, Response
from sqlalchemy import text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import (
    ADMIN_TOKEN,
    APP_TIMEZONE,
    BASE_URL,
    COMMAND_PENDING_TIMEOUT_SECONDS,
    COMMAND_RELAY_TIME_MS,
    DEVICE_ID,
    DEVICE_SECRET,
    PUBLIC_PATH_PREFIX,
)
from app.database import engine
from app.models import (
    AccessToken,
    Command,
    CommandLog,
    Device,
    TokenClientUsage,
    VirtualPilotButton,
)


CLIENT_COOKIE_NAME = "gate_control_client_id"
CLIENT_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365
MAX_VIRTUAL_PILOT_BUTTONS = 12

def now_utc() -> datetime:
    return datetime.utcnow()


def now_iso() -> str:
    return now_utc().isoformat()



def format_dt(value) -> str:
    if value is None:
        return ""

    # SQLite zwraca naive datetime. Traktujemy go jako UTC.
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))

    return value.astimezone(APP_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")

def public_path(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path

    return f"{PUBLIC_PATH_PREFIX}{path}"


def public_url(path: str) -> str:
    path = public_path(path)

    if BASE_URL:
        return f"{BASE_URL}{path}"

    return path


def request_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")

    if forwarded_for:
        return forwarded_for.split(",")[0].strip()

    if request.client:
        return request.client.host

    return ""


def token_prefix(token_value: Optional[str]) -> Optional[str]:
    if not token_value:
        return None

    return token_value[:10]


def client_id_from_request(request: Request) -> Optional[str]:
    client_id = (request.cookies.get(CLIENT_COOKIE_NAME) or "").strip()

    if 20 <= len(client_id) <= 128:
        return client_id

    return None


def ensure_client_id(request: Request) -> tuple[str, bool]:
    client_id = client_id_from_request(request)

    if client_id:
        return client_id, False

    return secrets.token_urlsafe(32), True


def set_client_id_cookie(response: Response, client_id: str) -> None:
    response.set_cookie(
        key=CLIENT_COOKIE_NAME,
        value=client_id,
        max_age=CLIENT_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        secure=BASE_URL.startswith("https://"),
        samesite="lax",
        path=PUBLIC_PATH_PREFIX or "/",
    )


def client_key(client_id: str) -> str:
    return hashlib.sha256(client_id.encode("utf-8")).hexdigest()


def get_client_usage(
    db: Session,
    *,
    token: AccessToken,
    request: Request,
) -> Optional[TokenClientUsage]:
    client_id = client_id_from_request(request)

    if not client_id:
        return None

    return (
        db.query(TokenClientUsage)
        .filter(TokenClientUsage.token_id == token.id)
        .filter(TokenClientUsage.client_key == client_key(client_id))
        .first()
    )


def client_usage_values(
    db: Session,
    *,
    token: AccessToken,
    request: Request,
) -> tuple[int, Optional[int]]:
    usage = get_client_usage(db, token=token, request=request)
    return (usage.used_count if usage else 0, token.max_uses_per_client)


def client_validity_values(
    db: Session,
    *,
    token: AccessToken,
    request: Request,
) -> tuple[Optional[int], Optional[datetime], bool]:
    validity_hours = token.client_validity_hours

    if validity_hours is None:
        return None, None, False

    usage = get_client_usage(db, token=token, request=request)

    if usage is None:
        return validity_hours, None, False

    valid_until = usage.created_at + timedelta(hours=validity_hours)
    return validity_hours, valid_until, now_utc() >= valid_until


def consume_client_usage(
    db: Session,
    *,
    token: AccessToken,
    request: Request,
) -> Optional[TokenClientUsage]:
    client_id = client_id_from_request(request)
    client_restrictions_enabled = (
        token.max_uses_per_client is not None
        or token.client_validity_hours is not None
    )

    if not client_id:
        if not client_restrictions_enabled:
            return None

        log_event(
            db,
            event_type="open_rejected",
            request=request,
            status="client_cookie_missing",
            token=token,
            message="Client cookie missing",
        )
        db.commit()
        raise HTTPException(
            status_code=400,
            detail="Odśwież stronę pilota, aby zarejestrować ten telefon",
        )

    usage_key = client_key(client_id)
    current_time = now_utc()

    db.execute(
        sqlite_insert(TokenClientUsage)
        .values(
            token_id=token.id,
            client_key=usage_key,
            used_count=0,
            created_at=current_time,
        )
        .on_conflict_do_nothing(index_elements=["token_id", "client_key"])
    )

    usage = (
        db.query(TokenClientUsage)
        .filter(TokenClientUsage.token_id == token.id)
        .filter(TokenClientUsage.client_key == usage_key)
        .first()
    )

    if usage is None:
        raise HTTPException(status_code=500, detail="Unable to register client device")

    if token.client_validity_hours is not None:
        valid_until = usage.created_at + timedelta(hours=token.client_validity_hours)

        if current_time >= valid_until:
            log_event(
                db,
                event_type="open_rejected",
                request=request,
                status="client_validity_expired",
                token=token,
                message="Per-client validity expired",
            )
            db.commit()
            raise HTTPException(
                status_code=403,
                detail="Ważność pilota na tym telefonie wygasła",
            )

    usage_update = (
        update(TokenClientUsage)
        .where(TokenClientUsage.token_id == token.id)
        .where(TokenClientUsage.client_key == usage_key)
        .values(
            used_count=TokenClientUsage.used_count + 1,
            last_used_at=current_time,
        )
    )

    if token.max_uses_per_client is not None:
        usage_update = usage_update.where(
            TokenClientUsage.used_count < token.max_uses_per_client
        )

    result = db.execute(usage_update)

    if result.rowcount != 1:
        log_event(
            db,
            event_type="open_rejected",
            request=request,
            status="client_use_limit_reached",
            token=token,
            message="Per-client use limit reached",
        )
        db.commit()
        raise HTTPException(
            status_code=403,
            detail="Limit użyć na tym telefonie został wykorzystany",
        )

    return get_client_usage(db, token=token, request=request)


def normalize_gate_target(value: str) -> str:
    value = value.strip().lower()

    if value in ("1", "gate1", "brama1", "open_1"):
        return "open_1"

    if value in ("2", "gate2", "brama2", "open_2"):
        return "open_2"

    if value in ("both", "3", "all", "obie", "open_both"):
        return "open_both"

    raise HTTPException(status_code=400, detail="Invalid gate target")


def gate_label(command: str) -> str:
    if command == "open_1":
        return "Otwórz bramę 1"

    if command == "open_2":
        return "Otwórz bramę 2"

    if command == "open_both":
        return "Otwórz obie"

    return "Otwórz bramę"


def log_event(
    db: Session,
    *,
    event_type: str,
    request: Optional[Request] = None,
    status: Optional[str] = None,
    message: Optional[str] = None,
    token: Optional[AccessToken] = None,
    token_value: Optional[str] = None,
    command: Optional[Command] = None,
    device_id: Optional[str] = None,
) -> None:
    entry = CommandLog(
        event_type=event_type,
        status=status,
        message=message,
        token_id=token.id if token else None,
        token_value_prefix=token_prefix(token.token_value if token else token_value),
        command_id=command.command_id if command else None,
        device_id=device_id or (command.device_id if command else None) or (token.device_id if token else None),
        ip_address=request_ip(request) if request else None,
        user_agent=request.headers.get("user-agent") if request else None,
    )

    db.add(entry)


def expire_pending_command(
    db: Session,
    command: Command,
    *,
    current_time: Optional[datetime] = None,
) -> bool:
    if command.status != "pending":
        return False

    current_time = current_time or now_utc()
    deadline = command.created_at + timedelta(seconds=COMMAND_PENDING_TIMEOUT_SECONDS)

    if current_time < deadline:
        return False

    command.status = "failed"
    command.message = (
        f"Command expired after {COMMAND_PENDING_TIMEOUT_SECONDS} seconds before delivery"
    )

    log_event(
        db,
        event_type="command_timeout",
        status="failed",
        command=command,
        message=command.message,
    )

    return True


def expire_pending_commands(
    db: Session,
    *,
    device_id: Optional[str] = None,
) -> int:
    current_time = now_utc()
    cutoff = current_time - timedelta(seconds=COMMAND_PENDING_TIMEOUT_SECONDS)
    query = (
        db.query(Command)
        .filter(Command.status == "pending")
        .filter(Command.created_at <= cutoff)
    )

    if device_id is not None:
        query = query.filter(Command.device_id == device_id)

    commands = query.all()

    for command in commands:
        expire_pending_command(db, command, current_time=current_time)

    return len(commands)


def check_admin_auth(x_admin_token: Optional[str]) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(
            status_code=403,
            detail="ADMIN_TOKEN is not configured on the server",
        )

    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid admin token")


def ensure_configured_device(db: Session) -> None:
    device = db.query(Device).filter(Device.device_id == DEVICE_ID).first()

    if device is None:
        device = Device(
            device_id=DEVICE_ID,
            name="Main gate device",
            secret=DEVICE_SECRET,
            is_active=True,
        )

        db.add(device)
        db.commit()
        return

    if DEVICE_SECRET and device.secret != DEVICE_SECRET:
        device.secret = DEVICE_SECRET

    device.is_active = True
    db.commit()


def authenticate_device(
    db: Session,
    *,
    header_device_id: Optional[str],
    query_device_id: Optional[str],
    body_device_id: Optional[str],
    x_device_secret: Optional[str],
    x_device_token: Optional[str],
) -> Device:
    provided_device_id = header_device_id or body_device_id or query_device_id

    if not provided_device_id:
        raise HTTPException(status_code=401, detail="Missing device id")

    device = db.query(Device).filter(Device.device_id == provided_device_id).first()

    if device is None:
        raise HTTPException(status_code=401, detail="Unknown device")

    if not device.is_active:
        raise HTTPException(status_code=403, detail="Device is inactive")

    provided_secret = x_device_secret or x_device_token

    if device.secret:
        if provided_secret != device.secret:
            raise HTTPException(status_code=401, detail="Invalid device secret")

    device.last_seen_at = now_utc()
    db.commit()
    # last_seen_at committed immediately

    return device


def validate_access_token(db: Session, token_value: str, request: Request) -> AccessToken:
    token = db.query(AccessToken).filter(AccessToken.token_value == token_value).first()

    if token is None:
        log_event(
            db,
            event_type="token_rejected",
            request=request,
            status="not_found",
            token_value=token_value,
            message="Token not found",
        )
        db.commit()
        raise HTTPException(status_code=404, detail="Token not found")

    now = now_utc()

    if not token.is_active:
        log_event(
            db,
            event_type="token_rejected",
            request=request,
            status="inactive",
            token=token,
            message="Token inactive",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="Token inactive")

    if token.valid_from > now:
        log_event(
            db,
            event_type="token_rejected",
            request=request,
            status="not_yet_valid",
            token=token,
            message="Token not yet valid",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="Token not yet valid")

    if (not getattr(token, "valid_forever", False)) and token.valid_to < now:
        token.status = "expired"
        log_event(
            db,
            event_type="token_rejected",
            request=request,
            status="expired",
            token=token,
            message="Token expired",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="Token expired")

    if token.max_uses is not None and token.used_count >= token.max_uses:
        token.status = "used"
        log_event(
            db,
            event_type="token_rejected",
            request=request,
            status="use_limit_reached",
            token=token,
            message="Token use limit reached",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="Token use limit reached")

    if token.status != "active":
        log_event(
            db,
            event_type="token_rejected",
            request=request,
            status="inactive",
            token=token,
            message=f"Token status is {token.status}",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="Token inactive")

    if token.last_used_at and token.open_cooldown_seconds > 0:
        elapsed = (now - token.last_used_at).total_seconds()

        if elapsed < token.open_cooldown_seconds:
            log_event(
                db,
                event_type="token_rejected",
                request=request,
                status="cooldown",
                token=token,
                message=f"Cooldown active: {elapsed:.1f}s",
            )
            db.commit()
            raise HTTPException(status_code=429, detail="Please wait before opening again")

    return token


def create_command_from_token(
    db: Session,
    *,
    token: AccessToken,
    requested_gate: Optional[str],
    request: Request,
) -> Command:
    if token.is_virtual:
        raise HTTPException(
            status_code=400,
            detail="Virtual pilot requires a configured button",
        )

    if requested_gate:
        requested_command = normalize_gate_target(requested_gate)

        if token.gate_target != "open_both" and requested_command != token.gate_target:
            log_event(
                db,
                event_type="open_rejected",
                request=request,
                status="gate_not_allowed",
                token=token,
                message=f"Requested {requested_command}, token allows {token.gate_target}",
            )
            db.commit()
            raise HTTPException(status_code=403, detail="Gate target not allowed by token")

        command_name = requested_command
    else:
        command_name = token.gate_target

    return create_command_for_target(
        db,
        token=token,
        device_id=token.device_id,
        command_name=command_name,
        request=request,
    )


def create_command_for_target(
    db: Session,
    *,
    token: AccessToken,
    device_id: str,
    command_name: str,
    request: Request,
) -> Command:
    consume_client_usage(db, token=token, request=request)

    command = Command(
        command_id=str(uuid.uuid4()),
        device_id=device_id,
        token_id=token.id,
        command=command_name,
        status="pending",
        relay_time_ms=COMMAND_RELAY_TIME_MS,
    )

    token.used_count += 1
    token.last_used_at = now_utc()

    if token.max_uses is not None and token.used_count >= token.max_uses:
        token.status = "used"

    db.add(command)
    db.flush()

    log_event(
        db,
        event_type="open_requested",
        request=request,
        status="pending",
        token=token,
        command=command,
        message=f"Command {command.command} created",
    )

    db.commit()
    db.refresh(command)

    return command


def virtual_buttons_for_token(
    db: Session,
    token_id: int,
) -> list[VirtualPilotButton]:
    return (
        db.query(VirtualPilotButton)
        .filter(VirtualPilotButton.token_id == token_id)
        .order_by(VirtualPilotButton.sort_order.asc(), VirtualPilotButton.id.asc())
        .all()
    )


def virtual_button_or_404(
    db: Session,
    *,
    token: AccessToken,
    button_id: int,
) -> VirtualPilotButton:
    button = (
        db.query(VirtualPilotButton)
        .filter(VirtualPilotButton.id == button_id)
        .filter(VirtualPilotButton.token_id == token.id)
        .first()
    )

    if button is None:
        raise HTTPException(status_code=404, detail="Virtual pilot button not found")

    return button


def create_command_from_virtual_button(
    db: Session,
    *,
    token: AccessToken,
    button_id: int,
    request: Request,
) -> Command:
    if not token.is_virtual:
        raise HTTPException(status_code=400, detail="Token is not a virtual pilot")

    button = virtual_button_or_404(db, token=token, button_id=button_id)
    device = db.query(Device).filter(Device.device_id == button.device_id).first()

    if device is None or not device.is_active:
        log_event(
            db,
            event_type="open_rejected",
            request=request,
            status="virtual_button_device_unavailable",
            token=token,
            device_id=button.device_id,
            message="Virtual pilot button device is unavailable",
        )
        db.commit()
        raise HTTPException(status_code=403, detail="Urządzenie tego przycisku jest niedostępne")

    return create_command_for_target(
        db,
        token=token,
        device_id=button.device_id,
        command_name=button.command,
        request=request,
    )


def create_virtual_pilot_button(
    db: Session,
    *,
    token: AccessToken,
    label: str,
    device_id: str,
    command: str,
    sort_order: int,
    request: Optional[Request] = None,
) -> VirtualPilotButton:
    if not token.is_virtual:
        raise HTTPException(status_code=400, detail="Token is not a virtual pilot")

    if db.query(VirtualPilotButton).filter(VirtualPilotButton.token_id == token.id).count() >= MAX_VIRTUAL_PILOT_BUTTONS:
        raise HTTPException(status_code=400, detail="Virtual pilot button limit reached")

    label = label.strip()

    if not label:
        raise HTTPException(status_code=400, detail="Button label is required")

    device = db.query(Device).filter(Device.device_id == device_id).first()

    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    if not device.is_active:
        raise HTTPException(status_code=400, detail="Device is inactive")

    button = VirtualPilotButton(
        token_id=token.id,
        label=label[:120],
        device_id=device.device_id,
        command=normalize_gate_target(command),
        sort_order=max(0, min(sort_order, 1000)),
    )
    db.add(button)
    db.flush()

    log_event(
        db,
        event_type="virtual_button_created",
        request=request,
        status="ok",
        token=token,
        device_id=button.device_id,
        message=f"Virtual button {button.id} created for {button.command}",
    )
    db.commit()
    db.refresh(button)
    return button


def update_virtual_pilot_button(
    db: Session,
    *,
    token: AccessToken,
    button: VirtualPilotButton,
    changes: dict,
    request: Optional[Request] = None,
) -> VirtualPilotButton:
    unknown_fields = set(changes) - {"label", "device_id", "command", "sort_order"}

    if unknown_fields:
        raise HTTPException(status_code=400, detail="Unsupported virtual button fields")

    if "label" in changes:
        label = str(changes["label"]).strip()

        if not label:
            raise HTTPException(status_code=400, detail="Button label is required")

        button.label = label[:120]

    if "device_id" in changes:
        device = db.query(Device).filter(Device.device_id == changes["device_id"]).first()

        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")

        if not device.is_active and device.device_id != button.device_id:
            raise HTTPException(status_code=400, detail="Device is inactive")

        button.device_id = device.device_id

    if "command" in changes:
        button.command = normalize_gate_target(str(changes["command"]))

    if "sort_order" in changes:
        button.sort_order = max(0, min(int(changes["sort_order"]), 1000))

    log_event(
        db,
        event_type="virtual_button_updated",
        request=request,
        status="ok",
        token=token,
        device_id=button.device_id,
        message=f"Virtual button {button.id} updated",
    )
    db.commit()
    db.refresh(button)
    return button


def delete_virtual_pilot_button(
    db: Session,
    *,
    token: AccessToken,
    button: VirtualPilotButton,
    request: Optional[Request] = None,
) -> None:
    button_id = button.id
    device_id = button.device_id
    db.delete(button)
    log_event(
        db,
        event_type="virtual_button_deleted",
        request=request,
        status="ok",
        token=token,
        device_id=device_id,
        message=f"Virtual button {button_id} deleted",
    )
    db.commit()


def delete_access_token(
    db: Session,
    *,
    token: AccessToken,
    request: Optional[Request] = None,
) -> dict:
    token_id = token.id
    token_label = token.label

    cancelled_commands = (
        db.query(Command)
        .filter(Command.token_id == token_id)
        .filter(Command.status.in_(["pending", "sent"]))
        .update(
            {
                Command.status: "cancelled",
                Command.message: "Cancelled because access token was deleted",
            },
            synchronize_session=False,
        )
    )
    deleted_client_usages = (
        db.query(TokenClientUsage)
        .filter(TokenClientUsage.token_id == token_id)
        .delete(synchronize_session=False)
    )
    deleted_virtual_buttons = (
        db.query(VirtualPilotButton)
        .filter(VirtualPilotButton.token_id == token_id)
        .delete(synchronize_session=False)
    )

    log_event(
        db,
        event_type="token_deleted",
        request=request,
        status="ok",
        token=token,
        message=(
            f"Token deleted; cancelled_commands={cancelled_commands}; "
            f"deleted_client_usages={deleted_client_usages}; "
            f"deleted_virtual_buttons={deleted_virtual_buttons}"
        ),
    )

    db.delete(token)
    db.commit()

    return {
        "token_id": token_id,
        "label": token_label,
        "cancelled_commands": cancelled_commands,
        "deleted_client_usages": deleted_client_usages,
        "deleted_virtual_buttons": deleted_virtual_buttons,
    }


def reactivate_access_token(
    db: Session,
    *,
    token: AccessToken,
    request: Optional[Request] = None,
) -> dict:
    current_time = now_utc()
    validity_duration = None

    if not token.valid_forever:
        validity_duration = token.valid_to - token.valid_from

        if validity_duration <= timedelta(0):
            raise HTTPException(status_code=400, detail="Token validity period is invalid")

    cancelled_commands = (
        db.query(Command)
        .filter(Command.token_id == token.id)
        .filter(Command.status.in_(["pending", "sent"]))
        .update(
            {
                Command.status: "cancelled",
                Command.message: "Cancelled because access token was reactivated",
            },
            synchronize_session=False,
        )
    )
    reset_client_usages = (
        db.query(TokenClientUsage)
        .filter(TokenClientUsage.token_id == token.id)
        .delete(synchronize_session=False)
    )

    token.is_active = True
    token.status = "active"
    token.used_count = 0
    token.last_used_at = None
    token.valid_from = current_time

    if validity_duration is not None:
        token.valid_to = current_time + validity_duration

    log_event(
        db,
        event_type="token_reactivated",
        request=request,
        status="active",
        token=token,
        message=(
            f"Token reactivated; cancelled_commands={cancelled_commands}; "
            f"reset_client_usages={reset_client_usages}"
        ),
    )

    db.commit()
    db.refresh(token)

    return {
        "token_id": token.id,
        "status": token.status,
        "cancelled_commands": cancelled_commands,
        "reset_client_usages": reset_client_usages,
    }


def update_access_token(
    db: Session,
    *,
    token: AccessToken,
    changes: dict,
    request: Optional[Request] = None,
) -> dict:
    editable_fields = {
        "label",
        "pilot_title",
        "button_1_label",
        "button_2_label",
        "button_both_label",
        "device_id",
        "gate_target",
        "valid_to",
        "valid_forever",
        "max_uses",
        "max_uses_per_client",
        "client_validity_hours",
        "open_cooldown_seconds",
        "is_active",
    }
    unknown_fields = set(changes) - editable_fields

    if unknown_fields:
        raise HTTPException(status_code=400, detail="Unsupported token fields")

    original_device_id = token.device_id
    original_gate_target = token.gate_target

    if "device_id" in changes:
        device = db.query(Device).filter(Device.device_id == changes["device_id"]).first()

        if device is None:
            raise HTTPException(status_code=404, detail="Device not found")

        if not device.is_active and device.device_id != token.device_id:
            raise HTTPException(status_code=400, detail="Device is inactive")

        changes["device_id"] = device.device_id

    if "gate_target" in changes:
        changes["gate_target"] = normalize_gate_target(changes["gate_target"])

    for field, value in changes.items():
        setattr(token, field, value)

    current_time = now_utc()

    if not token.is_active:
        token.status = "inactive"
    elif not token.valid_forever and token.valid_to < current_time:
        token.status = "expired"
    elif token.max_uses is not None and token.used_count >= token.max_uses:
        token.status = "used"
    else:
        token.status = "active"

    routing_changed = (
        token.device_id != original_device_id
        or token.gate_target != original_gate_target
    )
    cancelled_commands = 0

    if routing_changed or token.status != "active":
        cancelled_commands = (
            db.query(Command)
            .filter(Command.token_id == token.id)
            .filter(Command.status.in_(["pending", "sent"]))
            .update(
                {
                    Command.status: "cancelled",
                    Command.message: "Cancelled because access token was updated",
                },
                synchronize_session=False,
            )
        )

    log_event(
        db,
        event_type="token_updated",
        request=request,
        status=token.status,
        token=token,
        message=(
            f"Token updated fields={','.join(sorted(changes))}; "
            f"cancelled_commands={cancelled_commands}"
        ),
    )

    db.commit()
    db.refresh(token)

    return {
        "token_id": token.id,
        "status": token.status,
        "cancelled_commands": cancelled_commands,
        "updated_fields": sorted(changes),
    }


def run_schema_migrations() -> None:
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(access_tokens)")).mappings().all()
        columns = {row["name"] for row in rows}

        migrations = {
            "pilot_title": "ALTER TABLE access_tokens ADD COLUMN pilot_title VARCHAR(255)",
            "button_1_label": "ALTER TABLE access_tokens ADD COLUMN button_1_label VARCHAR(120)",
            "button_2_label": "ALTER TABLE access_tokens ADD COLUMN button_2_label VARCHAR(120)",
            "button_both_label": "ALTER TABLE access_tokens ADD COLUMN button_both_label VARCHAR(120)",
            "valid_forever": "ALTER TABLE access_tokens ADD COLUMN valid_forever BOOLEAN DEFAULT 0",
            "max_uses_per_client": "ALTER TABLE access_tokens ADD COLUMN max_uses_per_client INTEGER",
            "client_validity_hours": "ALTER TABLE access_tokens ADD COLUMN client_validity_hours INTEGER",
            "is_virtual": "ALTER TABLE access_tokens ADD COLUMN is_virtual BOOLEAN DEFAULT 0",
        }

        for name, sql in migrations.items():
            if name not in columns:
                conn.execute(text(sql))

def device_or_404(db: Session, device_id: str) -> Device:
    device = db.query(Device).filter(Device.device_id == device_id).first()

    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    return device


def device_counts(db: Session, device_id: str) -> dict:
    physical_token_ids = {
        token_id
        for (token_id,) in (
            db.query(AccessToken.id)
            .filter(AccessToken.device_id == device_id)
            .filter(AccessToken.is_virtual.is_(False))
            .all()
        )
    }
    virtual_token_ids = {
        token_id
        for (token_id,) in (
            db.query(VirtualPilotButton.token_id)
            .filter(VirtualPilotButton.device_id == device_id)
            .distinct()
            .all()
        )
    }
    virtual_buttons_count = (
        db.query(VirtualPilotButton)
        .filter(VirtualPilotButton.device_id == device_id)
        .count()
    )
    tokens_count = len(physical_token_ids | virtual_token_ids)

    pending_count = (
        db.query(Command)
        .filter(Command.device_id == device_id)
        .filter(Command.status.in_(["pending", "sent"]))
        .count()
    )

    all_commands_count = db.query(Command).filter(Command.device_id == device_id).count()

    return {
        "tokens": tokens_count,
        "virtual_buttons": virtual_buttons_count,
        "pending_or_sent_commands": pending_count,
        "all_commands": all_commands_count,
    }
