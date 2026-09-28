from httpx import AsyncClient

from tests.conftest import DEFAULT_PASSWORD, UserFactory

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
ME = "/api/v1/users/me"


async def test_register_returns_user_without_password(client: AsyncClient) -> None:
    resp = await client.post(
        REGISTER,
        json={"email": "John@Example.com", "password": DEFAULT_PASSWORD, "full_name": "John"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "john@example.com"
    assert "password" not in body and "hashed_password" not in body


async def test_register_duplicate_email_is_case_insensitive(
    client: AsyncClient, create_user: UserFactory
) -> None:
    await create_user(email="dup@example.com")

    resp = await client.post(
        REGISTER,
        json={"email": "DUP@example.com", "password": DEFAULT_PASSWORD, "full_name": "Dup"},
    )

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"


async def test_register_short_password_fails_validation(client: AsyncClient) -> None:
    resp = await client.post(
        REGISTER, json={"email": "short@example.com", "password": "123", "full_name": "S"}
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_login_with_wrong_password_returns_401(
    client: AsyncClient, create_user: UserFactory
) -> None:
    user = await create_user()

    resp = await client.post(LOGIN, data={"username": user["email"], "password": "wrong-pass"})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert resp.headers["WWW-Authenticate"] == "Bearer"


async def test_login_unknown_email_returns_same_error(client: AsyncClient) -> None:
    resp = await client.post(LOGIN, data={"username": "nobody@example.com", "password": "x" * 8})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_CREDENTIALS"


async def test_me_returns_current_user(client: AsyncClient, create_user: UserFactory) -> None:
    user = await create_user()

    resp = await client.get(ME, headers=user["headers"])

    assert resp.status_code == 200
    assert resp.json()["id"] == user["id"]


async def test_me_without_token_returns_401(client: AsyncClient) -> None:
    resp = await client.get(ME)

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "NOT_AUTHENTICATED"


async def test_me_with_garbage_token_returns_401(client: AsyncClient) -> None:
    resp = await client.get(ME, headers={"Authorization": "Bearer not-a-jwt"})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_TOKEN"


async def test_refresh_issues_new_working_access_token(
    client: AsyncClient, create_user: UserFactory
) -> None:
    user = await create_user()

    resp = await client.post(REFRESH, json={"refresh_token": user["tokens"]["refresh_token"]})

    assert resp.status_code == 200
    new_access = resp.json()["access_token"]
    me = await client.get(ME, headers={"Authorization": f"Bearer {new_access}"})
    assert me.status_code == 200


async def test_access_token_cannot_be_used_as_refresh(
    client: AsyncClient, create_user: UserFactory
) -> None:
    user = await create_user()

    resp = await client.post(REFRESH, json={"refresh_token": user["tokens"]["access_token"]})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_TOKEN"


async def test_refresh_token_cannot_be_used_as_access(
    client: AsyncClient, create_user: UserFactory
) -> None:
    user = await create_user()

    resp = await client.get(
        ME, headers={"Authorization": f"Bearer {user['tokens']['refresh_token']}"}
    )

    assert resp.status_code == 401
