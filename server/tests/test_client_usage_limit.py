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


class ClientUsageLimitTest(unittest.TestCase):
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
            valid_to=current_time + timedelta(hours=1),
            valid_forever=False,
            max_uses=None,
            max_uses_per_client=1,
            used_count=0,
            open_cooldown_seconds=0,
        )
        self.db.add(self.token)
        self.db.commit()
        self.db.refresh(self.token)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_limit_is_counted_separately_for_each_client(self):
        first_client = "first-phone-client-id-123456789"
        second_client = "second-phone-client-id-12345678"

        create_command_from_token(
            self.db,
            token=self.token,
            requested_gate="1",
            request=request_with_client_cookie(first_client),
        )

        with self.assertRaises(HTTPException) as error:
            create_command_from_token(
                self.db,
                token=self.token,
                requested_gate="1",
                request=request_with_client_cookie(first_client),
            )

        self.assertEqual(error.exception.status_code, 403)

        create_command_from_token(
            self.db,
            token=self.token,
            requested_gate="1",
            request=request_with_client_cookie(second_client),
        )

        usages = self.db.query(TokenClientUsage).order_by(TokenClientUsage.id).all()

        self.assertEqual(self.db.query(Command).count(), 2)
        self.assertEqual([usage.used_count for usage in usages], [1, 1])
        self.assertEqual(usages[0].client_key, client_key(first_client))
        self.assertNotEqual(usages[0].client_key, first_client)

    def test_count_increases_after_client_limit_is_raised(self):
        client_id = "raised-limit-phone-client-id-12345"
        request = request_with_client_cookie(client_id)

        create_command_from_token(
            self.db,
            token=self.token,
            requested_gate="1",
            request=request,
        )

        self.token.max_uses_per_client = 2
        self.db.commit()

        create_command_from_token(
            self.db,
            token=self.token,
            requested_gate="1",
            request=request,
        )

        usage = (
            self.db.query(TokenClientUsage)
            .filter(TokenClientUsage.client_key == client_key(client_id))
            .one()
        )
        self.assertEqual(usage.used_count, 2)

    def test_count_continues_when_client_limit_is_removed(self):
        client_id = "unlimited-phone-client-id-12345678"
        request = request_with_client_cookie(client_id)

        create_command_from_token(
            self.db,
            token=self.token,
            requested_gate="1",
            request=request,
        )

        self.token.max_uses_per_client = None
        self.db.commit()

        create_command_from_token(
            self.db,
            token=self.token,
            requested_gate="1",
            request=request,
        )

        usage = (
            self.db.query(TokenClientUsage)
            .filter(TokenClientUsage.client_key == client_key(client_id))
            .one()
        )
        self.assertEqual(usage.used_count, 2)


if __name__ == "__main__":
    unittest.main()
