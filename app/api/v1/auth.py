from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.api.deps import AuthServiceDep
from app.schemas.auth import RefreshRequest, TokenPair, UserCreate
from app.schemas.user import UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def register(data: UserCreate, auth: AuthServiceDep) -> UserRead:
    return UserRead.model_validate(await auth.register(data))


@router.post("/login")
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], auth: AuthServiceDep
) -> TokenPair:
    """OAuth2 password flow: `username` = email. Works with Swagger "Authorize"."""
    return await auth.login(form.username, form.password)


@router.post("/refresh")
async def refresh(data: RefreshRequest, auth: AuthServiceDep) -> TokenPair:
    return await auth.refresh(data.refresh_token)
