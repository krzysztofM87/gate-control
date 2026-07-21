import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import COMMAND_PENDING_TIMEOUT_SECONDS
from app.database import Base
from app.models import Command, Device
from app.routes.device import device_poll
from app.services import now_utc


class CommandTimeoutTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            future=True,
        )
        Base.metadata.create_all(bind=self.engine)
        session_factory = sessionmaker(bind=self.engine, future=True)
        self.db = session_factory()
        self.db.add(Device(device_id="test-device", secret="secret", is_active=True))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_poll_skips_expired_pending_command(self):
        current_time = now_utc()
        expired = Command(
            command_id="expired-command",
            device_id="test-device",
            command="open_1",
            status="pending",
            relay_time_ms=700,
            created_at=current_time
            - timedelta(seconds=COMMAND_PENDING_TIMEOUT_SECONDS + 1),
        )
        fresh = Command(
            command_id="fresh-command",
            device_id="test-device",
            command="open_2",
            status="pending",
            relay_time_ms=700,
            created_at=current_time,
        )
        self.db.add_all([expired, fresh])
        self.db.commit()

        response = device_poll(
            device_id="test-device",
            x_device_id=None,
            x_device_secret="secret",
            x_device_token=None,
            db=self.db,
        )

        self.db.refresh(expired)
        self.db.refresh(fresh)

        self.assertEqual(response["command_id"], fresh.command_id)
        self.assertEqual(expired.status, "failed")
        self.assertIn("expired", expired.message)
        self.assertEqual(expired.delivered_count, 0)
        self.assertEqual(fresh.status, "sent")
        self.assertEqual(fresh.delivered_count, 1)


if __name__ == "__main__":
    unittest.main()
