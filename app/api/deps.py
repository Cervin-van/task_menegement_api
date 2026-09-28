from typing import Annotated

from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError
from app.db.session import get_session
from app.models import User
from app.services.auth import AuthService

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# auto_error=False: missing token is reported in our unified error format.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def get_auth_service(session: SessionDep) -> AuthService:
    return AuthService(session)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)], auth: AuthServiceDep
) -> User:
    if not token:
        raise AuthenticationError("Not authenticated")
    return await auth.get_user_by_token(token)


CurrentUser = Annotated[User, Depends(get_current_user)]
