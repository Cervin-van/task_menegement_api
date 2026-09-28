from httpx import ASGITransport, AsyncClient

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
