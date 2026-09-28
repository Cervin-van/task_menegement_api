import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import DBAPIError

from app.main import create_app


async def test_unhandled_exception_returns_unified_500() -> None:
    app = create_app()

    async def boom() -> None:
        raise RuntimeError("secret internals")

    app.add_api_route("/boom", boom)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/boom")

    assert resp.status_code == 500
    assert resp.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "Internal server error", "details": {}}
    }
    assert "secret" not in resp.text


async def test_unknown_route_uses_unified_format(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/nope")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "HTTP_ERROR"


class _PgError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("sqlstate", "status", "code"),
    [
        ("57014", 503, "DB_TIMEOUT"),  # statement_timeout
        ("55P03", 503, "DB_TIMEOUT"),  # lock_timeout
        ("22021", 422, "INVALID_DATA"),  # invalid byte sequence
        ("42P01", 500, "INTERNAL_ERROR"),  # anything else
    ],
)
async def test_db_errors_are_mapped(sqlstate: str, status: int, code: str) -> None:
    app = create_app()

    async def fail() -> None:
        raise DBAPIError("SELECT 1", {}, _PgError(sqlstate))

    app.add_api_route("/fail", fail)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/fail")

    assert resp.status_code == status
    assert resp.json()["error"]["code"] == code
