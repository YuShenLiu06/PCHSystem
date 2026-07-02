from mcdreforged.api.utils import Serializable


class HtcmcAuthConfig(Serializable):
    api_url: str = "http://localhost:8000"
    service_token: str = "change_me_service_token"
    http_timeout_seconds: float = 5.0
    http_retries: int = 2
