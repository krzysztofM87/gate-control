from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Gate Control")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "gate-control",
    }


@app.get("/")
def index():
    return {
        "status": "ok",
        "message": "Gate Control server is running",
    }


@app.get("/brama/{token}", response_class=HTMLResponse)
def gate_page(token: str):
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
            }}
            button {{
                font-size: 22px;
                padding: 18px 28px;
                border-radius: 12px;
                border: none;
                cursor: pointer;
            }}
        </style>
    </head>
    <body>
        <h1>Otwieranie bramy</h1>
        <p>Token: {token}</p>
        <form method="post" action="/brama/{token}/open">
            <button type="submit">Otwórz bramę</button>
        </form>
    </body>
    </html>
    """


@app.post("/brama/{token}/open")
def open_gate(token: str):
    return {
        "status": "queued",
        "token": token,
        "message": "Command queued for gate device",
    }


@app.get("/api/device/poll")
def device_poll():
    return {
        "command": "none",
    }


@app.post("/api/device/ack")
def device_ack():
    return {
        "status": "ok",
    }
