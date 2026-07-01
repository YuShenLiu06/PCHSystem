from mcdreforged.api.config import Config


class HtcmcAuthConfig(Config):
    api_url: str = "http://localhost:8000"
    service_token: str = "change_me_service_token"
    http_timeout_seconds: float = 5.0
    http_retries: int = 2
