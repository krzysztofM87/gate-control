import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AccessToken, Command, CommandLog, Device, TokenClientUsage
from app.services import now_utc, update_access_token


class TokenUpdateTest(unittest.TestCase):
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

    def test_update_keeps_link_and_usage_but_cancels_old_routing(self):
        current_time = now_utc()
        self.db.add_all(
            [
                Device(device_id="device-a", name="A", is_active=True),
                Device(device_id="device-b", name="B", is_active=True),
            ]
        )
        token = AccessToken(
            token_value="unchanged-secret-token",
            label="Stara nazwa",
            device_id="device-a",
            gate_target="open_1",
            status="used",
            is_active=True,
            valid_from=current_time - timedelta(hours=1),
            valid_to=current_time + timedelta(hours=1),
            valid_forever=False,
            max_uses=3,
            max_uses_per_client=2,
            used_count=3,
            open_cooldown_seconds=5,
        )
        self.db.add(token)
        self.db.flush()
        self.db.add(
            TokenClientUsage(
                token_id=token.id,
                client_key="b" * 64,
                used_count=2,
            )
        )
        self.db.add(
            Command(
                command_id="old-routing-command",
                token_id=token.id,
                device_id="device-a",
                command="open_1",
                status="pending",
            )
        )
        self.db.commit()

        result = update_access_token(
            self.db,
            token=token,
            changes={
                "label": "Nowa nazwa",
                "device_id": "device-b",
                "gate_target": "open_2",
                "max_uses": 5,
                "open_cooldown_seconds": 10,
                "is_active": True,
            },
        )

        command = self.db.query(Command).filter_by(command_id="old-routing-command").one()
        usage = self.db.query(TokenClientUsage).filter_by(token_id=token.id).one()
        update_log = self.db.query(CommandLog).filter_by(event_type="token_updated").one()

        self.assertEqual(token.token_value, "unchanged-secret-token")
        self.assertEqual(token.label, "Nowa nazwa")
        self.assertEqual(token.device_id, "device-b")
        self.assertEqual(token.gate_target, "open_2")
        self.assertEqual(token.used_count, 3)
        self.assertEqual(usage.used_count, 2)
        self.assertEqual(token.status, "active")
        self.assertEqual(command.status, "cancelled")
        self.assertEqual(result["cancelled_commands"], 1)
        self.assertEqual(update_log.token_id, token.id)


if __name__ == "__main__":
    unittest.main()
