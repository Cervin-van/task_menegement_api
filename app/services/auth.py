from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, ConflictError
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models import User
from app.repositories.user import UserRepository
from app.schemas.auth import TokenPair, UserCreate

# Verified when the email is unknown so response time doesn't reveal registered emails.
_DUMMY_HASH = hash_password("dummy-password-for-timing")


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def register(self, data: UserCreate) -> User:
        email = data.email.lower()
        if await self.users.get_by_email(email):
            raise self._email_taken(email)

        user = User(
            email=email, full_name=data.full_name, hashed_password=hash_password(data.password)
        )
        try:
            await self.users.add(user)
            await self.session.commit()
        except IntegrityError as exc:  # concurrent registration with the same email
            await self.session.rollback()
            raise self._email_taken(email) from exc
        return user

    async def login(self, email: str, password: str) -> TokenPair:
        user = await self.users.get_by_email(email.lower())
        if user is None:
            verify_password(password, _DUMMY_HASH)
            raise self._invalid_credentials()
        if not verify_password(password, user.hashed_password):
            raise self._invalid_credentials()
        return self._issue_tokens(user.id)

    async def refresh(self, refresh_token: str) -> TokenPair:
        user_id = decode_token(refresh_token, TokenType.REFRESH)
        if await self.users.get_by_id(user_id) is None:
            raise AuthenticationError("User not found", code="INVALID_TOKEN")
        return self._issue_tokens(user_id)

    async def get_user_by_token(self, access_token: str) -> User:
        user_id = decode_token(access_token, TokenType.ACCESS)
        user = await self.users.get_by_id(user_id)
        if user is None:
            raise AuthenticationError("User not found", code="INVALID_TOKEN")
        return user

    @staticmethod
    def _issue_tokens(user_id: int) -> TokenPair:
        return TokenPair(
            access_token=create_access_token(user_id),
            refresh_token=create_refresh_token(user_id),
        )

    @staticmethod
    def _email_taken(email: str) -> ConflictError:
        return ConflictError(
            "User with this email already exists",
            code="EMAIL_ALREADY_EXISTS",
            details={"email": email},
        )

    @staticmethod
    def _invalid_credentials() -> AuthenticationError:
        return AuthenticationError("Incorrect email or password", code="INVALID_CREDENTIALS")
