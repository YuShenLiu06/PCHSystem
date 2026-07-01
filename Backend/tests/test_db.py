# Backend/tests/test_db.py
from app.core.db import Base, async_session_factory, get_session


def test_db_module_exports():
    assert Base is not None
    assert async_session_factory is not None
    assert callable(get_session)
