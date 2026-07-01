from app.core.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "pw")
    monkeypatch.setenv("JWT_SECRET", "s3cret")
    monkeypatch.setenv("MCDR_SERVICE_TOKEN", "svc")
    s = Settings()
    assert s.postgres_dsn.startswith("postgresql+asyncpg://")
    assert s.jwt_access_ttl_seconds == 3600
    assert s.web_base_url.startswith("http")
