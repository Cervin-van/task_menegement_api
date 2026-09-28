import math
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Self

from fastapi import Path
from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field

# Postgres INTEGER bounds: larger ids must be 422, not a DataError/500 from asyncpg.
INT32_MAX = 2_147_483_647
# Body ids are strict: no true -> 1 or "1" -> 1 coercion. Query ids stay lax (always strings).
DbId = Annotated[int, Field(strict=True, ge=1, le=INT32_MAX)]
DbIdQuery = Annotated[int, Field(ge=1, le=INT32_MAX)]
DbIdPath = Annotated[int, Path(ge=1, le=INT32_MAX)]
MAX_PAGE_SIZE = 100


def _no_nul(value: str) -> str:
    # Postgres text can't store \x00 -> reject at the edge instead of a DB error.
    if "\x00" in value:
        raise ValueError("NUL character is not allowed")
    return value


def _not_a_number(value: object) -> object:
    # Lax datetime parsing would accept a unix timestamp (e.g. 1900000000): require ISO 8601.
    if isinstance(value, int | float):
        raise ValueError("datetime must be an ISO 8601 string")
    return value


def _storable_year(value: datetime) -> datetime:
    # Converting to UTC must stay within datetime range (year 1..9999) for the DB driver.
    if not 2 <= value.year <= 9998:
        raise ValueError("year must be between 2 and 9998")
    return value


SafeStr = Annotated[str, AfterValidator(_no_nul)]
DbDatetime = Annotated[
    AwareDatetime, BeforeValidator(_not_a_number), AfterValidator(_storable_year)
]


class PageParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page: int = Field(default=1, ge=1, le=INT32_MAX // MAX_PAGE_SIZE)  # OFFSET stays in int32
    size: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE)

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
