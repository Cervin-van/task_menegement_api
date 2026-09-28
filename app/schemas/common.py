import math
from collections.abc import Sequence
from typing import Annotated, Self

from fastapi import Path
from pydantic import BaseModel, Field

# Postgres INTEGER bounds: larger ids must be 422, not a DataError/500 from asyncpg.
INT32_MAX = 2_147_483_647
DbId = Annotated[int, Field(ge=1, le=INT32_MAX)]
DbIdPath = Annotated[int, Path(ge=1, le=INT32_MAX)]


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int

    @classmethod
    def build(cls, items: Sequence[object], total: int, params: PageParams) -> Self:
        """`items` may be ORM objects: validated into T via from_attributes."""
        return cls.model_validate(
            {
                "items": list(items),
                "total": total,
                "page": params.page,
                "size": params.size,
                "pages": math.ceil(total / params.size),
            },
            from_attributes=True,
        )
