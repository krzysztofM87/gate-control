import html
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import (
    ADMIN_TOKEN,
    COMMAND_PENDING_TIMEOUT_SECONDS,
    DEVICE_ID,
    DEVICE_SECRET,
    PUBLIC_PATH_PREFIX,
)
from app.database import get_db
from app.models import AccessToken, Command, CommandLog
from app.services import (
    client_usage_values,
    client_validity_values,
    create_command_from_token,
    create_command_from_virtual_button,
    ensure_client_id,
    expire_pending_command,
    expire_pending_commands,
    format_dt,
    gate_label,
    now_iso,
    public_path,
    set_client_id_cookie,
    validate_access_token,
    virtual_buttons_for_token,
)
from app.views import (
    display_button_label,
    display_pilot_title,
    render_page,
    token_valid_to_text,
)


router = APIRouter()


def client_validity_text(
    validity_hours: Optional[int],
    valid_until,
) -> str:
    if validity_hours is None:
        return "bez dodatkowego limitu"

    if valid_until is None:
        return f"{validity_hours} h od pierwszego użycia"

    return format_dt(valid_until)


def pilot_limit_reached_response(token: AccessToken) -> HTMLResponse:
    title = display_pilot_title(token)
    body = f"""
        <h1>Limit użyć wyczerpany</h1>
        <p>Pilot <strong>{html.escape(title)}</strong> wykorzystał wszystkie dostępne użycia.</p>
        <p>Nie można wysłać kolejnej komendy. Administrator może ponownie aktywować pilot bez zmiany jego linku.</p>
    """
    return HTMLResponse(
        render_page("Limit użyć wyczerpany", body),
        status_code=403,
    )

@router.get("/health")
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


@router.get("/")
def index():
    return {
        "status": "ok",
        "message": "Gate Control server is running",
        "public_url": public_path("/"),
        "health_url": public_path("/health"),
        "create_token_endpoint": public_path("/admin/tokens"),
    }


@router.get("/brama/{token_value}", response_class=HTMLResponse)
def gate_page(
    token_value: str,
    request: Request,
    db: Session = Depends(get_db),
):
    token = validate_access_token(db, token_value, request)

    if token.is_virtual:
        return RedirectResponse(
            public_path(f"/pilot/{token_value}"),
            status_code=307,
        )

    client_id, client_id_is_new = ensure_client_id(request)
    client_used_count, client_max_uses = client_usage_values(
        db,
        token=token,
        request=request,
    )
    client_limit_reached = (
        client_max_uses is not None and client_used_count >= client_max_uses
    )
    client_validity_hours, client_valid_until, client_validity_expired = (
        client_validity_values(db, token=token, request=request)
    )
    client_blocked = client_limit_reached or client_validity_expired
    client_block_message = (
        "Ważność pilota na tym telefonie wygasła."
        if client_validity_expired
        else "Limit użyć na tym telefonie został wykorzystany."
    )

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

        disabled_attr = " disabled" if client_blocked else ""

        buttons += f"""
        <form method="post" action="{html.escape(action)}">
            <button{class_attr}{disabled_attr} type="submit">{html.escape(label)}</button>
        </form>
        """

    body = f"""
        <h1>Otwieranie bramy</h1>
        <p>Naciśnij przycisk, aby wysłać polecenie otwarcia.</p>
        {f"<p>{html.escape(client_block_message)}</p>" if client_blocked else ""}
        {buttons}
        <div class="small">
            Ważny do: {html.escape(token_valid_to_text(token))}<br>
            Użycia: {token.used_count} / {token.max_uses if token.max_uses is not None else "bez limitu"}<br>
            Ten telefon: {client_used_count} / {client_max_uses if client_max_uses is not None else "bez limitu"}<br>
            Ważność na tym telefonie: {html.escape(client_validity_text(client_validity_hours, client_valid_until))}
        </div>
    """

    response = HTMLResponse(render_page("Otwieranie bramy", body))

    if client_id_is_new:
        set_client_id_cookie(response, client_id)

    return response


@router.post("/brama/{token_value}/open", response_class=HTMLResponse)
def open_gate_default(
    token_value: str,
    request: Request,
    db: Session = Depends(get_db),
):
    return open_gate(token_value, None, request, db)


@router.post("/brama/{token_value}/open/{gate}", response_class=HTMLResponse)
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


@router.get("/debug/state")
def debug_state(db: Session = Depends(get_db)):
    if expire_pending_commands(db):
        db.commit()

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

@router.get("/pilot/{token_value}", response_class=HTMLResponse)
def client_pilot_page(
    token_value: str,
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        token = validate_access_token(db, token_value, request)
    except HTTPException as error:
        if error.status_code == 403 and error.detail == "Token use limit reached":
            token = (
                db.query(AccessToken)
                .filter(AccessToken.token_value == token_value)
                .first()
            )

            if token is not None:
                return pilot_limit_reached_response(token)

        raise

    client_id, client_id_is_new = ensure_client_id(request)
    client_used_count, client_max_uses = client_usage_values(
        db,
        token=token,
        request=request,
    )
    client_limit_reached = (
        client_max_uses is not None and client_used_count >= client_max_uses
    )
    client_validity_hours, client_valid_until, client_validity_expired = (
        client_validity_values(db, token=token, request=request)
    )
    client_blocked = client_limit_reached or client_validity_expired
    client_block_message = (
        "Ważność pilota na tym telefonie wygasła."
        if client_validity_expired
        else "Limit użyć na tym telefonie został wykorzystany."
    )

    if token.is_virtual:
        color_classes = ("primary", "secondary", "danger")
        buttons = [
            (
                public_path(f"/pilot/{token_value}/press-button/{button.id}"),
                button.label,
                color_classes[index % len(color_classes)],
            )
            for index, button in enumerate(virtual_buttons_for_token(db, token.id))
        ]
    elif token.gate_target == "open_both":
        buttons = [
            (public_path(f"/pilot/{token_value}/press/1"), display_button_label(token, "open_1"), "primary"),
            (public_path(f"/pilot/{token_value}/press/2"), display_button_label(token, "open_2"), "secondary"),
            (public_path(f"/pilot/{token_value}/press/both"), display_button_label(token, "open_both"), "danger"),
        ]
    elif token.gate_target == "open_2":
        buttons = [
            (public_path(f"/pilot/{token_value}/press/2"), display_button_label(token, "open_2"), "primary"),
        ]
    else:
        buttons = [
            (public_path(f"/pilot/{token_value}/press/1"), display_button_label(token, "open_1"), "primary"),
        ]

    buttons_html = ""

    for press_url, label, css_class in buttons:
        buttons_html += f"""
        <button class="remote-button {css_class}" data-url="{html.escape(press_url)}">
            {html.escape(label)}
        </button>
        """

    if not buttons_html:
        buttons_html = '<div class="empty-buttons">Brak skonfigurowanych przycisków.</div>'

    max_uses_text = token.max_uses if token.max_uses is not None else "bez limitu"
    client_max_uses_text = client_max_uses if client_max_uses is not None else "bez limitu"
    client_validity_display = client_validity_text(
        client_validity_hours,
        client_valid_until,
    )
    title = display_pilot_title(token)
    status_poll_attempts = max(
        20,
        ((COMMAND_PENDING_TIMEOUT_SECONDS * 1000 + 699) // 700) + 3,
    )

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
        .limit-alert {{
            display: none;
            margin-bottom: 14px;
            padding: 12px;
            border: 1px solid rgba(255, 80, 80, .55);
            border-radius: 8px;
            background: #321313;
            color: #ffd0d0;
            text-align: center;
            font-size: 14px;
            line-height: 1.4;
        }}
        .limit-alert.visible {{ display: block; }}
        .limit-alert strong {{ display: block; margin-bottom: 4px; }}
        .steps {{ display: grid; gap: 6px; margin-bottom: 18px; font-size: 13px; color: #777; }}
        .step {{ background: rgba(255,255,255,.04); border-radius: 10px; padding: 8px 10px; }}
        .step.active {{ color: #ffe9a6; }}
        .step.done {{ color: #b7ffc9; }}
        .buttons {{ display: grid; gap: 14px; }}
        .empty-buttons {{
            padding: 18px 12px;
            border: 1px solid rgba(255,255,255,.12);
            border-radius: 8px;
            color: #bbb;
            text-align: center;
            font-size: 14px;
        }}
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
            .admin-nav {{
            position: sticky;
            top: 0;
            z-index: 100;
            background: #222;
            border-radius: 12px;
            padding: 10px 12px;
            margin-bottom: 18px;
            box-shadow: 0 6px 18px rgba(0,0,0,.12);
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            align-items: center;
        }}

        .admin-nav a {{
            display: inline-block;
            color: white;
            text-decoration: none;
            padding: 9px 12px;
            border-radius: 8px;
            background: #333;
            font-size: 14px;
        }}

        .admin-nav a:hover {{
            background: #444;
        }}

        .admin-nav .brand {{
            font-weight: bold;
            background: #111;
        }}

        @media (max-width: 700px) {{
            .admin-nav {{
                position: static;
            }}

            .admin-nav a {{
                width: 100%;
            }}
        }}
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

        <div id="limit-alert" class="limit-alert">
            <strong id="limit-alert-title"></strong>
            <span id="limit-alert-message"></span>
        </div>

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
            Użycia łącznie: <span id="usage-count">{token.used_count}</span> / <span id="usage-max">{html.escape(str(max_uses_text))}</span><br>
            Ten telefon: <span id="client-usage-count">{client_used_count}</span> / <span id="client-usage-max">{html.escape(str(client_max_uses_text))}</span><br>
            Ważność na tym telefonie: <span id="client-validity">{html.escape(client_validity_display)}</span>
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
        const clientUsageCountEl = document.getElementById("client-usage-count");
        const clientUsageMaxEl = document.getElementById("client-usage-max");
        const clientValidityEl = document.getElementById("client-validity");
        const limitAlertEl = document.getElementById("limit-alert");
        const limitAlertTitleEl = document.getElementById("limit-alert-title");
        const limitAlertMessageEl = document.getElementById("limit-alert-message");

        let readyTimer = null;
        let clientAccessBlocked = {"true" if client_blocked else "false"};
        let clientBlockMessage = "{client_block_message}";
        let tokenLimitReached = false;

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

        function isAccessBlocked() {{
            return tokenLimitReached || clientAccessBlocked;
        }}

        function refreshLimitAlert() {{
            if (tokenLimitReached) {{
                limitAlertTitleEl.textContent = "Limit użyć pilota wyczerpany";
                limitAlertMessageEl.textContent = "Pilot nie może wysłać kolejnej komendy.";
                limitAlertEl.classList.add("visible");
                return;
            }}

            if (clientAccessBlocked) {{
                limitAlertTitleEl.textContent = "Pilot niedostępny na tym telefonie";
                limitAlertMessageEl.textContent = clientBlockMessage;
                limitAlertEl.classList.add("visible");
                return;
            }}

            limitAlertEl.classList.remove("visible");
        }}

        function scheduleReady() {{
            clearReadyTimer();
            readyTimer = setTimeout(() => {{
                resetSteps();

                if (tokenLimitReached) {{
                    setStatus("Limit użyć pilota został wyczerpany.", "err");
                }} else if (clientAccessBlocked) {{
                    setStatus(clientBlockMessage, "err");
                }} else {{
                    setStatus("Gotowy", "");
                }}
            }}, 2500);
        }}

        function setStep(step, state) {{
            step.className = "step " + state;
        }}

        function setBusy(isBusy) {{
            buttons.forEach(button => button.disabled = isBusy || isAccessBlocked());
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

            if (typeof data.client_used_count !== "undefined" && data.client_used_count !== null) {{
                clientUsageCountEl.textContent = data.client_used_count;
            }}

            if (typeof data.client_max_uses !== "undefined") {{
                clientUsageMaxEl.textContent = data.client_max_uses === null ? "bez limitu" : data.client_max_uses;
            }}

            if (typeof data.client_validity_text === "string") {{
                clientValidityEl.textContent = data.client_validity_text;
            }}

            const clientUsedCount = Number(clientUsageCountEl.textContent);
            const clientMaxUses = data.client_max_uses;
            const usedCount = Number(usageCountEl.textContent);
            const maxUses = data.max_uses;
            const clientLimitReached = clientMaxUses !== null
                && typeof clientMaxUses !== "undefined"
                && clientUsedCount >= Number(clientMaxUses);
            const clientValidityExpired = data.client_validity_expired === true;
            tokenLimitReached = maxUses !== null
                && typeof maxUses !== "undefined"
                && usedCount >= Number(maxUses);
            clientAccessBlocked = clientLimitReached || clientValidityExpired;

            if (clientValidityExpired) {{
                clientBlockMessage = "Ważność pilota na tym telefonie wygasła.";
            }} else if (clientLimitReached) {{
                clientBlockMessage = "Limit użyć na tym telefonie został wykorzystany.";
            }}

            refreshLimitAlert();
        }}

        async function readJsonResponse(response) {{
            const responseText = await response.text();

            try {{
                return responseText ? JSON.parse(responseText) : {{}};
            }} catch (error) {{
                if (!response.ok) {{
                    throw new Error("Błąd serwera HTTP " + response.status);
                }}

                throw new Error("Serwer zwrócił nieprawidłową odpowiedź.");
            }}
        }}

        async function checkCommandStatus(statusUrl) {{
            const response = await fetch(statusUrl, {{
                method: "GET",
                headers: {{ "X-Requested-With": "fetch" }}
            }});

            const data = await readJsonResponse(response);

            if (!response.ok) {{
                throw new Error(data.detail || "Błąd statusu HTTP " + response.status);
            }}

            return data;
        }}

        async function watchCommandStatus(statusUrl) {{
            const maxAttempts = {status_poll_attempts};

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

                if (data.status === "failed") {{
                    setStep(stepCreated, "done");
                    setStatus("Nie wykonano polecenia. Sterownik nie odebrał go przed upływem czasu.", "err");
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

                const data = await readJsonResponse(response);
                updateUsage(data);

                if (!response.ok) {{
                    let message = data && data.detail ? data.detail : "Błąd HTTP " + response.status;

                    if (message === "Token use limit reached") {{
                        tokenLimitReached = true;
                        message = "Limit użyć pilota został wyczerpany.";
                    }}

                    if (response.status === 403 && (
                        message.includes("na tym telefonie")
                        || message.includes("tym telefonie")
                    )) {{
                        clientAccessBlocked = true;
                        clientBlockMessage = message;
                    }}

                    refreshLimitAlert();
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

        refreshLimitAlert();

        if (isAccessBlocked()) {{
            setStatus("{client_block_message}", "err");
            setBusy(false);
        }}
    </script>
</body>
</html>
"""

    response = HTMLResponse(body)

    if client_id_is_new:
        set_client_id_cookie(response, client_id)

    return response


@router.post("/pilot/{token_value}/press/{gate}")
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

    return pilot_command_payload(db, token, command, request, token_value)


@router.post("/pilot/{token_value}/press-button/{button_id}")
def client_virtual_pilot_press(
    token_value: str,
    button_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    token = validate_access_token(db, token_value, request)
    command = create_command_from_virtual_button(
        db,
        token=token,
        button_id=button_id,
        request=request,
    )

    return pilot_command_payload(db, token, command, request, token_value)


def pilot_command_payload(
    db: Session,
    token: AccessToken,
    command: Command,
    request: Request,
    token_value: str,
) -> dict:

    db.refresh(token)
    client_used_count, client_max_uses = client_usage_values(
        db,
        token=token,
        request=request,
    )
    client_validity_hours, client_valid_until, client_validity_expired = (
        client_validity_values(db, token=token, request=request)
    )

    return {
        "status": "ok",
        "command": command.command,
        "command_id": command.command_id,
        "relay_time_ms": command.relay_time_ms,
        "status_url": public_path(f"/pilot/{token_value}/command/{command.command_id}/status"),
        "used_count": token.used_count,
        "max_uses": token.max_uses,
        "client_used_count": client_used_count,
        "client_max_uses": client_max_uses,
        "client_validity_hours": client_validity_hours,
        "client_valid_until": client_valid_until.isoformat() if client_valid_until else None,
        "client_validity_text": client_validity_text(client_validity_hours, client_valid_until),
        "client_validity_expired": client_validity_expired,
        "valid_forever": getattr(token, "valid_forever", False),
        "token_status": token.status,
    }


@router.get("/pilot/{token_value}/command/{command_id}/status")
def client_pilot_command_status(
    token_value: str,
    command_id: str,
    request: Request,
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

    if expire_pending_command(db, command):
        db.commit()
        db.refresh(command)

    client_used_count, client_max_uses = client_usage_values(
        db,
        token=token,
        request=request,
    )
    client_validity_hours, client_valid_until, client_validity_expired = (
        client_validity_values(db, token=token, request=request)
    )

    return {
        "command_id": command.command_id,
        "command": command.command,
        "status": command.status,
        "delivered_count": command.delivered_count,
        "created_at": command.created_at.isoformat() if command.created_at else None,
        "sent_at": command.sent_at.isoformat() if command.sent_at else None,
        "ack_at": command.ack_at.isoformat() if command.ack_at else None,
        "message": command.message,
        "used_count": token.used_count,
        "max_uses": token.max_uses,
        "client_used_count": client_used_count,
        "client_max_uses": client_max_uses,
        "client_validity_hours": client_validity_hours,
        "client_valid_until": client_valid_until.isoformat() if client_valid_until else None,
        "client_validity_text": client_validity_text(client_validity_hours, client_valid_until),
        "client_validity_expired": client_validity_expired,
        "valid_forever": getattr(token, "valid_forever", False),
        "token_status": token.status,
    }
