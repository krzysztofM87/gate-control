from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Command
from app.schemas import AckRequest
from app.services import authenticate_device, log_event, now_iso, now_utc


router = APIRouter()

@router.get("/api/device/poll")
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


@router.post("/api/device/ack")
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
