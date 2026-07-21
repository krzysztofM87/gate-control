import html
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import (
    DEVICE_ID,
    FOREVER_VALID_TO,
    OPEN_COOLDOWN_SECONDS,
    TOKEN_DEFAULT_VALID_HOURS,
)
from app.database import get_db
from app.models import AccessToken, Command, Device, TokenClientUsage
from app.services import (
    check_admin_auth,
    delete_access_token,
    device_counts,
    device_or_404,
    expire_pending_commands,
    format_dt,
    log_event,
    normalize_gate_target,
    now_utc,
    public_path,
    public_url,
)
from app.views import (
    admin_login_page,
    admin_panel_page,
    display_pilot_title,
    is_admin_panel_authorized,
    token_valid_to_text,
)


router = APIRouter()

@router.get("/admin-panel", response_class=HTMLResponse)
def admin_panel(
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page()

    if expire_pending_commands(db):
        db.commit()

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

    devices = (
        db.query(Device)
        .order_by(Device.name.asc(), Device.device_id.asc())
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
            <td>{token.max_uses_per_client if token.max_uses_per_client is not None else "∞"}</td>
            <td>{html.escape(token_valid_to_text(token))}</td>
            <td><a href="{html.escape(url)}" target="_blank">pilot</a><br><code>{html.escape(url)}</code></td>
            <td>
                <form class="inline-form" method="post" action="{public_path(f'/admin-panel/tokens/{token.id}/delete')}" onsubmit="return confirm('Usunąć ten pilot?')">
                    <button class="danger compact" type="submit">Usuń</button>
                </form>
            </td>
        </tr>
        """

    if not token_rows:
        token_rows = "<tr><td colspan='10'>Brak tokenów.</td></tr>"

    command_rows = ""

    for command in commands:
        command_rows += f"""
        <tr>
            <td><code>{html.escape(command.command_id)}</code></td>
            <td>{html.escape(command.device_id)}</td>
            <td><code>{html.escape(command.command)}</code></td>
            <td>{html.escape(command.status)}</td>
            <td>{command.delivered_count}</td>
            <td>{html.escape(format_dt(command.created_at))}</td>
            <td>{html.escape(format_dt(command.ack_at)) if command.ack_at else ""}</td>
        </tr>
        """

    if not command_rows:
        command_rows = "<tr><td colspan='7'>Brak komend.</td></tr>"

    active_devices = [device for device in devices if device.is_active]
    selected_device_id = ""

    if any(device.device_id == DEVICE_ID for device in active_devices):
        selected_device_id = DEVICE_ID
    elif active_devices:
        selected_device_id = active_devices[0].device_id

    device_options = ""

    if devices and not active_devices:
        device_options = """
                        <option value="" disabled selected>Brak aktywnych urządzeń</option>
        """

    for device in devices:
        selected = " selected" if device.device_id == selected_device_id else ""
        disabled = " disabled" if not device.is_active else ""
        status_text = "" if device.is_active else " (wyłączone)"
        name_text = f"{device.name} - " if device.name else ""
        label = f"{name_text}{device.device_id}{status_text}"

        device_options += f"""
                        <option value="{html.escape(device.device_id)}"{selected}{disabled}>{html.escape(label)}</option>
        """

    if not device_options:
        device_options = """
                        <option value="" disabled selected>Brak urządzeń - dodaj ESP32 w zakładce urządzeń</option>
        """

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

        <form method="post" action="{public_path('/admin-panel/tokens/delete-all')}">
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
                    <select name="device_id" required>
                        {device_options}
                    </select>
                </div>
                <div>
                    <label>Typ pilota</label>
                    <select name="gate_target">
                        <option value="open_1">1 przycisk - brama 1 / GPIO26</option>
                        <option value="open_2">1 przycisk - brama 2 / GPIO27</option>
                        <option value="open_both">3 przyciski - brama 1, brama 2, obie</option>
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

            <label>Limit użyć na każdy telefon, puste = bez limitu</label>
            <input name="max_uses_per_client" type="number" min="1" max="1000" placeholder="np. 2">

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
                    <th>Limit / telefon</th>
                    <th>Ważny do</th>
                    <th>Link</th>
                    <th>Akcje</th>
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


@router.post("/admin-panel/login", response_class=HTMLResponse)
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


@router.post("/admin-panel/logout", response_class=HTMLResponse)
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


@router.post("/admin-panel/tokens", response_class=HTMLResponse)
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

    max_uses_per_client_raw = str(form.get("max_uses_per_client") or "").strip()
    if max_uses_per_client_raw == "":
        max_uses_per_client = None
    else:
        try:
            max_uses_per_client = int(max_uses_per_client_raw)
        except ValueError:
            max_uses_per_client = None

        if max_uses_per_client is not None:
            max_uses_per_client = max(1, min(max_uses_per_client, 1000))

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
        max_uses_per_client=max_uses_per_client,
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


@router.post("/admin-panel/tokens/{token_id}/delete", response_class=HTMLResponse)
def admin_panel_delete_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    token = db.query(AccessToken).filter(AccessToken.id == token_id).first()

    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    result = delete_access_token(db, token=token, request=request)
    label = result["label"] or f"ID {result['token_id']}"

    body = f"""
    <div class="card">
        <h1>Usunięto pilota</h1>
        <p><strong>{html.escape(label)}</strong></p>
        <p>Anulowano oczekujących/wysłanych komend: <strong>{result['cancelled_commands']}</strong></p>
        <a href="{public_path('/admin-panel')}">Wróć do panelu</a>
    </div>
    """

    return HTMLResponse(admin_panel_page("Usunięto pilota", body))

@router.post("/admin-panel/tokens/delete-all", response_class=HTMLResponse)
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

    db.query(TokenClientUsage).delete(synchronize_session=False)
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

@router.get("/admin-panel/devices", response_class=HTMLResponse)
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
            <td>{html.escape(format_dt(device.last_seen_at)) if device.last_seen_at else ""}</td>
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


@router.post("/admin-panel/devices", response_class=HTMLResponse)
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


@router.get("/admin-panel/devices/{device_id}/edit", response_class=HTMLResponse)
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
            Ostatnio widziane: {html.escape(format_dt(device.last_seen_at)) if device.last_seen_at else ""}
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


@router.post("/admin-panel/devices/{device_id}/update", response_class=HTMLResponse)
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


@router.post("/admin-panel/devices/{device_id}/toggle", response_class=HTMLResponse)
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


@router.get("/admin-panel/devices/{device_id}/delete", response_class=HTMLResponse)
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


@router.post("/admin-panel/devices/{device_id}/delete", response_class=HTMLResponse)
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
        token_ids = [
            token_id
            for (token_id,) in (
                db.query(AccessToken.id)
                .filter(AccessToken.device_id == device.device_id)
                .all()
            )
        ]

        if token_ids:
            db.query(TokenClientUsage).filter(
                TokenClientUsage.token_id.in_(token_ids)
            ).delete(synchronize_session=False)

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
