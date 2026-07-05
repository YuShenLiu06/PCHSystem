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

    # 投影解析：上传字节上限（默认 50MB）
    litematic_max_upload_bytes: int = 50 * 1024 * 1024

    # 归档根目录绝对路径（迁移 0009 sheet 三阶段生命周期的 archived 产物落盘位置）。
    # 空串 = 未配置：归档端点返 503，不启动 fail-fast 避免阻塞其他端点（计划 §归档服务 config）。
    archive_root: str = ""
    # 可选：仅加载静态 TemplateSection 覆盖文案（产品/运营改 header/footer 不动代码）。
    # 空串 = 不加载；目录不存在时 loader 静默返空。
    markdown_fragments_dir: str = ""

    # wiki 内容 git 仓（独立部署；后端把归档推送过去，wiki.js 双向同步）。空 = 未配置，
    # publisher 不推（R-8 重写后：归档 DB 成功即生效，wiki 同步为可选 best-effort 副产物）。
    wiki_git_remote_url: str = ""
    wiki_git_branch: str = "main"
    wiki_git_token: str = ""  # R-11：经 .env 注入，不进代码库
    wiki_git_author_name: str = "PCHSystem"
    wiki_git_author_email: str = "pchsystem@local"

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
