import unittest
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.database import Base
from app.models import AccessToken, Command, CommandLog, TokenClientUsage, VirtualPilotButton
from app.routes.public import client_pilot_page
from app.services import now_utc, reactivate_access_token, validate_access_token


class TokenReactivationTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            future=True,
        )
        Base.metadata.create_all(bind=self.engine)
        session_factory = sessionmaker(bind=self.engine, future=True)
        self.db = session_factory()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_reactivation_keeps_link_and_resets_access_state(self):
        current_time = now_utc()
        original_duration = timedelta(hours=2)
        token = AccessToken(
            token_value="same-secret-link",
            device_id="device-a",
            gate_target="open_1",
            status="used",
            is_active=False,
            valid_from=current_time - timedelta(hours=3),
            valid_to=current_time - timedelta(hours=1),
            valid_forever=False,
            max_uses=2,
            max_uses_per_client=1,
            client_validity_hours=1,
            used_count=2,
            last_used_at=current_time - timedelta(hours=1),
            open_cooldown_seconds=5,
        )
        self.db.add(token)
        self.db.flush()
        self.db.add_all(
            [
                TokenClientUsage(
                    token_id=token.id,
                    client_key="a" * 64,
                    used_count=1,
                ),
                VirtualPilotButton(
                    token_id=token.id,
                    label="Brama A",
                    device_id="device-a",
                    command="open_1",
                    sort_order=0,
                ),
                TokenClientUsage(
                    token_id=token.id,
                    client_key="b" * 64,
                    used_count=1,
                ),
                Command(
                    command_id="pending-command",
                    token_id=token.id,
                    device_id="device-a",
                    command="open_1",
                    status="pending",
                ),
                Command(
                    command_id="sent-command",
                    token_id=token.id,
                    device_id="device-a",
                    command="open_1",
                    status="sent",
                ),
                Command(
                    command_id="done-command",
                    token_id=token.id,
                    device_id="device-a",
                    command="open_1",
                    status="done",
                ),
            ]
        )
        self.db.commit()

        result = reactivate_access_token(self.db, token=token)

        commands = {
            command.command_id: command.status
            for command in self.db.query(Command).all()
        }
        reactivation_log = (
            self.db.query(CommandLog)
            .filter(CommandLog.event_type == "token_reactivated")
            .one()
        )

        self.assertEqual(token.token_value, "same-secret-link")
        self.assertTrue(token.is_active)
        self.assertEqual(token.status, "active")
        self.assertEqual(token.used_count, 0)
        self.assertIsNone(token.last_used_at)
        self.assertEqual(token.valid_to - token.valid_from, original_duration)
        self.assertGreater(token.valid_to, current_time)
        self.assertEqual(self.db.query(TokenClientUsage).count(), 0)
        self.assertEqual(self.db.query(VirtualPilotButton).count(), 1)
        self.assertEqual(commands["pending-command"], "cancelled")
        self.assertEqual(commands["sent-command"], "cancelled")
        self.assertEqual(commands["done-command"], "done")
        self.assertEqual(result["cancelled_commands"], 2)
        self.assertEqual(result["reset_client_usages"], 2)
        self.assertEqual(reactivation_log.token_id, token.id)

    def test_exhausted_pilot_page_has_clear_message(self):
        current_time = now_utc()
        token = AccessToken(
            token_value="used-page-token",
            pilot_title="Pilot testowy",
            device_id="device-a",
            gate_target="open_1",
            status="used",
            is_active=True,
            valid_from=current_time - timedelta(hours=1),
            valid_to=current_time + timedelta(hours=1),
            valid_forever=False,
            max_uses=1,
            used_count=1,
            open_cooldown_seconds=0,
        )
        self.db.add(token)
        self.db.commit()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/pilot/used-page-token",
                "headers": [],
                "query_string": b"",
                "client": ("127.0.0.1", 12345),
                "scheme": "https",
                "server": ("testserver", 443),
            }
        )

        response = client_pilot_page(token.token_value, request, self.db)
        body = response.body.decode("utf-8")

        self.assertEqual(response.status_code, 403)
        self.assertIn("Limit użyć wyczerpany", body)
        self.assertIn("Administrator może ponownie aktywować pilot", body)

    def test_used_token_is_reported_as_exhausted(self):
        current_time = now_utc()
        token = AccessToken(
            token_value="used-token",
            device_id="device-a",
            gate_target="open_1",
            status="used",
            is_active=True,
            valid_from=current_time - timedelta(hours=1),
            valid_to=current_time + timedelta(hours=1),
            valid_forever=False,
            max_uses=1,
            used_count=1,
            open_cooldown_seconds=0,
        )
        self.db.add(token)
        self.db.commit()
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/pilot/used-token",
                "headers": [],
                "query_string": b"",
                "client": ("127.0.0.1", 12345),
                "scheme": "https",
                "server": ("testserver", 443),
            }
        )

        with self.assertRaises(HTTPException) as error:
            validate_access_token(self.db, token.token_value, request)

        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(error.exception.detail, "Token use limit reached")


if __name__ == "__main__":
    unittest.main()
