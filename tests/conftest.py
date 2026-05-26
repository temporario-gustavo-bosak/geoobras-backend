from __future__ import annotations

import pytest
from unittest.mock import MagicMock


@pytest.fixture
def mock_db_session() -> MagicMock:
    """
    SQLAlchemy Session mock that supports the two call chains used by repositories:
      - session.execute(...).mappings().all()  → []
      - session.execute(...).scalar()          → 0
    """
    session = MagicMock()
    execute_result = MagicMock()
    session.execute.return_value = execute_result
    execute_result.mappings.return_value.all.return_value = []
    execute_result.scalar.return_value = 0
    return session
