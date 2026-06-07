import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel


PUBLIC_PATH_PREFIX = os.getenv("PUBLIC_PATH_PREFIX", "/gate-control").rstrip("/")
DEVICE_ID = os.getenv("DEVICE_ID", "gate-main")
DEVICE_TOKEN = os.getenv("DEVICE_TOKEN", "")
COMMAND_RELAY_TIME_MS = int(os.getenv("COMMAND_RELAY_TIME_MS", "700"))


app = FastAPI(
    title="Gate Control",
    root_path=PUBLIC_PATH_PREFIX,
)


# Prosty stan w pamięci.
# Na MVP wystarczy do testów. Docelowo przeniesiemy to do SQLite.
pending_command: Optional[dict] = None
last_ack: Optional[dict] = None


class AckRequest(BaseModel):
    command_id: Optional[str] = None
    status: str = "done"
    message: Optional[str] = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_path(path: str) -> str:
    """
    Buduje publiczny URL pod /gate-control.
    Przykład:
    public_path("/brama/abc") -> /gate-control/brama/abc
    """
    if not path.startswith("/"):
        path = "/" + path
    return f"{PUBLIC_PATH_PREFIX}{path}"


def check_device_auth(
    x_device_id: Optional[str],
    x_device_token: Optional[str],
) -> None:
    """
    Prosta autoryzacja ESP32.
    Jeżeli DEVICE_TOKEN jest pusty, nie blokujemy testów.
    Docelowo token powinien być ustawiony w server/.env.
    """
    if not DEVICE_TOKEN:
        return

    if x_device_id != DEVICE_ID:
        raise HTTPException(status_code=401, detail="Invalid device id")

    if x_device_token != DEVICE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid device token")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "gate-control",
        "public_path_prefix": PUBLIC_PATH_PREFIX,
        "time": now_iso(),
    }


@app.get("/")
def index():
    return {
        "status": "ok",
        "message": "Gate Control server is running",
        "public_url": public_path("/"),
        "health_url": public_path("/health"),
        "test_gate_url": public_path("/brama/test-token"),
    }


@app.get("/brama/{token}", response_class=HTMLResponse)
def gate_page(token: str):
    open_url = public_path(f"/brama/{token}/open")

    return f"""
<!doctype html>
<html lang="pl">
<head>
    <meta charset="utf-8">
    <title>Otwieranie bramy</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 480px;
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
            font-size: 22px;
            padding: 18px 24px;
            border-radius: 14px;
            border: none;
            cursor: pointer;
            background: #222;
            color: white;
            margin-top: 18px;
        }}

        button:active {{
            transform: scale(0.98);
        }}

        .token {{
            margin-top: 20px;
            font-size: 12px;
            color: #888;
            word-break: break-all;
        }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Otwieranie bramy</h1>
        <p>Naciśnij przycisk, aby wysłać polecenie otwarcia bramy.</p>

        <form method="post" action="{open_url}">
            <button type="submit">Otwórz bramę</button>
        </form>

        <div class="token">
            Token: {token}
        </div>
    </div>
</body>
</html>
"""


@app.post("/brama/{token}/open", response_class=HTMLResponse)
def open_gate(token: str):
    global pending_command

    command_id = str(uuid.uuid4())

    pending_command = {
        "command_id": command_id,
        "command": "open",
        "token": token,
        "relay_time_ms": COMMAND_RELAY_TIME_MS,
        "created_at": now_iso(),
    }

    back_url = public_path(f"/brama/{token}")

    return f"""
<!doctype html>
<html lang="pl">
<head>
    <meta charset="utf-8">
    <title>Polecenie wysłane</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 480px;
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
        <h1>Polecenie wysłane</h1>
        <p>Komenda otwarcia została zapisana. Moduł ESP32 odbierze ją przy następnym odpytywaniu serwera.</p>

        <a href="{back_url}">Wróć do przycisku</a>

        <div class="small">
            Command ID: {command_id}
        </div>
    </div>
</body>
</html>
"""


@app.get("/api/device/poll")
def device_poll(
    x_device_id: Optional[str] = Header(default=None),
    x_device_token: Optional[str] = Header(default=None),
):
    global pending_command

    check_device_auth(x_device_id, x_device_token)

    if pending_command is None:
        return {
            "command": "none",
            "time": now_iso(),
        }

    return {
        "command": pending_command["command"],
        "command_id": pending_command["command_id"],
        "relay_time_ms": pending_command["relay_time_ms"],
        "created_at": pending_command["created_at"],
        "time": now_iso(),
    }


@app.post("/api/device/ack")
def device_ack(
    payload: AckRequest,
    x_device_id: Optional[str] = Header(default=None),
    x_device_token: Optional[str] = Header(default=None),
):
    global pending_command
    global last_ack

    check_device_auth(x_device_id, x_device_token)

    last_ack = {
        "command_id": payload.command_id,
        "status": payload.status,
        "message": payload.message,
        "ack_at": now_iso(),
    }

    if pending_command is not None:
        if payload.command_id is None or payload.command_id == pending_command.get("command_id"):
            pending_command = None

    return {
        "status": "ok",
        "ack": last_ack,
    }


@app.get("/debug/state")
def debug_state():
    """
    Tymczasowy podgląd stanu.
    Docelowo usuniemy albo zabezpieczymy.
    """
    return {
        "pending_command": pending_command,
        "last_ack": last_ack,
        "public_path_prefix": PUBLIC_PATH_PREFIX,
        "device_id": DEVICE_ID,
        "device_token_configured": bool(DEVICE_TOKEN),
    }