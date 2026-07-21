import html
from typing import Optional

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse

from app.models import AccessToken
from app.services import check_admin_auth, format_dt, public_path

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
    <div class="card">
        {body}
    </div>
</body>
</html>
"""



def token_valid_to_text(token: AccessToken) -> str:
    if getattr(token, "valid_forever", False):
        return "bezterminowo"

    if token.valid_to is None:
        return "brak"

    return format_dt(token.valid_to)

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
        .inline-form {{
            display: inline;
        }}
        button.compact {{
            padding: 8px 10px;
            margin-top: 0;
            font-size: 13px;
        }}
        .action-link {{
            display: inline-block;
            padding: 8px 10px;
            border-radius: 8px;
            background: #333;
            color: #fff;
            text-decoration: none;
            font-size: 13px;
            margin-right: 6px;
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
    <nav class="admin-nav">
        <a class="brand" href="{public_path('/admin-panel')}">Gate Control</a>
        <a href="{public_path('/admin-panel')}">Piloty / tokeny</a>
        <a href="{public_path('/admin-panel/devices')}">Urządzenia ESP32</a>
    </nav>
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
