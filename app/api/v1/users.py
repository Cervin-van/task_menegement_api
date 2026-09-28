from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.schemas.user import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
