from fastapi import FastAPI

from app.config import PUBLIC_PATH_PREFIX
from app.database import SessionLocal, init_db
from app.routes import admin, admin_panel, device, public
from app.services import ensure_configured_device, expire_pending_commands, run_schema_migrations


app = FastAPI(
    title="Gate Control",
    root_path=PUBLIC_PATH_PREFIX,
)


@app.on_event("startup")
def startup() -> None:
    init_db()
    run_schema_migrations()

    db = SessionLocal()

    try:
        ensure_configured_device(db)

        if expire_pending_commands(db):
            db.commit()
    finally:
        db.close()


app.include_router(public.router)
app.include_router(device.router)
app.include_router(admin.router)
app.include_router(admin_panel.router)
