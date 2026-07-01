from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = "pch"
    postgres_password: str = ""
    postgres_db: str = "pchsystem"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    jwt_secret: str = ""
    jwt_access_ttl_seconds: int = 3600
    jwt_refresh_ttl_seconds: int = 604800

    auth_token_ttl_seconds: int = 600
    auth_token_rate_limit_seconds: int = 30

    mcdr_service_token: str = ""
    web_base_url: str = "http://localhost:5173"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_dsn_sync(self) -> str:
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


def get_settings() -> Settings:
    return Settings()
