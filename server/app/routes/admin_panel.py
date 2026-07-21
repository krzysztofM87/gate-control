import html
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import (
    APP_TIMEZONE,
    DEVICE_ID,
    FOREVER_VALID_TO,
    OPEN_COOLDOWN_SECONDS,
    TOKEN_DEFAULT_VALID_HOURS,
)
from app.database import get_db
from app.models import AccessToken, Command, Device, TokenClientUsage, VirtualPilotButton
from app.services import (
    check_admin_auth,
    create_virtual_pilot_button,
    delete_access_token,
    delete_virtual_pilot_button,
    device_counts,
    device_or_404,
    expire_pending_commands,
    format_dt,
    log_event,
    normalize_gate_target,
    now_utc,
    public_path,
    public_url,
    reactivate_access_token,
    update_access_token,
    update_virtual_pilot_button,
    virtual_button_or_404,
    virtual_buttons_for_token,
)
from app.views import (
    admin_login_page,
    admin_panel_page,
    display_pilot_title,
    is_admin_panel_authorized,
    token_valid_to_text,
)


router = APIRouter()


def datetime_local_value(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    return value.astimezone(APP_TIMEZONE).strftime("%Y-%m-%dT%H:%M")


def parse_datetime_local(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid valid_to value") from error

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=APP_TIMEZONE)

    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def parse_optional_limit(value: str) -> int | None:
    value = value.strip()

    if not value:
        return None

    try:
        parsed = int(value)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid limit value") from error

    if not 1 <= parsed <= 1000:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 1000")

    return parsed


def parse_optional_hours(value: str) -> int | None:
    value = value.strip()

    if not value:
        return None

    try:
        parsed = int(value)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid validity value") from error

    if not 1 <= parsed <= 24 * 60:
        raise HTTPException(status_code=400, detail="Validity must be between 1 and 1440 hours")

    return parsed


def parse_sort_order(value: str) -> int:
    try:
        parsed = int(value or 0)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid button order") from error

    return max(0, min(parsed, 1000))


def mapping_device_options(devices: list[Device], selected_device_id: str = "") -> str:
    options = ""

    for device in devices:
        selected = " selected" if device.device_id == selected_device_id else ""
        disabled = " disabled" if not device.is_active and not selected else ""
        status_text = "" if device.is_active else " (wyłączone)"
        name_text = f"{device.name} - " if device.name else ""
        label = f"{name_text}{device.device_id}{status_text}"
        options += (
            f'<option value="{html.escape(device.device_id)}"{selected}{disabled}>'
            f"{html.escape(label)}</option>"
        )

    return options


def mapping_command_options(selected_command: str = "open_1") -> str:
    options = ""

    for value, label in (
        ("open_1", "Kanał 1 / GPIO26"),
        ("open_2", "Kanał 2 / GPIO27"),
        ("open_both", "Oba kanały"),
    ):
        selected = " selected" if value == selected_command else ""
        options += f'<option value="{value}"{selected}>{label}</option>'

    return options

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
        virtual_buttons = virtual_buttons_for_token(db, token.id) if token.is_virtual else []
        target_text = (
            f"wirtualny ({len(virtual_buttons)} przyc.)"
            if token.is_virtual
            else token.gate_target
        )
        client_use_limit_text = (
            str(token.max_uses_per_client)
            if token.max_uses_per_client is not None
            else "bez limitu użyć"
        )
        client_validity_text = (
            f"{token.client_validity_hours} h"
            if token.client_validity_hours is not None
            else "bez terminu"
        )
        needs_reactivation = (
            not token.is_active
            or token.status != "active"
            or (not token.valid_forever and token.valid_to < now_utc())
            or (token.max_uses is not None and token.used_count >= token.max_uses)
        )
        reactivate_action = ""

        if needs_reactivation:
            reactivate_action = f"""
                <form class="inline-form" method="post" action="{public_path(f'/admin-panel/tokens/{token.id}/reactivate')}" onsubmit="return confirm('Ponownie aktywować pilot i wyzerować jego liczniki?')">
                    <button class="compact" type="submit">Reaktywuj</button>
                </form>
            """
        token_rows += f"""
        <tr>
            <td>{token.id}</td>
            <td>{html.escape(token.label or "")}</td>
            <td>{html.escape(display_pilot_title(token))}</td>
            <td><code>{html.escape(target_text)}</code></td>
            <td>{html.escape(token.status)}</td>
            <td>{token.used_count} / {token.max_uses if token.max_uses is not None else "∞"}</td>
            <td>{html.escape(client_use_limit_text)} / {html.escape(client_validity_text)}</td>
            <td>{html.escape(token_valid_to_text(token))}</td>
            <td><a href="{html.escape(url)}" target="_blank">pilot</a><br><code>{html.escape(url)}</code></td>
            <td>
                <a class="action-link" href="{public_path(f'/admin-panel/tokens/{token.id}/edit')}">Edytuj</a>
                {reactivate_action}
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

            <label>Rodzaj pilota</label>
            <select id="pilot-kind" name="pilot_kind">
                <option value="physical">Pilot jednego urządzenia</option>
                <option value="virtual">Pilot wirtualny z wielu urządzeń</option>
            </select>

            <div id="physical-pilot-settings">
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
            </div>

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

            <div class="grid">
                <div>
                    <label>Limit użyć na każdy telefon, puste = bez limitu</label>
                    <input name="max_uses_per_client" type="number" min="1" max="1000" placeholder="np. 2">
                </div>
                <div>
                    <label>Ważność na telefon w godzinach, puste = bez limitu</label>
                    <input name="client_validity_hours" type="number" min="1" max="1440" placeholder="np. 24">
                </div>
            </div>

            <label>Cooldown w sekundach</label>
            <input name="open_cooldown_seconds" type="number" value="{OPEN_COOLDOWN_SECONDS}" min="0" max="3600">

            <button type="submit">Utwórz pilota</button>
        </form>

        <script>
            const pilotKind = document.getElementById("pilot-kind");
            const physicalSettings = document.getElementById("physical-pilot-settings");

            function updatePilotKind() {{
                physicalSettings.hidden = pilotKind.value === "virtual";
            }}

            pilotKind.addEventListener("change", updatePilotKind);
            updatePilotKind();
        </script>
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
                    <th>Ograniczenia telefonu</th>
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
    is_virtual = str(form.get("pilot_kind") or "physical") == "virtual"

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

    client_validity_hours = parse_optional_hours(
        str(form.get("client_validity_hours") or "")
    )

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
        is_virtual=is_virtual,
        device_id=device.device_id,
        gate_target=gate_target,
        status="active",
        is_active=True,
        valid_from=valid_from,
        valid_to=valid_to,
        valid_forever=valid_forever,
        max_uses=max_uses,
        max_uses_per_client=max_uses_per_client,
        client_validity_hours=client_validity_hours,
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
        message=f"Token created from admin panel for {'virtual pilot' if is_virtual else gate_target}",
    )

    db.commit()
    db.refresh(token)

    url = public_url(f"/pilot/{token.token_value}")
    target_text = "pilot wirtualny" if token.is_virtual else gate_target
    configure_link = (
        f'<a href="{public_path(f"/admin-panel/tokens/{token.id}/edit")}">Dodaj przyciski pilota</a><br>'
        if token.is_virtual
        else ""
    )

    body = f"""
    <div class="card">
        <h1>Utworzono pilota</h1>
        <p><strong>{html.escape(display_pilot_title(token))}</strong></p>
        <p>Cel: <code>{html.escape(target_text)}</code></p>
        <p><a href="{html.escape(url)}" target="_blank">Otwórz pilota</a></p>
        <p><code>{html.escape(url)}</code></p>
        {configure_link}
        <a href="{public_path('/admin-panel')}">Wróć do panelu</a>
    </div>
    """

    return HTMLResponse(admin_panel_page("Utworzono pilota", body))


@router.get("/admin-panel/tokens/{token_id}/edit", response_class=HTMLResponse)
def admin_panel_edit_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    token = db.query(AccessToken).filter(AccessToken.id == token_id).first()

    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    devices = db.query(Device).order_by(Device.name.asc(), Device.device_id.asc()).all()
    device_options = ""

    for device in devices:
        selected = " selected" if device.device_id == token.device_id else ""
        disabled = " disabled" if not device.is_active and not selected else ""
        status_text = "" if device.is_active else " (wyłączone)"
        name_text = f"{device.name} - " if device.name else ""
        option_label = f"{name_text}{device.device_id}{status_text}"
        device_options += (
            f'<option value="{html.escape(device.device_id)}"{selected}{disabled}>'
            f"{html.escape(option_label)}</option>"
        )

    gate_options = ""
    for value, label in (
        ("open_1", "1 przycisk - brama 1 / GPIO26"),
        ("open_2", "1 przycisk - brama 2 / GPIO27"),
        ("open_both", "3 przyciski - brama 1, brama 2, obie"),
    ):
        selected = " selected" if value == token.gate_target else ""
        gate_options += f'<option value="{value}"{selected}>{label}</option>'

    valid_forever_checked = " checked" if token.valid_forever else ""
    active_checked = " checked" if token.is_active else ""
    valid_to_value = "" if token.valid_forever else datetime_local_value(token.valid_to)
    max_uses_value = "" if token.max_uses is None else str(token.max_uses)
    max_uses_per_client_value = (
        "" if token.max_uses_per_client is None else str(token.max_uses_per_client)
    )
    client_validity_hours_value = (
        "" if token.client_validity_hours is None else str(token.client_validity_hours)
    )

    if token.is_virtual:
        routing_fields_html = """
            <p><strong>Rodzaj:</strong> pilot wirtualny</p>
        """
    else:
        routing_fields_html = f"""
            <div class="grid">
                <div>
                    <label>Urządzenie</label>
                    <select name="device_id" required>{device_options}</select>
                </div>
                <div>
                    <label>Typ pilota</label>
                    <select name="gate_target" required>{gate_options}</select>
                </div>
            </div>

            <div class="grid">
                <div>
                    <label>Nazwa przycisku 1</label>
                    <input name="button_1_label" value="{html.escape(token.button_1_label or '')}">
                </div>
                <div>
                    <label>Nazwa przycisku 2</label>
                    <input name="button_2_label" value="{html.escape(token.button_2_label or '')}">
                </div>
            </div>

            <label>Nazwa przycisku „obie”</label>
            <input name="button_both_label" value="{html.escape(token.button_both_label or '')}">
        """

    virtual_config_html = ""

    if token.is_virtual:
        virtual_button_rows = ""
        configured_buttons = virtual_buttons_for_token(db, token.id)

        for button in configured_buttons:
            edit_form_id = f"edit-virtual-button-{button.id}"
            device_select = mapping_device_options(devices, button.device_id)
            command_select = mapping_command_options(button.command)
            virtual_button_rows += f"""
            <tr>
                <td><input form="{edit_form_id}" name="label" value="{html.escape(button.label)}" required maxlength="120"></td>
                <td><select form="{edit_form_id}" name="device_id" required>{device_select}</select></td>
                <td><select form="{edit_form_id}" name="command" required>{command_select}</select></td>
                <td><input form="{edit_form_id}" name="sort_order" type="number" min="0" max="1000" value="{button.sort_order}"></td>
                <td>
                    <form id="{edit_form_id}" class="inline-form" method="post" action="{public_path(f'/admin-panel/tokens/{token.id}/buttons/{button.id}/update')}"></form>
                    <button form="{edit_form_id}" class="compact" type="submit">Zapisz</button>
                    <form class="inline-form" method="post" action="{public_path(f'/admin-panel/tokens/{token.id}/buttons/{button.id}/delete')}" onsubmit="return confirm('Usunąć ten przycisk?')">
                        <button class="danger compact" type="submit">Usuń</button>
                    </form>
                </td>
            </tr>
            """

        if not virtual_button_rows:
            virtual_button_rows = "<tr><td colspan='5'>Brak przycisków.</td></tr>"

        next_order = len(configured_buttons) * 10
        add_device_options = mapping_device_options(
            [device for device in devices if device.is_active]
        )
        virtual_config_html = f"""
        <div class="card">
            <h2>Przyciski pilota wirtualnego</h2>
            <table>
                <thead>
                    <tr>
                        <th>Nazwa</th>
                        <th>Urządzenie</th>
                        <th>Kanał</th>
                        <th>Kolejność</th>
                        <th>Akcje</th>
                    </tr>
                </thead>
                <tbody>{virtual_button_rows}</tbody>
            </table>

            <h3>Dodaj przycisk</h3>
            <form method="post" action="{public_path(f'/admin-panel/tokens/{token.id}/buttons')}">
                <div class="grid">
                    <div>
                        <label>Nazwa</label>
                        <input name="label" maxlength="120" required placeholder="np. Brama wjazdowa">
                    </div>
                    <div>
                        <label>Urządzenie</label>
                        <select name="device_id" required>{add_device_options}</select>
                    </div>
                </div>
                <div class="grid">
                    <div>
                        <label>Kanał</label>
                        <select name="command" required>{mapping_command_options()}</select>
                    </div>
                    <div>
                        <label>Kolejność</label>
                        <input name="sort_order" type="number" min="0" max="1000" value="{next_order}">
                    </div>
                </div>
                <button type="submit">Dodaj przycisk</button>
            </form>
        </div>
        """

    body = f"""
    <div class="card">
        <div class="top">
            <h1>Edytuj pilota</h1>
            <a href="{public_path('/admin-panel')}">Wróć</a>
        </div>

        <p class="muted">Link pilota pozostaje bez zmian.</p>
        <p><code>{html.escape(public_url(f'/pilot/{token.token_value}'))}</code></p>

        <form method="post" action="{public_path(f'/admin-panel/tokens/{token.id}/update')}">
            <div class="grid">
                <div>
                    <label>Opis techniczny</label>
                    <input name="label" value="{html.escape(token.label or '')}">
                </div>
                <div>
                    <label>Nazwa pilota</label>
                    <input name="pilot_title" value="{html.escape(token.pilot_title or '')}">
                </div>
            </div>

            {routing_fields_html}

            <div class="grid">
                <div>
                    <label>Ważny do</label>
                    <input name="valid_to" type="datetime-local" value="{valid_to_value}">
                    <label><input name="valid_forever" type="checkbox" value="1" style="width:auto"{valid_forever_checked}> Bezterminowy</label>
                </div>
                <div>
                    <label>Łączny limit użyć, puste = bez limitu</label>
                    <input name="max_uses" type="number" min="1" max="1000" value="{max_uses_value}">
                    <p class="muted">Dotychczasowe użycia: {token.used_count}</p>
                </div>
            </div>

            <div class="grid">
                <div>
                    <label>Limit użyć na każdy telefon, puste = bez limitu</label>
                    <input name="max_uses_per_client" type="number" min="1" max="1000" value="{max_uses_per_client_value}">
                </div>
                <div>
                    <label>Ważność na telefon w godzinach, puste = bez limitu</label>
                    <input name="client_validity_hours" type="number" min="1" max="1440" value="{client_validity_hours_value}">
                </div>
            </div>

            <label>Cooldown w sekundach</label>
            <input name="open_cooldown_seconds" type="number" min="0" max="3600" value="{token.open_cooldown_seconds}">

            <label><input name="is_active" type="checkbox" value="1" style="width:auto"{active_checked}> Pilot aktywny</label>
            <button type="submit">Zapisz zmiany</button>
        </form>
    </div>
    {virtual_config_html}
    """

    return HTMLResponse(admin_panel_page("Edytuj pilota", body))


@router.post("/admin-panel/tokens/{token_id}/update", response_class=HTMLResponse)
async def admin_panel_update_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    token = db.query(AccessToken).filter(AccessToken.id == token_id).first()

    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    form = await request.form()
    valid_forever = bool(form.get("valid_forever"))
    valid_to_raw = str(form.get("valid_to") or "").strip()

    if valid_forever:
        valid_to = FOREVER_VALID_TO
    elif valid_to_raw:
        valid_to = parse_datetime_local(valid_to_raw)
    else:
        raise HTTPException(status_code=400, detail="valid_to is required")

    try:
        cooldown = int(form.get("open_cooldown_seconds") or 0)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid cooldown value") from error

    if not 0 <= cooldown <= 3600:
        raise HTTPException(status_code=400, detail="Cooldown must be between 0 and 3600")

    changes = {
        "label": str(form.get("label") or ""),
        "pilot_title": str(form.get("pilot_title") or ""),
        "valid_to": valid_to,
        "valid_forever": valid_forever,
        "max_uses": parse_optional_limit(str(form.get("max_uses") or "")),
        "max_uses_per_client": parse_optional_limit(
            str(form.get("max_uses_per_client") or "")
        ),
        "client_validity_hours": parse_optional_hours(
            str(form.get("client_validity_hours") or "")
        ),
        "open_cooldown_seconds": cooldown,
        "is_active": bool(form.get("is_active")),
    }

    if not token.is_virtual:
        changes.update(
            {
                "button_1_label": str(form.get("button_1_label") or ""),
                "button_2_label": str(form.get("button_2_label") or ""),
                "button_both_label": str(form.get("button_both_label") or ""),
                "device_id": str(form.get("device_id") or ""),
                "gate_target": str(form.get("gate_target") or ""),
            }
        )
    result = update_access_token(
        db,
        token=token,
        changes=changes,
        request=request,
    )

    body = f"""
    <div class="card">
        <h1>Zapisano zmiany</h1>
        <p><strong>{html.escape(display_pilot_title(token))}</strong></p>
        <p>Status: <code>{html.escape(token.status)}</code></p>
        <p>Anulowano oczekujących/wysłanych komend: <strong>{result['cancelled_commands']}</strong></p>
        <a href="{public_path('/admin-panel')}">Wróć do panelu</a>
    </div>
    """

    return HTMLResponse(admin_panel_page("Zapisano pilota", body))


@router.post("/admin-panel/tokens/{token_id}/buttons", response_class=HTMLResponse)
async def admin_panel_create_virtual_button(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    token = db.query(AccessToken).filter(AccessToken.id == token_id).first()

    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    form = await request.form()
    create_virtual_pilot_button(
        db,
        token=token,
        label=str(form.get("label") or ""),
        device_id=str(form.get("device_id") or ""),
        command=str(form.get("command") or ""),
        sort_order=parse_sort_order(str(form.get("sort_order") or "0")),
        request=request,
    )
    return RedirectResponse(
        public_path(f"/admin-panel/tokens/{token.id}/edit"),
        status_code=303,
    )


@router.post("/admin-panel/tokens/{token_id}/buttons/{button_id}/update", response_class=HTMLResponse)
async def admin_panel_update_virtual_button(
    token_id: int,
    button_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    token = db.query(AccessToken).filter(AccessToken.id == token_id).first()

    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    button = virtual_button_or_404(db, token=token, button_id=button_id)
    form = await request.form()
    update_virtual_pilot_button(
        db,
        token=token,
        button=button,
        changes={
            "label": str(form.get("label") or ""),
            "device_id": str(form.get("device_id") or ""),
            "command": str(form.get("command") or ""),
            "sort_order": parse_sort_order(str(form.get("sort_order") or "0")),
        },
        request=request,
    )
    return RedirectResponse(
        public_path(f"/admin-panel/tokens/{token.id}/edit"),
        status_code=303,
    )


@router.post("/admin-panel/tokens/{token_id}/buttons/{button_id}/delete", response_class=HTMLResponse)
def admin_panel_delete_virtual_button(
    token_id: int,
    button_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    token = db.query(AccessToken).filter(AccessToken.id == token_id).first()

    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    button = virtual_button_or_404(db, token=token, button_id=button_id)
    delete_virtual_pilot_button(
        db,
        token=token,
        button=button,
        request=request,
    )
    return RedirectResponse(
        public_path(f"/admin-panel/tokens/{token.id}/edit"),
        status_code=303,
    )


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


@router.post("/admin-panel/tokens/{token_id}/reactivate", response_class=HTMLResponse)
def admin_panel_reactivate_token(
    token_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    if not is_admin_panel_authorized(request):
        return admin_login_page("Sesja wygasła albo token jest nieprawidłowy.")

    token = db.query(AccessToken).filter(AccessToken.id == token_id).first()

    if token is None:
        raise HTTPException(status_code=404, detail="Token not found")

    result = reactivate_access_token(db, token=token, request=request)

    body = f"""
    <div class="card">
        <h1>Pilot ponownie aktywny</h1>
        <p><strong>{html.escape(display_pilot_title(token))}</strong></p>
        <p>Wyzerowano licznik użyć oraz urządzenia/telefony: <strong>{result['reset_client_usages']}</strong>.</p>
        <p>Anulowano niewykonane komendy: <strong>{result['cancelled_commands']}</strong>.</p>
        <p>Ważny do: <strong>{html.escape(token_valid_to_text(token))}</strong></p>
        <a href="{public_path('/admin-panel')}">Wróć do panelu</a>
    </div>
    """

    return HTMLResponse(admin_panel_page("Pilot aktywny", body))

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
    db.query(VirtualPilotButton).delete(synchronize_session=False)
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
            Przyciski pilotów wirtualnych: {counts["virtual_buttons"]}<br>
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
    deleted_virtual_buttons = (
        db.query(VirtualPilotButton)
        .filter(VirtualPilotButton.device_id == device.device_id)
        .delete(synchronize_session=False)
    )

    if delete_tokens:
        token_ids = [
            token_id
            for (token_id,) in (
                db.query(AccessToken.id)
                .filter(AccessToken.device_id == device.device_id)
                .filter(AccessToken.is_virtual.is_(False))
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
            .filter(AccessToken.is_virtual.is_(False))
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
        message=f"Device deleted; delete_tokens={delete_tokens}; deleted_tokens={deleted_tokens}; deleted_virtual_buttons={deleted_virtual_buttons}; cancelled_commands={cancelled_commands}",
    )

    db.commit()

    body = f"""
    <div class="card">
        <h1>Usunięto urządzenie</h1>
        <p>Device ID: <code>{html.escape(deleted_device_id)}</code></p>
        <p>Nazwa: {html.escape(deleted_device_name)}</p>
        <p>Usunięto tokenów: <strong>{deleted_tokens}</strong></p>
        <p>Usunięto przycisków pilotów wirtualnych: <strong>{deleted_virtual_buttons}</strong></p>
        <p>Anulowano oczekujących/wysłanych komend: <strong>{cancelled_commands}</strong></p>
        <a href="{public_path('/admin-panel/devices')}">Wróć do urządzeń</a>
    </div>
    """

    return HTMLResponse(admin_panel_page("Usunięto urządzenie", body))
