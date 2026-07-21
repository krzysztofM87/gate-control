import asyncio
import unittest
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.database import Base
from app.models import AccessToken, Command, Device, TokenClientUsage
from app.routes import admin_panel as admin_panel_routes
from app.routes.public import client_pilot_page, gate_page
from app.services import (
    CLIENT_COOKIE_NAME,
    create_command_from_token,
    create_command_from_virtual_button,
    create_virtual_pilot_button,
    device_counts,
    now_utc,
    update_virtual_pilot_button,
    virtual_button_or_404,
    virtual_buttons_for_token,
)


def request_with_client_cookie(client_id: str) -> Request:
    cookie = f"{CLIENT_COOKIE_NAME}={client_id}".encode("ascii")
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/pilot/virtual-token/press-button/1",
            "headers": [(b"cookie", cookie)],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "scheme": "https",
            "server": ("testserver", 443),
        }
    )


def request_with_form(values: dict[str, str]) -> Request:
    body = urlencode(values).encode("ascii")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin-panel/devices/device-a/delete",
            "headers": [
                (b"content-type", b"application/x-www-form-urlencoded"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "scheme": "https",
            "server": ("testserver", 443),
        },
        receive,
    )


class VirtualPilotTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            future=True,
        )
        Base.metadata.create_all(bind=self.engine)
        session_factory = sessionmaker(bind=self.engine, future=True)
        self.db = session_factory()
        current_time = now_utc()
        self.db.add_all(
            [
                Device(device_id="device-a", name="Brama A", is_active=True),
                Device(device_id="device-b", name="Brama B", is_active=True),
            ]
        )
        self.token = AccessToken(
            token_value="virtual-token",
            label="Wspólny pilot",
            pilot_title="Osiedle",
            is_virtual=True,
            device_id="device-a",
            gate_target="open_1",
            status="active",
            is_active=True,
            valid_from=current_time - timedelta(hours=1),
            valid_to=current_time + timedelta(hours=1),
            valid_forever=False,
            max_uses=None,
            max_uses_per_client=None,
            used_count=0,
            open_cooldown_seconds=0,
        )
        self.db.add(self.token)
        self.db.commit()
        self.db.refresh(self.token)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_buttons_target_different_devices_and_render_in_order(self):
        later_button = create_virtual_pilot_button(
            self.db,
            token=self.token,
            label="Brama A",
            device_id="device-a",
            command="open_1",
            sort_order=20,
        )
        first_button = create_virtual_pilot_button(
            self.db,
            token=self.token,
            label="Brama B",
            device_id="device-b",
            command="open_2",
            sort_order=10,
        )

        buttons = virtual_buttons_for_token(self.db, self.token.id)
        self.assertEqual([button.id for button in buttons], [first_button.id, later_button.id])

        page_request = request_with_client_cookie("virtual-page-client-id-123456")
        page_request.scope["method"] = "GET"
        response = client_pilot_page(self.token.token_value, page_request, self.db)
        body = response.body.decode("utf-8")
        self.assertLess(body.index("Brama B"), body.index("Brama A"))
        self.assertIn(f"press-button/{first_button.id}", body)
        self.assertIn(f"press-button/{later_button.id}", body)

        command = create_command_from_virtual_button(
            self.db,
            token=self.token,
            button_id=first_button.id,
            request=request_with_client_cookie("virtual-phone-client-id-12345"),
        )

        self.assertEqual(command.device_id, "device-b")
        self.assertEqual(command.command, "open_2")
        self.assertEqual(self.token.used_count, 1)
        self.assertEqual(self.db.query(Command).count(), 1)
        self.assertEqual(self.db.query(TokenClientUsage).count(), 1)
        self.assertEqual(device_counts(self.db, "device-b")["tokens"], 1)

        with self.assertRaises(HTTPException) as error:
            create_command_from_token(
                self.db,
                token=self.token,
                requested_gate="1",
                request=request_with_client_cookie("legacy-route-client-id-123456"),
            )

        self.assertEqual(error.exception.status_code, 400)
        self.assertEqual(self.db.query(Command).count(), 1)

    def test_button_update_and_token_ownership_are_enforced(self):
        button = create_virtual_pilot_button(
            self.db,
            token=self.token,
            label="Stara nazwa",
            device_id="device-a",
            command="open_1",
            sort_order=10,
        )
        updated = update_virtual_pilot_button(
            self.db,
            token=self.token,
            button=button,
            changes={
                "label": "Obie bramy B",
                "device_id": "device-b",
                "command": "open_both",
                "sort_order": 5,
            },
        )

        self.assertEqual(updated.label, "Obie bramy B")
        self.assertEqual(updated.device_id, "device-b")
        self.assertEqual(updated.command, "open_both")
        self.assertEqual(updated.sort_order, 5)

        other_token = AccessToken(
            token_value="other-token",
            is_virtual=True,
            device_id="device-a",
            gate_target="open_1",
            status="active",
            is_active=True,
            valid_from=now_utc() - timedelta(hours=1),
            valid_to=now_utc() + timedelta(hours=1),
            valid_forever=False,
            used_count=0,
            open_cooldown_seconds=0,
        )
        self.db.add(other_token)
        self.db.commit()

        with self.assertRaises(HTTPException) as error:
            virtual_button_or_404(
                self.db,
                token=other_token,
                button_id=button.id,
            )

        self.assertEqual(error.exception.status_code, 404)

    def test_admin_panel_contains_virtual_pilot_editor(self):
        button = create_virtual_pilot_button(
            self.db,
            token=self.token,
            label="Furtka",
            device_id="device-a",
            command="open_1",
            sort_order=10,
        )
        request = request_with_client_cookie("admin-panel-client-id-123456789")
        request.scope["method"] = "GET"
        original_auth_check = admin_panel_routes.is_admin_panel_authorized
        admin_panel_routes.is_admin_panel_authorized = lambda _request: True

        try:
            edit_response = admin_panel_routes.admin_panel_edit_token(
                self.token.id,
                request,
                self.db,
            )
            main_response = admin_panel_routes.admin_panel(request, self.db)
        finally:
            admin_panel_routes.is_admin_panel_authorized = original_auth_check

        edit_body = edit_response.body.decode("utf-8")
        main_body = main_response.body.decode("utf-8")
        self.assertIn("Przyciski pilota wirtualnego", edit_body)
        self.assertIn("Furtka", edit_body)
        self.assertIn(f"buttons/{button.id}/update", edit_body)
        self.assertIn('option value="virtual"', main_body)

    def test_legacy_page_redirects_virtual_pilot_to_button_layout(self):
        request = request_with_client_cookie("legacy-page-client-id-123456")
        request.scope["method"] = "GET"

        response = gate_page(self.token.token_value, request, self.db)

        self.assertEqual(response.status_code, 307)
        self.assertTrue(
            response.headers["location"].endswith(
                f"/pilot/{self.token.token_value}"
            )
        )

    def test_deleting_device_removes_only_its_virtual_buttons(self):
        create_virtual_pilot_button(
            self.db,
            token=self.token,
            label="Brama A",
            device_id="device-a",
            command="open_1",
            sort_order=10,
        )
        surviving_button = create_virtual_pilot_button(
            self.db,
            token=self.token,
            label="Brama B",
            device_id="device-b",
            command="open_2",
            sort_order=20,
        )
        physical_token = AccessToken(
            token_value="physical-token",
            is_virtual=False,
            device_id="device-a",
            gate_target="open_1",
            status="active",
            is_active=True,
            valid_from=now_utc() - timedelta(hours=1),
            valid_to=now_utc() + timedelta(hours=1),
            valid_forever=False,
            used_count=0,
            open_cooldown_seconds=0,
        )
        self.db.add(physical_token)
        self.db.commit()
        physical_token_id = physical_token.id
        original_auth_check = admin_panel_routes.is_admin_panel_authorized
        admin_panel_routes.is_admin_panel_authorized = lambda _request: True

        try:
            response = asyncio.run(
                admin_panel_routes.admin_panel_delete_device(
                    "device-a",
                    request_with_form({"confirm": "USUN", "delete_tokens": "1"}),
                    self.db,
                )
            )
        finally:
            admin_panel_routes.is_admin_panel_authorized = original_auth_check

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(
            self.db.query(AccessToken).filter(AccessToken.id == self.token.id).first()
        )
        self.assertIsNone(
            self.db.query(AccessToken)
            .filter(AccessToken.id == physical_token_id)
            .first()
        )
        buttons = virtual_buttons_for_token(self.db, self.token.id)
        self.assertEqual([button.id for button in buttons], [surviving_button.id])
        self.assertIsNone(
            self.db.query(Device).filter(Device.device_id == "device-a").first()
        )


if __name__ == "__main__":
    unittest.main()
