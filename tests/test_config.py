import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_short_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(jwt_secret_key="too-short", _env_file=None)  # type: ignore[call-arg]
