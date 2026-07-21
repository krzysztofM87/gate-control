import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import AccessToken, Command, CommandLog, TokenClientUsage, VirtualPilotButton
from app.services import delete_access_token, now_utc


class TokenDeletionTest(unittest.TestCase):
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

    def test_delete_token_cancels_open_commands_and_keeps_history(self):
        current_time = now_utc()
        token = AccessToken(
            token_value="token-to-delete",
            label="Pilot testowy",
            device_id="test-device",
            gate_target="open_1",
            status="active",
            is_active=True,
            valid_from=current_time - timedelta(hours=1),
            valid_to=current_time + timedelta(hours=1),
            valid_forever=False,
            used_count=3,
            open_cooldown_seconds=0,
        )
        self.db.add(token)
        self.db.flush()

        self.db.add(
            TokenClientUsage(
                token_id=token.id,
                client_key="a" * 64,
                used_count=2,
            )
        )
        self.db.add(
            VirtualPilotButton(
                token_id=token.id,
                label="Brama testowa",
                device_id="test-device",
                command="open_1",
                sort_order=0,
            )
        )
        self.db.add_all(
            [
                Command(
                    command_id="pending-command",
                    token_id=token.id,
                    device_id="test-device",
                    command="open_1",
                    status="pending",
                ),
                Command(
                    command_id="sent-command",
                    token_id=token.id,
                    device_id="test-device",
                    command="open_1",
                    status="sent",
                ),
                Command(
                    command_id="done-command",
                    token_id=token.id,
                    device_id="test-device",
                    command="open_1",
                    status="done",
                ),
            ]
        )
        self.db.commit()
        token_id = token.id

        result = delete_access_token(self.db, token=token)

        commands = {
            command.command_id: command
            for command in self.db.query(Command).order_by(Command.id).all()
        }
        deletion_log = (
            self.db.query(CommandLog)
            .filter(CommandLog.event_type == "token_deleted")
            .one()
        )

        self.assertEqual(result["cancelled_commands"], 2)
        self.assertEqual(result["deleted_client_usages"], 1)
        self.assertEqual(result["deleted_virtual_buttons"], 1)
        self.assertIsNone(
            self.db.query(AccessToken).filter(AccessToken.id == token_id).first()
        )
        self.assertEqual(
            self.db.query(TokenClientUsage)
            .filter(TokenClientUsage.token_id == token_id)
            .count(),
            0,
        )
        self.assertEqual(
            self.db.query(VirtualPilotButton)
            .filter(VirtualPilotButton.token_id == token_id)
            .count(),
            0,
        )
        self.assertEqual(commands["pending-command"].status, "cancelled")
        self.assertEqual(commands["sent-command"].status, "cancelled")
        self.assertEqual(commands["done-command"].status, "done")
        self.assertEqual(deletion_log.token_id, token_id)


if __name__ == "__main__":
    unittest.main()
