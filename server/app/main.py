import html
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db, init_db, SessionLocal
from app.models import AccessToken, Command, CommandLog, Device


PUBLIC_PATH_PREFIX = os.getenv("PUBLIC_PATH_PREFIX", "/gate-control").rstrip("/")
BASE_URL = os.getenv("BASE_URL", "").rstrip("/")

DEVICE_ID = os.getenv("DEVICE_ID", "gate-main")
DEVICE_SECRET = os.getenv("DEVICE_SECRET", os.getenv("DEVICE_TOKEN", ""))

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

COMMAND_RELAY_TIME_MS = int(os.getenv("COMMAND_RELAY_TIME_MS", "700"))
TOKEN_DEFAULT_VALID_HOURS = int(os.getenv("TOKEN_DEFAULT_VALID_HOURS", "72"))
OPEN_COOLDOWN_SECONDS = int(os.getenv("OPEN_COOLDOWN_SECONDS", "5"))


app = FastAPI(
    title="Gate Control",
    root_path=PUBLIC_PATH_PREFIX,
)


class AckRequest(BaseModel):
    device_id: Optional[str] = None
    command_id: Optional[str] = None
    status: str = "done"
    message: Optional[str] = None


class CreateTokenRequest(BaseModel):
    label: Optional[str] = None
    device_id: Optional[str] = None
    gate_target: str = "open_1"
    valid_hours: int = Field(default=TOKEN_DEFAULT_VALID_HOURS, ge=1, le=24 * 60)
    max_uses: Optional[int] = Field(default=3, ge=1, le=1000)
    open_cooldown_seconds: int = Field(default=OPEN_COOLDOWN_SECONDS, ge=0, le=3600)


class CreateDeviceRequest(BaseModel):
    device_id: str
    name: Optional[str] = None
    secret: Optional[str] = None
    is_active: bool = True


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


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

    if not token.is_active or token.status != "active":
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

    if token.valid_to < now:
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

    command = Command(
        command_id=str(uuid.uuid4()),
        device_id=token.device_id,
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


def render_page(title: str, body: str) -> str:
    return f"""
<!doctype html>
<html lang="pl">
<head>
    <meta charset="utf-8">
    <title>{html.escape(title)}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 520px;
            margin: 40px auto;
            padding: 20px;
            text-align: center;
            background: #f5f5f5;
            color: #222;
        }}

        .card {{
            background: #fff;
            border-radius: 16px;
            padding: 28px 22px;
            box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
        }}

        h1 {{
            margin-top: 0;
            font-size: 26px;
        }}

        p {{
            color: #555;
            line-height: 1.4;
        }}

        button {{
            width: 100%;
            font-size: 21px;
            padding: 18px 24px;
            border-radius: 14px;
            border: none;
            cursor: pointer;
            background: #222;
            color: white;
            margin-top: 14px;
        }}

        button.secondary {{
            background: #444;
        }}

        button.danger {{
            background: #6b1f1f;
        }}

        button:active {{
            transform: scale(0.98);
        }}

        a {{
            display: inline-block;
            margin-top: 20px;
            color: #222;
        }}

        .small {{
            margin-top: 20px;
            font-size: 12px;
            color: #888;
            word-break: break-all;
        }}
    </style>
</head>
<body>
    <div class="card">
        {body}
    </div>
</body>
</html>
"""


@app.on_event("startup")
def startup() -> None:
    init_db()

    db = SessionLocal()

    try:
        ensure_configured_device(db)
    finally:
        db.close()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "gate-control",
        "public_path_prefix": PUBLIC_PATH_PREFIX,
        "database": "sqlite",
        "device_id": DEVICE_ID,
        "device_secret_configured": bool(DEVICE_SECRET),
        "admin_token_configured": bool(ADMIN_TOKEN),
        "time": now_iso(),
    }


@app.get("/")
def index():
    return {
        "status": "ok",
        "message": "Gate Control server is running",
        "public_url": public_path("/"),
        "health_url": public_path("/health"),
        "create_token_endpoint": public_path("/admin/tokens"),
    }


@app.get("/brama/{token_value}", response_class=HTMLResponse)
def gate_page(
    token_value: str,
    request: Request,
    db: Session = Depends(get_db),
):
    token = validate_access_token(db, token_value, request)

    buttons = ""

    if token.gate_target == "open_both":
        options = [
            ("1", "Otwórz bramę 1", ""),
            ("2", "Otwórz bramę 2", "secondary"),
            ("both", "Otwórz obie", "danger"),
        ]
    else:
        options = [
            ("", gate_label(token.gate_target), ""),
        ]

    for gate, label, css_class in options:
        if gate:
            action = public_path(f"/brama/{token_value}/open/{gate}")
        else:
            action = public_path(f"/brama/{token_value}/open")

        class_attr = f' class="{css_class}"' if css_class else ""

        buttons += f"""
        <form method="post" action="{html.escape(action)}">
            <button{class_attr} type="submit">{html.escape(label)}</button>
        </form>
        """

    body = f"""
        <h1>Otwieranie bramy</h1>
        <p>Naciśnij przycisk, aby wysłać polecenie otwarcia.</p>
        {buttons}
        <div class="small">
            Ważny do: {html.escape(token.valid_to.isoformat())}<br>
            Użycia: {token.used_count} / {token.max_uses if token.max_uses is not None else "bez limitu"}
        </div>
    """

    return render_page("Otwieranie bramy", body)


@app.post("/brama/{token_value}/open", response_class=HTMLResponse)
def open_gate_default(
    token_value: str,
    request: Request,
    db: Session = Depends(get_db),
):
    return open_gate(token_value, None, request, db)


@app.post("/brama/{token_value}/open/{gate}", response_class=HTMLResponse)
def open_gate_route(
    token_value: str,
    gate: str,
    request: Request,
    db: Session = Depends(get_db),
):
    return open_gate(token_value, gate, request, db)


def open_gate(
    token_value: str,
    gate: Optional[str],
    request: Request,
    db: Session,
):
    token = validate_access_token(db, token_value, request)
    command = create_command_from_token(
        db,
        token=token,
        requested_gate=gate,
        request=request,
    )

    back_url = public_path(f"/brama/{token_value}")

    body = f"""
        <h1>Polecenie wysłane</h1>
        <p>Komenda <strong>{html.escape(command.command)}</strong> została zapisana. ESP32 odbierze ją przy następnym odpytywaniu serwera.</p>
        <a href="{html.escape(back_url)}">Wróć do przycisku</a>
        <div class="small">
            Command ID: {html.escape(command.command_id)}
        </div>
    """

    return render_page("Polecenie wysłane", body)


@app.get("/api/device/poll")
def device_poll(
    device_id: Optional[str] = None,
    x_device_id: Optional[str] = Header(default=None),
    x_device_secret: Optional[str] = Header(default=None),
    x_device_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    device = authenticate_device(
        db,
        header_device_id=x_device_id,
        query_device_id=device_id,
        body_device_id=None,
        x_device_secret=x_device_secret,
        x_device_token=x_device_token,
    )

    command = (
        db.query(Command)
        .filter(Command.device_id == device.device_id)
        .filter(Command.status == "pending")
        .order_by(Command.created_at.asc())
        .first()
    )

    if command is None:
        db.commit()

        return {
            "command": "none",
            "time": now_iso(),
        }

    command.status = "sent"
    command.sent_at = now_utc()
    command.delivered_count += 1

    log_event(
        db,
        event_type="command_delivered",
        status="sent",
        command=command,
        device_id=device.device_id,
        message="Command delivered to device",
    )

    db.commit()

    return {
        "command": command.command,
        "command_id": command.command_id,
        "relay_time_ms": command.relay_time_ms,
        "created_at": command.created_at.isoformat(),
        "time": now_iso(),
    }


@app.post("/api/device/ack")
def device_ack(
    payload: AckRequest,
    x_device_id: Optional[str] = Header(default=None),
    x_device_secret: Optional[str] = Header(default=None),
    x_device_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    device = authenticate_device(
        db,
        header_device_id=x_device_id,
        query_device_id=None,
        body_device_id=payload.device_id,
        x_device_secret=x_device_secret,
        x_device_token=x_device_token,
    )

    if not payload.command_id:
        raise HTTPException(status_code=400, detail="Missing command_id")

    command = (
        db.query(Command)
        .filter(Command.command_id == payload.command_id)
        .filter(Command.device_id == device.device_id)
        .first()
    )

    if command is None:
        log_event(
            db,
            event_type="ack_rejected",
            status="not_found",
            device_id=device.device_id,
            message=f"Unknown command_id={payload.command_id}",
        )
        db.commit()

        raise HTTPException(status_code=404, detail="Command not found")

    command.status = payload.status or "done"
    command.ack_at = now_utc()
    command.message = payload.message

    log_event(
        db,
        event_type="command_ack",
        status=command.status,
        command=command,
        device_id=device.device_id,
        message=payload.message,
    )

    db.commit()

    return {
        "status": "ok",
        "ack": {
            "device_id": device.device_id,
            "command_id": command.command_id,
            "status": command.status,
            "ack_at": command.ack_at.isoformat(),
        },
    }


@app.post("/admin/devices")
def admin_create_device(
    payload: CreateDeviceRequest,
    x_admin_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    check_admin_auth(x_admin_token)

    device = db.query(Device).filter(Device.device_id == payload.device_id).first()

    if device is None:
        device = Device(
            device_id=payload.device_id,
            name=payload.name,
            secret=payload.secret,
            is_active=payload.is_active,
        )
        db.add(device)
    else:
        device.name = payload.name
        device.secret = payload.secret
        device.is_active = payload.is_active

    db.commit()

    return {
        "status": "ok",
        "device_id": device.device_id,
        "is_active": device.is_active,
    }


@app.post("/admin/tokens")
def admin_create_token(
    payload: CreateTokenRequest,
    request: Request,
    x_admin_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    check_admin_auth(x_admin_token)

    device_id = payload.device_id or DEVICE_ID

    device = db.query(Device).filter(Device.device_id == device_id).first()

    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    gate_target = normalize_gate_target(payload.gate_target)

    token_value = secrets.token_urlsafe(32)
    valid_from = now_utc()
    valid_to = valid_from + timedelta(hours=payload.valid_hours)

    token = AccessToken(
        token_value=token_value,
        label=payload.label,
        device_id=device.device_id,
        gate_target=gate_target,
        status="active",
        is_active=True,
        valid_from=valid_from,
        valid_to=valid_to,
        max_uses=payload.max_uses,
        used_count=0,
        open_cooldown_seconds=payload.open_cooldown_seconds,
    )

    db.add(token)
    db.flush()

    log_event(
        db,
        event_type="token_created",
        request=request,
        status="active",
        token=token,
        message=f"Token created for {gate_target}",
    )

    db.commit()
    db.refresh(token)

    return {
        "status": "ok",
        "token": token.token_value,
        "public_url": public_url(f"/brama/{token.token_value}"),
        "device_id": token.device_id,
        "gate_target": token.gate_target,
        "valid_from": token.valid_from.isoformat(),
        "valid_to": token.valid_to.isoformat(),
        "max_uses": token.max_uses,
    }


@app.get("/admin/tokens")
def admin_list_tokens(
    x_admin_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    check_admin_auth(x_admin_token)

    tokens = (
        db.query(AccessToken)
        .order_by(AccessToken.created_at.desc())
        .limit(50)
        .all()
    )

    return {
        "tokens": [
            {
                "id": token.id,
                "label": token.label,
                "token": token.token_value,
                "public_url": public_url(f"/brama/{token.token_value}"),
                "device_id": token.device_id,
                "gate_target": token.gate_target,
                "status": token.status,
                "is_active": token.is_active,
                "valid_from": token.valid_from.isoformat(),
                "valid_to": token.valid_to.isoformat(),
                "used_count": token.used_count,
                "max_uses": token.max_uses,
            }
            for token in tokens
        ]
    }


@app.get("/admin/commands")
def admin_list_commands(
    x_admin_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    check_admin_auth(x_admin_token)

    commands = (
        db.query(Command)
        .order_by(Command.created_at.desc())
        .limit(50)
        .all()
    )

    return {
        "commands": [
            {
                "command_id": command.command_id,
                "device_id": command.device_id,
                "command": command.command,
                "status": command.status,
                "relay_time_ms": command.relay_time_ms,
                "delivered_count": command.delivered_count,
                "created_at": command.created_at.isoformat(),
                "sent_at": command.sent_at.isoformat() if command.sent_at else None,
                "ack_at": command.ack_at.isoformat() if command.ack_at else None,
                "message": command.message,
            }
            for command in commands
        ]
    }


@app.get("/debug/state")
def debug_state(db: Session = Depends(get_db)):
    pending_count = db.query(Command).filter(Command.status == "pending").count()
    sent_count = db.query(Command).filter(Command.status == "sent").count()
    done_count = db.query(Command).filter(Command.status == "done").count()

    last_command = (
        db.query(Command)
        .order_by(Command.created_at.desc())
        .first()
    )

    last_log = (
        db.query(CommandLog)
        .order_by(CommandLog.created_at.desc())
        .first()
    )

    return {
        "database": "sqlite",
        "public_path_prefix": PUBLIC_PATH_PREFIX,
        "device_id": DEVICE_ID,
        "device_secret_configured": bool(DEVICE_SECRET),
        "admin_token_configured": bool(ADMIN_TOKEN),
        "counts": {
            "pending": pending_count,
            "sent": sent_count,
            "done": done_count,
        },
        "last_command": {
            "command_id": last_command.command_id,
            "device_id": last_command.device_id,
            "command": last_command.command,
            "status": last_command.status,
            "created_at": last_command.created_at.isoformat(),
        } if last_command else None,
        "last_log": {
            "event_type": last_log.event_type,
            "status": last_log.status,
            "message": last_log.message,
            "created_at": last_log.created_at.isoformat(),
        } if last_log else None,
    }
