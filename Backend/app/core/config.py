from pydantic import Field, field_validator
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

    @field_validator("mcdr_service_token")
    @classmethod
    def _mcdr_service_token_non_empty(cls, v: str) -> str:
        """H-1'：service-token 是 MCDR 代玩家写的唯一共享密钥，空串 = 完全开放，
        启动即 fail-fast（R-11 经环境注入，绝不硬编码、不留默认空）。
        """
        if not v or not v.strip():
            raise ValueError(
                "MCDR_SERVICE_TOKEN must be set to a non-empty value "
                "(service-token 代理写通道的共享密钥，禁止空)"
            )
        return v

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
