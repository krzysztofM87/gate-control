from typing import Optional

from pydantic import BaseModel, Field

from app.config import OPEN_COOLDOWN_SECONDS, TOKEN_DEFAULT_VALID_HOURS


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

    # null = bez limitu u?y?
    max_uses: Optional[int] = Field(default=10, ge=1, le=1000)

    # null = bez limitu dla pojedynczego telefonu
    max_uses_per_client: Optional[int] = Field(default=None, ge=1, le=1000)

    open_cooldown_seconds: int = Field(default=OPEN_COOLDOWN_SECONDS, ge=0, le=3600)


class CreateDeviceRequest(BaseModel):
    device_id: str
    name: Optional[str] = None
    secret: Optional[str] = None
    is_active: bool = True
