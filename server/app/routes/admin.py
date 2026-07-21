import secrets
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import DEVICE_ID, FOREVER_VALID_TO
from app.database import get_db
from app.models import AccessToken, Command, Device, TokenClientUsage
from app.schemas import CreateDeviceRequest, CreateTokenRequest, UpdateTokenRequest
from app.services import (
    check_admin_auth,
    delete_access_token,
    device_counts,
    expire_pending_commands,
    log_event,
    normalize_gate_target,
    now_utc,
    public_url,
    reactivate_access_token,
    update_access_token,
)


router = APIRouter()

@router.post("/admin/devices")
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


@router.post("/admin/tokens")
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
        pilot_title=payload.pilot_title,
        button_1_label=payload.button_1_label,
        button_2_label=payload.button_2_label,
        button_both_label=payload.button_both_label,
        device_id=device.device_id,
        gate_target=gate_target,
        status="active",
        is_active=True,
        valid_from=valid_from,
        valid_to=valid_to,
        valid_forever=valid_forever,
        max_uses=payload.max_uses,
        max_uses_per_client=payload.max_uses_per_client,
        client_validity_hours=payload.client_validity_hours,
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
        "public_url": public_url(f"/pilot/{token.token_value}"),
        "device_id": token.device_id,
        "gate_target": token.gate_target,
        "valid_from": token.valid_from.isoformat(),
        "valid_to": None if getattr(token, "valid_forever", False) else token.valid_to.isoformat(),
        "valid_forever": getattr(token, "valid_forever", False),
        "max_uses": token.max_uses,
        "max_uses_per_client": token.max_uses_per_client,
        "client_validity_hours": token.client_validity_hours,
        "valid_forever": getattr(token, "valid_forever", False),
    }


@router.get("/admin/tokens")
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
                "public_url": public_url(f"/pilot/{token.token_value}"),
                "device_id": token.device_id,
                "gate_target": token.gate_target,
                "status": token.status,
                "is_active": token.is_active,
                "valid_from": token.valid_from.isoformat(),
                "valid_to": None if getattr(token, "valid_forever", False) else token.valid_to.isoformat(),
        "valid_forever": getattr(token, "valid_forever", False),
                "used_count": token.used_count,
                "max_uses": token.max_uses,
                "max_uses_per_client": token.max_uses_per_client,
                "client_validity_hours": token.client_validity_hours,
        "valid_forever": getattr(token, "valid_forever", False),
            }
            for token in tokens
        ]
    }


@router.get("/admin/commands")
def admin_list_commands(
    x_admin_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    check_admin_auth(x_admin_token)

    if expire_pending_commands(db):
        db.commit()

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


@router.patch("/admin/tokens/{token_id}")
def admin_update_token(
    token_id: int,
    payload: UpdateTokenRequest,
    request: Request,
    x_admin_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    check_admin_auth(x_admin_token)

    token = db.query(AccessToken).filter(AccessToken.id == token_id).first()

    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    changes = payload.model_dump(exclude_unset=True)

    for required_field in ("device_id", "gate_target", "open_cooldown_seconds", "is_active"):
        if required_field in changes and changes[required_field] is None:
            raise HTTPException(
                status_code=400,
                detail=f"{required_field} cannot be null",
            )

    if "valid_hours" in changes:
        valid_hours = changes.pop("valid_hours")
        changes["valid_forever"] = valid_hours is None
        changes["valid_to"] = (
            FOREVER_VALID_TO
            if valid_hours is None
            else now_utc() + timedelta(hours=valid_hours)
        )

    result = update_access_token(
        db,
        token=token,
        changes=changes,
        request=request,
    )

    return {
        "status": "ok",
        **result,
        "token": token.token_value,
        "public_url": public_url(f"/pilot/{token.token_value}"),
        "device_id": token.device_id,
        "gate_target": token.gate_target,
        "is_active": token.is_active,
        "valid_to": None if token.valid_forever else token.valid_to.isoformat(),
        "valid_forever": token.valid_forever,
        "used_count": token.used_count,
        "max_uses": token.max_uses,
        "max_uses_per_client": token.max_uses_per_client,
        "client_validity_hours": token.client_validity_hours,
        "open_cooldown_seconds": token.open_cooldown_seconds,
    }


@router.delete("/admin/tokens/{token_id}")
def admin_delete_token(
    token_id: int,
    request: Request,
    x_admin_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    check_admin_auth(x_admin_token)

    token = db.query(AccessToken).filter(AccessToken.id == token_id).first()

    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    result = delete_access_token(db, token=token, request=request)

    return {
        "status": "ok",
        **result,
    }


@router.post("/admin/tokens/{token_id}/reactivate")
def admin_reactivate_token(
    token_id: int,
    request: Request,
    x_admin_token: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
):
    check_admin_auth(x_admin_token)

    token = db.query(AccessToken).filter(AccessToken.id == token_id).first()

    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    result = reactivate_access_token(db, token=token, request=request)

    return {
        "status": "ok",
        **result,
        "token": token.token_value,
        "public_url": public_url(f"/pilot/{token.token_value}"),
        "valid_to": None if token.valid_forever else token.valid_to.isoformat(),
        "valid_forever": token.valid_forever,
        "used_count": token.used_count,
    }

@router.post("/admin/tokens/delete-all")
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

    db.query(TokenClientUsage).delete(synchronize_session=False)
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


@router.get("/admin/devices")
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
