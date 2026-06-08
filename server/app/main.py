import html
import os
import secrets
import uuid
from datetime import datetime, timedelta
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

# SQLite ma valid_to jako NOT NULL, więc dla bezterminowych pilotów
# trzymamy technicznie daleką datę i flagę valid_forever.
FOREVER_VALID_TO = datetime(9999, 12, 31, 23, 59, 59)


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
    pilot_title: Optional[str] = None
    button_1_label: Optional[str] = None
    button_2_label: Optional[str] = None
    button_both_label: Optional[str] = None

    device_id: Optional[str] = None
    gate_target: str = "open_1"

    # null = bezterminowo
    valid_hours: Optional[int] = Field(default=TOKEN_DEFAULT_VALID_HOURS, ge=1, le=24 * 60)

    # null = bez limitu użyć
    max_uses: Optional[int] = Field(default=10, ge=1, le=1000)

    open_cooldown_seconds: int = Field(default=OPEN_COOLDOWN_SECONDS, ge=0, le=3600)


class CreateDeviceRequest(BaseModel):
    device_id: str
    name: Optional[str] = None
    secret: Optional[str] = None
    is_active: bool = True


def now_utc() -> datetime:
    return datetime.utcnow()


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
            Ważny do: {html.escape(token_valid_to_text(token))}<br>
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
    valid_forever = payload.valid_hours is None
    valid_to = FOREVER_VALID_TO if valid_forever else valid_from + timedelta(hours=payload.valid_hours)

    token = AccessToken(
        token_value=token_value,
        label=payload.label,
        device_id=device.device_id,
        gate_target=gate_target,
        status="active",
        is_active=True,
        valid_from=valid_from,
        valid_to=valid_to,
        valid_forever=valid_forever,
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
        "valid_to": None if getattr(token, "valid_forever", False) else token.valid_to.isoformat(),
        "valid_forever": getattr(token, "valid_forever", False),
        "max_uses": token.max_uses,
        "valid_forever": getattr(token, "valid_forever", False),
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
                "valid_to": None if getattr(token, "valid_forever", False) else token.valid_to.isoformat(),
        "valid_forever": getattr(token, "valid_forever", False),
                "used_count": token.used_count,
                "max_uses": token.max_uses,
        "valid_forever": getattr(token, "valid_forever", False),
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


def token_valid_to_text(token: AccessToken) -> str:
    if getattr(token, "valid_forever", False):
        return "bezterminowo"

    if token.valid_to is None:
        return "brak"

    return token.valid_to.isoformat()

# ===== Admin panel HTML =====

def admin_panel_token_from_request(request: Request) -> Optional[str]:
    return request.cookies.get("gate_admin_token") or request.query_params.get("admin_token")


def is_admin_panel_authorized(request: Request) -> bool:
    token = admin_panel_token_from_request(request)

    try:
        check_admin_auth(token)
        return True
    except HTTPException:
        return False


def display_pilot_title(token: AccessToken) -> str:
    return token.pilot_title or token.label or "Pilot do bramy"


def display_button_label(token: AccessToken, target: str) -> str:
    if target == "open_1":
        return token.button_1_label or ("Brama 1" if token.gate_target == "open_both" else "Otwórz")

    if target == "open_2":
        return token.button_2_label or ("Brama 2" if token.gate_target == "open_both" else "Otwórz")

    if target == "open_both":
        return token.button_both_label or "Obie"

    return "Otwórz"


def admin_panel_page(title: str, body: str) -> str:
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
            max-width: 1100px;
            margin: 32px auto;
            padding: 20px;
            background: #f5f5f5;
            color: #222;
        }}
        .card {{
            background: #fff;
            border-radius: 14px;
            padding: 22px;
            margin-bottom: 18px;
            box-shadow: 0 6px 20px rgba(0, 0, 0, 0.07);
        }}
        h1, h2 {{
            margin-top: 0;
        }}
        label {{
            display: block;
            margin-top: 12px;
            font-weight: bold;
        }}
        input, select {{
            width: 100%;
            box-sizing: border-box;
            padding: 10px;
            border: 1px solid #bbb;
            border-radius: 8px;
            font-size: 15px;
            margin-top: 4px;
        }}
        button {{
            padding: 12px 18px;
            border: 0;
            border-radius: 8px;
            background: #222;
            color: white;
            cursor: pointer;
            margin-top: 14px;
            font-size: 15px;
        }}
        a {{
            color: #111;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        th, td {{
            text-align: left;
            border-bottom: 1px solid #ddd;
            padding: 8px;
            vertical-align: top;
        }}
        code {{
            background: #eee;
            padding: 2px 4px;
            border-radius: 4px;
            word-break: break-all;
        }}
        .muted {{
            color: #666;
            font-size: 13px;
        }}
        .top {{
            display: flex;
            justify-content: space-between;
            gap: 12px;
            align-items: center;
        }}
        .danger {{
            background: #7a1f1f;
        }}
        .grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }}
        @media (max-width: 700px) {{
            .grid {{
                grid-template-columns: 1fr;
            }}
        }}
    </style>
</head>
<body>
    {body}
</body>
</html>
"""


def admin_login_page(error: Optional[str] = None) -> HTMLResponse:
    error_html = ""

    if error:
        error_html = f"<p style='color:#8b0000'><strong>{html.escape(error)}</strong></p>"

    body = f"""
    <div class="card">
        <h1>Gate Control - panel admina</h1>
        <p class="muted">Wpisz ADMIN_TOKEN z pliku .env.</p>
        {error_html}
        <form method="post" action="{public_path('/admin-panel/login')}">
            <label>Admin token</label>
            <input name="admin_token" type="password" autocomplete="off" required>
            <button type="submit">Zaloguj</button>
        </form>
    </div>
    """

    return HTMLResponse(admin_panel_page("Panel admina", body))


@app.get("/admin-panel", response_class=HTMLResponse)
def admin_panel(
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page()

    tokens = (
        db.query(AccessToken)
        .order_by(AccessToken.created_at.desc())
        .limit(30)
        .all()
    )

    commands = (
        db.query(Command)
        .order_by(Command.created_at.desc())
        .limit(20)
        .all()
    )

    token_rows = ""

    for token in tokens:
        url = public_url(f"/pilot/{token.token_value}")
        token_rows += f"""
        <tr>
            <td>{token.id}</td>
            <td>{html.escape(token.label or "")}</td>
            <td>{html.escape(display_pilot_title(token))}</td>
            <td><code>{html.escape(token.gate_target)}</code></td>
            <td>{html.escape(token.status)}</td>
            <td>{token.used_count} / {token.max_uses if token.max_uses is not None else "∞"}</td>
            <td>{html.escape(token_valid_to_text(token))}</td>
            <td><a href="{html.escape(url)}" target="_blank">pilot</a><br><code>{html.escape(url)}</code></td>
        </tr>
        """

    if not token_rows:
        token_rows = "<tr><td colspan='8'>Brak tokenów.</td></tr>"

    command_rows = ""

    for command in commands:
        command_rows += f"""
        <tr>
            <td><code>{html.escape(command.command_id)}</code></td>
            <td>{html.escape(command.device_id)}</td>
            <td><code>{html.escape(command.command)}</code></td>
            <td>{html.escape(command.status)}</td>
            <td>{command.delivered_count}</td>
            <td>{html.escape(command.created_at.isoformat())}</td>
            <td>{html.escape(command.ack_at.isoformat()) if command.ack_at else ""}</td>
        </tr>
        """

    if not command_rows:
        command_rows = "<tr><td colspan='7'>Brak komend.</td></tr>"

    body = f"""
    <div class="top">
        <h1>Gate Control - panel admina</h1>
        <form method="post" action="{public_path('/admin-panel/logout')}">
            <button class="danger" type="submit">Wyloguj</button>
        </form>
    </div>

    <div class="card">
        <h2>Usuń wszystkie tokeny</h2>
        <p class="muted">
            Usuwa wszystkie linki/piloty dostępu. Historia komend i logi zostają.
            Oczekujące komendy zostaną anulowane.
        </p>

        <form method="post" action="/gate-control/admin-panel/tokens/delete-all">
            <label>Potwierdzenie</label>
            <input name="confirm" placeholder="Wpisz: USUN" autocomplete="off">

            <button class="danger" type="submit">Usuń wszystkie tokeny</button>
        </form>
    </div>

    <div class="card">
        <h2>Utwórz pilota / link</h2>

        <form method="post" action="{public_path('/admin-panel/tokens')}">
            <div class="grid">
                <div>
                    <label>Opis techniczny</label>
                    <input name="label" value="test link">
                </div>
                <div>
                    <label>Nazwa pilota wyświetlana klientowi</label>
                    <input name="pilot_title" value="Pilot do bramy">
                </div>
            </div>

            <div class="grid">
                <div>
                    <label>Urządzenie</label>
                    <input name="device_id" value="{html.escape(DEVICE_ID)}">
                </div>
                <div>
                    <label>Brama / kanał</label>
                    <select name="gate_target">
                        <option value="open_1">Tylko przycisk 1 / GPIO26</option>
                        <option value="open_2">Tylko przycisk 2 / GPIO27</option>
                        <option value="open_both">Pilot z trzema przyciskami</option>
                    </select>
                </div>
            </div>

            <div class="grid">
                <div>
                    <label>Nazwa przycisku 1</label>
                    <input name="button_1_label" value="Brama 1">
                </div>
                <div>
                    <label>Nazwa przycisku 2</label>
                    <input name="button_2_label" value="Brama 2">
                </div>
            </div>

            <label>Nazwa przycisku „obie”</label>
            <input name="button_both_label" value="Obie bramy">

            <div class="grid">
                <div>
                    <label>Ważność w godzinach, puste = bezterminowo</label>
                    <input name="valid_hours" type="number" value="{TOKEN_DEFAULT_VALID_HOURS}" min="1" max="1440" placeholder="puste = bezterminowo">
                </div>
                <div>
                    <label>Limit użyć, puste = bez limitu</label>
                    <input name="max_uses" type="number" value="10" min="1" max="1000">
                </div>
            </div>

            <label>Cooldown w sekundach</label>
            <input name="open_cooldown_seconds" type="number" value="{OPEN_COOLDOWN_SECONDS}" min="0" max="3600">

            <button type="submit">Utwórz pilota</button>
        </form>
    </div>

    <div class="card">
        <h2>Ostatnie tokeny / piloty</h2>
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Etykieta</th>
                    <th>Nazwa pilota</th>
                    <th>Cel</th>
                    <th>Status</th>
                    <th>Użycia</th>
                    <th>Ważny do</th>
                    <th>Link</th>
                </tr>
            </thead>
            <tbody>
                {token_rows}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h2>Ostatnie komendy</h2>
        <table>
            <thead>
                <tr>
                    <th>Command ID</th>
                    <th>Device</th>
                    <th>Komenda</th>
                    <th>Status</th>
                    <th>Dostarczono</th>
                    <th>Utworzono</th>
                    <th>ACK</th>
                </tr>
            </thead>
            <tbody>
                {command_rows}
            </tbody>
        </table>
    </div>
    """

    return HTMLResponse(admin_panel_page("Panel admina", body))


@app.post("/admin-panel/login", response_class=HTMLResponse)
async def admin_panel_login(request: Request):
    form = await request.form()
    admin_token = str(form.get("admin_token") or "")

    try:
        check_admin_auth(admin_token)
    except HTTPException:
        return admin_login_page("Nieprawidłowy admin token.")

    response = HTMLResponse(
        admin_panel_page(
            "Zalogowano",
            f"""
            <div class="card">
                <h1>Zalogowano</h1>
                <p>Przejdź do panelu.</p>
                <a href="{public_path('/admin-panel')}">Otwórz panel</a>
            </div>
            """
        )
    )

    response.set_cookie(
        key="gate_admin_token",
        value=admin_token,
        max_age=60 * 60 * 12,
        httponly=True,
        samesite="lax",
    )

    return response


@app.post("/admin-panel/logout", response_class=HTMLResponse)
def admin_panel_logout():
    response = HTMLResponse(
        admin_panel_page(
            "Wylogowano",
            f"""
            <div class="card">
                <h1>Wylogowano</h1>
                <a href="{public_path('/admin-panel')}">Wróć do logowania</a>
            </div>
            """
        )
    )

    response.delete_cookie("gate_admin_token")
    return response


@app.post("/admin-panel/tokens", response_class=HTMLResponse)
async def admin_panel_create_token(
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    form = await request.form()

    label = str(form.get("label") or "")
    pilot_title = str(form.get("pilot_title") or "")
    button_1_label = str(form.get("button_1_label") or "")
    button_2_label = str(form.get("button_2_label") or "")
    button_both_label = str(form.get("button_both_label") or "")

    device_id = str(form.get("device_id") or DEVICE_ID)
    gate_target = normalize_gate_target(str(form.get("gate_target") or "open_1"))

    valid_hours_raw = str(form.get("valid_hours") or "").strip()

    if valid_hours_raw == "":
        valid_hours = None
        valid_forever = True
    else:
        try:
            valid_hours = int(valid_hours_raw)
        except ValueError:
            valid_hours = TOKEN_DEFAULT_VALID_HOURS
        valid_forever = False

    max_uses_raw = str(form.get("max_uses") or "").strip()
    if max_uses_raw == "":
        max_uses = None
    else:
        try:
            max_uses = int(max_uses_raw)
        except ValueError:
            max_uses = 10

        max_uses = max(1, min(max_uses, 1000))

    try:
        cooldown = int(form.get("open_cooldown_seconds") or OPEN_COOLDOWN_SECONDS)
    except ValueError:
        cooldown = OPEN_COOLDOWN_SECONDS
    cooldown = max(0, min(cooldown, 3600))

    device = db.query(Device).filter(Device.device_id == device_id).first()

    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    token_value = secrets.token_urlsafe(32)
    valid_from = now_utc()
    valid_to = FOREVER_VALID_TO if valid_forever else valid_from + timedelta(hours=valid_hours)

    token = AccessToken(
        token_value=token_value,
        label=label,
        pilot_title=pilot_title,
        button_1_label=button_1_label,
        button_2_label=button_2_label,
        button_both_label=button_both_label,
        device_id=device.device_id,
        gate_target=gate_target,
        status="active",
        is_active=True,
        valid_from=valid_from,
        valid_to=valid_to,
        valid_forever=valid_forever,
        max_uses=max_uses,
        used_count=0,
        open_cooldown_seconds=cooldown,
    )

    db.add(token)
    db.flush()

    log_event(
        db,
        event_type="token_created",
        request=request,
        status="active",
        token=token,
        message=f"Token created from admin panel for {gate_target}",
    )

    db.commit()
    db.refresh(token)

    url = public_url(f"/pilot/{token.token_value}")

    body = f"""
    <div class="card">
        <h1>Utworzono pilota</h1>
        <p><strong>{html.escape(display_pilot_title(token))}</strong></p>
        <p>Cel: <code>{html.escape(gate_target)}</code></p>
        <p><a href="{html.escape(url)}" target="_blank">Otwórz pilota</a></p>
        <p><code>{html.escape(url)}</code></p>
        <a href="{public_path('/admin-panel')}">Wróć do panelu</a>
    </div>
    """

    return HTMLResponse(admin_panel_page("Utworzono pilota", body))


# ===== Client pilot page =====

@app.get("/pilot/{token_value}", response_class=HTMLResponse)
def client_pilot_page(
    token_value: str,
    request: Request,
    db: Session = Depends(get_db),
):
    token = validate_access_token(db, token_value, request)

    if token.gate_target == "open_both":
        buttons = [
            ("1", display_button_label(token, "open_1"), "primary"),
            ("2", display_button_label(token, "open_2"), "secondary"),
            ("both", display_button_label(token, "open_both"), "danger"),
        ]
    elif token.gate_target == "open_2":
        buttons = [
            ("2", display_button_label(token, "open_2"), "primary"),
        ]
    else:
        buttons = [
            ("1", display_button_label(token, "open_1"), "primary"),
        ]

    buttons_html = ""

    for gate, label, css_class in buttons:
        press_url = public_path(f"/pilot/{token_value}/press/{gate}")

        buttons_html += f"""
        <button class="remote-button {css_class}" data-url="{html.escape(press_url)}">
            {html.escape(label)}
        </button>
        """

    max_uses_text = token.max_uses if token.max_uses is not None else "bez limitu"
    title = display_pilot_title(token)

    body = f"""
<!doctype html>
<html lang="pl">
<head>
    <meta charset="utf-8">
    <title>{html.escape(title)}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            min-height: 100vh;
            font-family: Arial, sans-serif;
            background: radial-gradient(circle at top, #333 0, #111 48%, #050505 100%);
            color: #f4f4f4;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 24px;
        }}
        .remote {{
            width: 100%;
            max-width: 360px;
            background: linear-gradient(180deg, #2d2d2d, #171717);
            border-radius: 36px;
            padding: 28px 22px 24px;
            box-shadow: 0 24px 60px rgba(0,0,0,.55), inset 0 1px 0 rgba(255,255,255,.12);
            border: 1px solid rgba(255,255,255,.08);
        }}
        .remote-header {{ text-align: center; margin-bottom: 22px; }}
        .remote-title {{ font-size: 24px; font-weight: 700; letter-spacing: .5px; margin: 0; }}
        .remote-subtitle {{ color: #aaa; font-size: 13px; margin-top: 7px; line-height: 1.35; }}
        .status {{
            min-height: 58px;
            background: #101010;
            border-radius: 16px;
            padding: 13px 12px;
            margin-bottom: 14px;
            text-align: center;
            color: #aaa;
            border: 1px solid rgba(255,255,255,.08);
            font-size: 14px;
            line-height: 1.35;
        }}
        .status.ok {{ color: #b7ffc9; border-color: rgba(75, 255, 120, .3); }}
        .status.wait {{ color: #ffe9a6; border-color: rgba(255, 220, 80, .35); }}
        .status.err {{ color: #ffc1c1; border-color: rgba(255, 80, 80, .35); }}
        .steps {{ display: grid; gap: 6px; margin-bottom: 18px; font-size: 13px; color: #777; }}
        .step {{ background: rgba(255,255,255,.04); border-radius: 10px; padding: 8px 10px; }}
        .step.active {{ color: #ffe9a6; }}
        .step.done {{ color: #b7ffc9; }}
        .buttons {{ display: grid; gap: 14px; }}
        .remote-button {{
            width: 100%;
            min-height: 78px;
            border: none;
            border-radius: 22px;
            color: white;
            font-size: 23px;
            font-weight: 700;
            letter-spacing: .4px;
            cursor: pointer;
            box-shadow: 0 9px 0 rgba(0,0,0,.28), inset 0 1px 0 rgba(255,255,255,.18);
            transition: transform .06s ease, box-shadow .06s ease, opacity .2s ease;
        }}
        .remote-button:active {{
            transform: translateY(6px);
            box-shadow: 0 3px 0 rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.12);
        }}
        .remote-button:disabled {{ opacity: .55; cursor: wait; }}
        .primary {{ background: linear-gradient(180deg, #2f7dff, #174aaf); }}
        .secondary {{ background: linear-gradient(180deg, #666, #343434); }}
        .danger {{ background: linear-gradient(180deg, #a43535, #641818); }}
        .footer {{
            margin-top: 20px;
            text-align: center;
            color: #777;
            font-size: 11px;
            word-break: break-all;
            line-height: 1.35;
        }}
        .led {{
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #2d2d2d;
            margin: 0 auto 14px;
            box-shadow: inset 0 1px 2px rgba(0,0,0,.8);
        }}
        .led.on {{ background: #39ff6a; box-shadow: 0 0 14px rgba(57,255,106,.8); }}
    </style>
</head>
<body>
    <main class="remote">
        <div id="led" class="led"></div>

        <div class="remote-header">
            <h1 class="remote-title">{html.escape(title)}</h1>
            <div class="remote-subtitle">Naciśnij przycisk, aby wysłać polecenie do sterownika.</div>
        </div>

        <div id="status" class="status">Gotowy</div>

        <div class="steps">
            <div id="step-created" class="step">1. Komenda zapisana na serwerze</div>
            <div id="step-sent" class="step">2. Sterownik odebrał komendę</div>
            <div id="step-done" class="step">3. Sterownik potwierdził wykonanie</div>
        </div>

        <div class="buttons">
            {buttons_html}
        </div>

        <div class="footer">
            Ważny do: {html.escape(token_valid_to_text(token))}<br>
            Użycia: <span id="usage-count">{token.used_count}</span> / <span id="usage-max">{html.escape(str(max_uses_text))}</span>
        </div>
    </main>

    <script>
        const statusEl = document.getElementById("status");
        const ledEl = document.getElementById("led");
        const buttons = Array.from(document.querySelectorAll(".remote-button"));
        const stepCreated = document.getElementById("step-created");
        const stepSent = document.getElementById("step-sent");
        const stepDone = document.getElementById("step-done");
        const usageCountEl = document.getElementById("usage-count");
        const usageMaxEl = document.getElementById("usage-max");

        let readyTimer = null;

        function setStatus(text, mode) {{
            statusEl.textContent = text;
            statusEl.className = "status" + (mode ? " " + mode : "");
        }}

        function clearReadyTimer() {{
            if (readyTimer) {{
                clearTimeout(readyTimer);
                readyTimer = null;
            }}
        }}

        function resetSteps() {{
            [stepCreated, stepSent, stepDone].forEach(step => step.className = "step");
        }}

        function scheduleReady() {{
            clearReadyTimer();
            readyTimer = setTimeout(() => {{
                resetSteps();
                setStatus("Gotowy", "");
            }}, 2500);
        }}

        function setStep(step, state) {{
            step.className = "step " + state;
        }}

        function setBusy(isBusy) {{
            buttons.forEach(button => button.disabled = isBusy);
            ledEl.classList.toggle("on", isBusy);
        }}

        function updateUsage(data) {{
            if (!data) return;

            if (typeof data.used_count !== "undefined" && data.used_count !== null) {{
                usageCountEl.textContent = data.used_count;
            }}

            if (typeof data.max_uses !== "undefined") {{
                usageMaxEl.textContent = data.max_uses === null ? "bez limitu" : data.max_uses;
            }}
        }}

        async function checkCommandStatus(statusUrl) {{
            const response = await fetch(statusUrl, {{
                method: "GET",
                headers: {{ "X-Requested-With": "fetch" }}
            }});

            const data = await response.json();

            if (!response.ok) {{
                throw new Error(data.detail || "Błąd statusu HTTP " + response.status);
            }}

            return data;
        }}

        async function watchCommandStatus(statusUrl) {{
            const maxAttempts = 20;

            for (let attempt = 0; attempt < maxAttempts; attempt++) {{
                const data = await checkCommandStatus(statusUrl);
                updateUsage(data);

                if (data.status === "pending") {{
                    setStep(stepCreated, "done");
                    setStep(stepSent, "active");
                    setStatus("Komenda zapisana. Czekam aż sterownik ją odbierze...", "wait");
                }}

                if (data.status === "sent") {{
                    setStep(stepCreated, "done");
                    setStep(stepSent, "done");
                    setStep(stepDone, "active");
                    setStatus("Sterownik odebrał komendę. Czekam na potwierdzenie...", "wait");
                }}

                if (data.status === "done") {{
                    setStep(stepCreated, "done");
                    setStep(stepSent, "done");
                    setStep(stepDone, "done");
                    setStatus("Wykonano. Sterownik potwierdził komendę.", "ok");

                    if (navigator.vibrate) {{
                        navigator.vibrate([60, 40, 60]);
                    }}

                    scheduleReady();
                    return;
                }}

                if (data.status !== "pending" && data.status !== "sent" && data.status !== "done") {{
                    setStatus("Status komendy: " + data.status, "err");
                    return;
                }}

                await new Promise(resolve => setTimeout(resolve, 700));
            }}

            setStatus("Komenda wysłana, ale brak potwierdzenia w oczekiwanym czasie.", "err");
        }}

        async function pressButton(url, label) {{
            clearReadyTimer();
            resetSteps();
            setBusy(true);
            setStatus("Wysyłam polecenie: " + label + "...", "wait");

            try {{
                const response = await fetch(url, {{
                    method: "POST",
                    headers: {{ "X-Requested-With": "fetch" }}
                }});

                const data = await response.json();
                updateUsage(data);

                if (!response.ok) {{
                    const message = data && data.detail ? data.detail : "Błąd HTTP " + response.status;
                    setStatus(message, "err");
                    return;
                }}

                if (data && data.status === "ok") {{
                    setStep(stepCreated, "done");
                    setStatus("Komenda zapisana na serwerze.", "wait");

                    if (data.status_url) {{
                        await watchCommandStatus(data.status_url);
                    }} else {{
                        setStatus("Polecenie wysłane: " + data.command, "ok");
                        scheduleReady();
                    }}

                    return;
                }}

                setStatus("Polecenie wysłane", "ok");
                scheduleReady();
            }} catch (err) {{
                setStatus("Błąd: " + err.message, "err");
            }} finally {{
                setTimeout(() => setBusy(false), 700);
            }}
        }}

        buttons.forEach(button => {{
            button.addEventListener("click", () => {{
                pressButton(button.dataset.url, button.textContent.trim());
            }});
        }});
    </script>
</body>
</html>
"""

    return HTMLResponse(body)


@app.post("/pilot/{token_value}/press/{gate}")
def client_pilot_press(
    token_value: str,
    gate: str,
    request: Request,
    db: Session = Depends(get_db),
):
    token = validate_access_token(db, token_value, request)

    command = create_command_from_token(
        db,
        token=token,
        requested_gate=gate,
        request=request,
    )

    db.refresh(token)

    return {
        "status": "ok",
        "command": command.command,
        "command_id": command.command_id,
        "relay_time_ms": command.relay_time_ms,
        "status_url": public_path(f"/pilot/{token_value}/command/{command.command_id}/status"),
        "used_count": token.used_count,
        "max_uses": token.max_uses,
        "valid_forever": getattr(token, "valid_forever", False),
        "token_status": token.status,
    }


@app.get("/pilot/{token_value}/command/{command_id}/status")
def client_pilot_command_status(
    token_value: str,
    command_id: str,
    db: Session = Depends(get_db),
):
    token = db.query(AccessToken).filter(AccessToken.token_value == token_value).first()

    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    command = (
        db.query(Command)
        .filter(Command.command_id == command_id)
        .filter(Command.token_id == token.id)
        .first()
    )

    if command is None:
        raise HTTPException(status_code=404, detail="Command not found")

    return {
        "command_id": command.command_id,
        "command": command.command,
        "status": command.status,
        "delivered_count": command.delivered_count,
        "created_at": command.created_at.isoformat() if command.created_at else None,
        "sent_at": command.sent_at.isoformat() if command.sent_at else None,
        "ack_at": command.ack_at.isoformat() if command.ack_at else None,
        "used_count": token.used_count,
        "max_uses": token.max_uses,
        "valid_forever": getattr(token, "valid_forever", False),
        "token_status": token.status,
    }

# ===== Delete all tokens endpoints =====

@app.post("/admin-panel/tokens/delete-all", response_class=HTMLResponse)
async def admin_panel_delete_all_tokens(
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    form = await request.form()
    confirm = str(form.get("confirm") or "").strip()

    if confirm != "USUN":
        body = f"""
        <div class="card">
            <h1>Nie usunięto tokenów</h1>
            <p>Potwierdzenie było nieprawidłowe. Trzeba wpisać dokładnie: <code>USUN</code></p>
            <a href="{public_path('/admin-panel')}">Wróć do panelu</a>
        </div>
        """
        return HTMLResponse(admin_panel_page("Nie usunięto tokenów", body))

    token_count = db.query(AccessToken).count()

    cancelled_commands = (
        db.query(Command)
        .filter(Command.status.in_(["pending", "sent"]))
        .update(
            {
                Command.status: "cancelled",
                Command.message: "Cancelled because all access tokens were deleted",
            },
            synchronize_session=False,
        )
    )

    db.query(AccessToken).delete(synchronize_session=False)

    log_event(
        db,
        event_type="tokens_deleted",
        request=request,
        status="ok",
        message=f"Deleted {token_count} tokens; cancelled {cancelled_commands} commands",
    )

    db.commit()

    body = f"""
    <div class="card">
        <h1>Usunięto tokeny</h1>
        <p>Usunięto tokenów: <strong>{token_count}</strong></p>
        <p>Anulowano oczekujących/wysłanych komend: <strong>{cancelled_commands}</strong></p>
        <a href="{public_path('/admin-panel')}">Wróć do panelu</a>
    </div>
    """

    return HTMLResponse(admin_panel_page("Usunięto tokeny", body))


@app.post("/admin/tokens/delete-all")
def admin_delete_all_tokens(
    confirm: str = "",
    x_admin_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    check_admin_auth(x_admin_token)

    if confirm != "USUN":
        raise HTTPException(status_code=400, detail="Confirmation required. Use confirm=USUN")

    token_count = db.query(AccessToken).count()

    cancelled_commands = (
        db.query(Command)
        .filter(Command.status.in_(["pending", "sent"]))
        .update(
            {
                Command.status: "cancelled",
                Command.message: "Cancelled because all access tokens were deleted",
            },
            synchronize_session=False,
        )
    )

    db.query(AccessToken).delete(synchronize_session=False)

    log_event(
        db,
        event_type="tokens_deleted",
        status="ok",
        message=f"Deleted {token_count} tokens; cancelled {cancelled_commands} commands",
    )

    db.commit()

    return {
        "status": "ok",
        "deleted_tokens": token_count,
        "cancelled_commands": cancelled_commands,
    }


# ===== Multi device admin panel =====

def device_or_404(db: Session, device_id: str) -> Device:
    device = db.query(Device).filter(Device.device_id == device_id).first()

    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    return device


def device_counts(db: Session, device_id: str) -> dict:
    tokens_count = db.query(AccessToken).filter(AccessToken.device_id == device_id).count()

    pending_count = (
        db.query(Command)
        .filter(Command.device_id == device_id)
        .filter(Command.status.in_(["pending", "sent"]))
        .count()
    )

    all_commands_count = db.query(Command).filter(Command.device_id == device_id).count()

    return {
        "tokens": tokens_count,
        "pending_or_sent_commands": pending_count,
        "all_commands": all_commands_count,
    }


@app.get("/admin/devices")
def admin_list_devices(
    x_admin_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    check_admin_auth(x_admin_token)

    devices = (
        db.query(Device)
        .order_by(Device.created_at.desc())
        .all()
    )

    return {
        "devices": [
            {
                "id": device.id,
                "device_id": device.device_id,
                "name": device.name,
                "secret": device.secret,
                "is_active": device.is_active,
                "created_at": device.created_at.isoformat() if device.created_at else None,
                "updated_at": device.updated_at.isoformat() if device.updated_at else None,
                "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
                "counts": device_counts(db, device.device_id),
            }
            for device in devices
        ]
    }


@app.get("/admin-panel/devices", response_class=HTMLResponse)
def admin_panel_devices(
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    devices = (
        db.query(Device)
        .order_by(Device.created_at.desc())
        .all()
    )

    rows = ""

    for device in devices:
        counts = device_counts(db, device.device_id)

        status_text = "aktywne" if device.is_active else "wyłączone"
        toggle_text = "Dezaktywuj" if device.is_active else "Aktywuj"

        secret_text = device.secret or ""

        rows += f"""
        <tr>
            <td>{device.id}</td>
            <td><code>{html.escape(device.device_id)}</code></td>
            <td>{html.escape(device.name or "")}</td>
            <td><code>{html.escape(secret_text)}</code></td>
            <td>{status_text}</td>
            <td>{counts["tokens"]}</td>
            <td>{counts["pending_or_sent_commands"]}</td>
            <td>{html.escape(device.last_seen_at.isoformat()) if device.last_seen_at else ""}</td>
            <td>
                <a href="{public_path(f'/admin-panel/devices/{device.device_id}/edit')}">edytuj</a>
                <br>
                <form method="post" action="{public_path(f'/admin-panel/devices/{device.device_id}/toggle')}" style="display:inline">
                    <button type="submit">{toggle_text}</button>
                </form>
                <br>
                <a href="{public_path(f'/admin-panel/devices/{device.device_id}/delete')}">usuń</a>
            </td>
        </tr>
        """

    if not rows:
        rows = "<tr><td colspan='9'>Brak urządzeń.</td></tr>"

    body = f"""
    <div class="top">
        <h1>Urządzenia ESP32</h1>
        <a href="{public_path('/admin-panel')}">Wróć do panelu</a>
    </div>

    <div class="card">
        <h2>Dodaj urządzenie</h2>
        <p class="muted">
            Każde ESP32 powinno mieć własny identyfikator i sekret.
            Sekret jest wyświetlany w panelu, bo tego teraz potrzebujemy. Tak, po HTTP to nadal nie jest sejf pancerny.
        </p>

        <form method="post" action="{public_path('/admin-panel/devices')}">
            <label>ID urządzenia</label>
            <input name="device_id" placeholder="np. gate-plocka-1" required>

            <label>Nazwa opisowa</label>
            <input name="name" placeholder="np. Brama Płocka 1">

            <label>Sekret urządzenia</label>
            <input name="secret" placeholder="zostaw puste, aby wygenerować automatycznie">

            <button type="submit">Dodaj / zaktualizuj urządzenie</button>
        </form>
    </div>

    <div class="card">
        <h2>Lista urządzeń</h2>
        <table>
            <thead>
                <tr>
                    <th>ID</th>
                    <th>Device ID</th>
                    <th>Nazwa</th>
                    <th>Sekret</th>
                    <th>Status</th>
                    <th>Tokeny</th>
                    <th>Komendy oczekujące/wysłane</th>
                    <th>Ostatnio widziane</th>
                    <th>Akcje</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </div>
    """

    return HTMLResponse(admin_panel_page("Urządzenia ESP32", body))


@app.post("/admin-panel/devices", response_class=HTMLResponse)
async def admin_panel_save_device(
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    form = await request.form()

    device_id = str(form.get("device_id") or "").strip()
    name = str(form.get("name") or "").strip()
    secret = str(form.get("secret") or "").strip()

    if not device_id:
        raise HTTPException(status_code=400, detail="device_id is required")

    if not secret:
        secret = secrets.token_urlsafe(32)

    device = db.query(Device).filter(Device.device_id == device_id).first()

    if device is None:
        device = Device(
            device_id=device_id,
            name=name,
            secret=secret,
            is_active=True,
        )
        db.add(device)
        action = "utworzone"
    else:
        device.name = name
        device.secret = secret
        device.is_active = True
        action = "zaktualizowane"

    log_event(
        db,
        event_type="device_saved",
        request=request,
        status="ok",
        device_id=device_id,
        message=f"Device {device_id} {action}",
    )

    db.commit()
    db.refresh(device)

    body = f"""
    <div class="card">
        <h1>Urządzenie {action}</h1>
        <p><strong>{html.escape(device.name or device.device_id)}</strong></p>

        <p>Device ID:</p>
        <p><code>{html.escape(device.device_id)}</code></p>

        <p>Sekret urządzenia:</p>
        <p><code>{html.escape(secret)}</code></p>

        <h2>Konfiguracja ESP32 przez terminal</h2>
        <p><code>device {html.escape(device.device_id)}|{html.escape(secret)}</code></p>
        <p><code>server http://tools.malmaz.com/gate-control</code></p>
        <p><code>save</code></p>
        <p><code>reboot</code></p>

        <a href="{public_path('/admin-panel/devices')}">Wróć do urządzeń</a>
    </div>
    """

    return HTMLResponse(admin_panel_page("Urządzenie zapisane", body))


@app.get("/admin-panel/devices/{device_id}/edit", response_class=HTMLResponse)
def admin_panel_edit_device(
    device_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    device = device_or_404(db, device_id)
    counts = device_counts(db, device.device_id)

    current_secret = device.secret or ""

    body = f"""
    <div class="top">
        <h1>Edytuj urządzenie</h1>
        <a href="{public_path('/admin-panel/devices')}">Wróć do urządzeń</a>
    </div>

    <div class="card">
        <h2>{html.escape(device.name or device.device_id)}</h2>

        <p>Device ID:</p>
        <p><code>{html.escape(device.device_id)}</code></p>

        <p>Aktualny sekret:</p>
        <p><code>{html.escape(current_secret)}</code></p>

        <p class="muted">
            Tokeny przypisane: {counts["tokens"]}<br>
            Komendy oczekujące/wysłane: {counts["pending_or_sent_commands"]}<br>
            Wszystkie komendy historycznie: {counts["all_commands"]}<br>
            Ostatnio widziane: {html.escape(device.last_seen_at.isoformat()) if device.last_seen_at else ""}
        </p>

        <form method="post" action="{public_path(f'/admin-panel/devices/{device.device_id}/update')}">
            <label>Nazwa opisowa</label>
            <input name="name" value="{html.escape(device.name or '')}">

            <label>Sekret urządzenia</label>
            <input name="secret" value="{html.escape(current_secret)}">

            <label>
                <input type="checkbox" name="is_active" value="1" {"checked" if device.is_active else ""} style="width:auto">
                Urządzenie aktywne
            </label>

            <button type="submit">Zapisz zmiany</button>
        </form>
    </div>
    """

    return HTMLResponse(admin_panel_page("Edytuj urządzenie", body))


@app.post("/admin-panel/devices/{device_id}/update", response_class=HTMLResponse)
async def admin_panel_update_device(
    device_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    device = device_or_404(db, device_id)
    form = await request.form()

    name = str(form.get("name") or "").strip()
    secret = str(form.get("secret") or "").strip()
    is_active = bool(form.get("is_active"))

    device.name = name
    device.is_active = is_active

    if secret:
        device.secret = secret

    if not is_active:
        cancelled_commands = (
            db.query(Command)
            .filter(Command.device_id == device.device_id)
            .filter(Command.status.in_(["pending", "sent"]))
            .update(
                {
                    Command.status: "cancelled",
                    Command.message: "Cancelled because device was deactivated",
                },
                synchronize_session=False,
            )
        )
    else:
        cancelled_commands = 0

    log_event(
        db,
        event_type="device_updated",
        request=request,
        status="ok",
        device_id=device.device_id,
        message=f"Device updated; active={is_active}; cancelled_commands={cancelled_commands}",
    )

    db.commit()

    body = f"""
    <div class="card">
        <h1>Zapisano zmiany</h1>
        <p>Urządzenie: <code>{html.escape(device.device_id)}</code></p>
        <p>Sekret: <code>{html.escape(device.secret or "")}</code></p>
        <p>Anulowano oczekujących/wysłanych komend: <strong>{cancelled_commands}</strong></p>
        <a href="{public_path('/admin-panel/devices')}">Wróć do urządzeń</a>
    </div>
    """

    return HTMLResponse(admin_panel_page("Zapisano urządzenie", body))


@app.post("/admin-panel/devices/{device_id}/toggle", response_class=HTMLResponse)
def admin_panel_toggle_device(
    device_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    device = device_or_404(db, device_id)
    device.is_active = not device.is_active

    if not device.is_active:
        cancelled_commands = (
            db.query(Command)
            .filter(Command.device_id == device.device_id)
            .filter(Command.status.in_(["pending", "sent"]))
            .update(
                {
                    Command.status: "cancelled",
                    Command.message: "Cancelled because device was deactivated",
                },
                synchronize_session=False,
            )
        )
    else:
        cancelled_commands = 0

    log_event(
        db,
        event_type="device_toggled",
        request=request,
        status="ok",
        device_id=device.device_id,
        message=f"Device active={device.is_active}; cancelled_commands={cancelled_commands}",
    )

    db.commit()

    body = f"""
    <div class="card">
        <h1>Zmieniono status urządzenia</h1>
        <p>Urządzenie: <code>{html.escape(device.device_id)}</code></p>
        <p>Status: <strong>{"aktywne" if device.is_active else "wyłączone"}</strong></p>
        <p>Anulowano oczekujących/wysłanych komend: <strong>{cancelled_commands}</strong></p>
        <a href="{public_path('/admin-panel/devices')}">Wróć do urządzeń</a>
    </div>
    """

    return HTMLResponse(admin_panel_page("Zmieniono status urządzenia", body))


@app.get("/admin-panel/devices/{device_id}/delete", response_class=HTMLResponse)
def admin_panel_delete_device_confirm(
    device_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    device = device_or_404(db, device_id)
    counts = device_counts(db, device.device_id)

    body = f"""
    <div class="card">
        <h1>Usuń urządzenie</h1>

        <p>Urządzenie:</p>
        <p><code>{html.escape(device.device_id)}</code></p>
        <p><strong>{html.escape(device.name or "")}</strong></p>

        <p class="muted">
            Tokeny przypisane do urządzenia: {counts["tokens"]}<br>
            Komendy oczekujące/wysłane: {counts["pending_or_sent_commands"]}<br>
            Wszystkie komendy historycznie: {counts["all_commands"]}
        </p>

        <form method="post" action="{public_path(f'/admin-panel/devices/{device.device_id}/delete')}">
            <label>Potwierdzenie</label>
            <input name="confirm" placeholder="Wpisz: USUN" autocomplete="off">

            <label>
                <input type="checkbox" name="delete_tokens" value="1" style="width:auto">
                Usuń też tokeny/piloty przypisane do tego urządzenia
            </label>

            <button class="danger" type="submit">Usuń urządzenie</button>
        </form>

        <p><a href="{public_path('/admin-panel/devices')}">Anuluj i wróć</a></p>
    </div>
    """

    return HTMLResponse(admin_panel_page("Usuń urządzenie", body))


@app.post("/admin-panel/devices/{device_id}/delete", response_class=HTMLResponse)
async def admin_panel_delete_device(
    device_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    device = device_or_404(db, device_id)
    form = await request.form()

    confirm = str(form.get("confirm") or "").strip()
    delete_tokens = bool(form.get("delete_tokens"))

    if confirm != "USUN":
        body = f"""
        <div class="card">
            <h1>Nie usunięto urządzenia</h1>
            <p>Potwierdzenie było nieprawidłowe. Trzeba wpisać dokładnie: <code>USUN</code></p>
            <a href="{public_path('/admin-panel/devices')}">Wróć do urządzeń</a>
        </div>
        """
        return HTMLResponse(admin_panel_page("Nie usunięto urządzenia", body))

    cancelled_commands = (
        db.query(Command)
        .filter(Command.device_id == device.device_id)
        .filter(Command.status.in_(["pending", "sent"]))
        .update(
            {
                Command.status: "cancelled",
                Command.message: "Cancelled because device was deleted",
            },
            synchronize_session=False,
        )
    )

    if delete_tokens:
        deleted_tokens = (
            db.query(AccessToken)
            .filter(AccessToken.device_id == device.device_id)
            .delete(synchronize_session=False)
        )
    else:
        deleted_tokens = 0

    deleted_device_id = device.device_id
    deleted_device_name = device.name or ""

    db.delete(device)

    log_event(
        db,
        event_type="device_deleted",
        request=request,
        status="ok",
        device_id=deleted_device_id,
        message=f"Device deleted; delete_tokens={delete_tokens}; deleted_tokens={deleted_tokens}; cancelled_commands={cancelled_commands}",
    )

    db.commit()

    body = f"""
    <div class="card">
        <h1>Usunięto urządzenie</h1>
        <p>Device ID: <code>{html.escape(deleted_device_id)}</code></p>
        <p>Nazwa: {html.escape(deleted_device_name)}</p>
        <p>Usunięto tokenów: <strong>{deleted_tokens}</strong></p>
        <p>Anulowano oczekujących/wysłanych komend: <strong>{cancelled_commands}</strong></p>
        <a href="{public_path('/admin-panel/devices')}">Wróć do urządzeń</a>
    </div>
    """

    return HTMLResponse(admin_panel_page("Usunięto urządzenie", body))
