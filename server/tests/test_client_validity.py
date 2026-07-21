import unittest
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.database import Base
from app.models import AccessToken, Command, TokenClientUsage
from app.services import (
    CLIENT_COOKIE_NAME,
    client_key,
    client_validity_values,
    create_command_from_token,
    now_utc,
)


def request_with_client_cookie(client_id: str) -> Request:
    cookie = f"{CLIENT_COOKIE_NAME}={client_id}".encode("ascii")
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/pilot/test/press/1",
            "headers": [(b"cookie", cookie)],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
            "scheme": "https",
            "server": ("testserver", 443),
        }
    )


class ClientValidityTest(unittest.TestCase):
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
        self.token = AccessToken(
            token_value="test-token",
            device_id="test-device",
            gate_target="open_1",
            status="active",
            is_active=True,
            valid_from=current_time - timedelta(hours=1),
            valid_to=current_time + timedelta(days=1),
            valid_forever=False,
            max_uses=None,
            max_uses_per_client=None,
            client_validity_hours=1,
            used_count=0,
            open_cooldown_seconds=0,
        )
        self.db.add(self.token)
        self.db.flush()

        self.expired_client = "expired-phone-client-id-123456"
        self.db.add(
            TokenClientUsage(
                token_id=self.token.id,
                client_key=client_key(self.expired_client),
                used_count=1,
                created_at=current_time - timedelta(hours=2),
                last_used_at=current_time - timedelta(hours=2),
            )
        )
        self.db.commit()
        self.db.refresh(self.token)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_validity_is_counted_separately_for_each_client(self):
        with self.assertRaises(HTTPException) as error:
            create_command_from_token(
                self.db,
                token=self.token,
                requested_gate="1",
                request=request_with_client_cookie(self.expired_client),
            )

        self.assertEqual(error.exception.status_code, 403)
        self.assertEqual(self.db.query(Command).count(), 0)
        self.assertEqual(self.token.used_count, 0)

        fresh_client = "fresh-phone-client-id-123456789"
        fresh_request = request_with_client_cookie(fresh_client)
        create_command_from_token(
            self.db,
            token=self.token,
            requested_gate="1",
            request=fresh_request,
        )

        validity_hours, valid_until, expired = client_validity_values(
            self.db,
            token=self.token,
            request=fresh_request,
        )
        fresh_usage = (
            self.db.query(TokenClientUsage)
            .filter(TokenClientUsage.client_key == client_key(fresh_client))
            .one()
        )

        self.assertEqual(self.db.query(Command).count(), 1)
        self.assertEqual(fresh_usage.used_count, 1)
        self.assertEqual(validity_hours, 1)
        self.assertEqual(valid_until, fresh_usage.created_at + timedelta(hours=1))
        self.assertFalse(expired)


if __name__ == "__main__":
    unittest.main()
