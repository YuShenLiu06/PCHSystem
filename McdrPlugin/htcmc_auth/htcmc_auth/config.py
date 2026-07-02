from mcdreforged.api.utils import Serializable


class HtcmcAuthConfig(Serializable):
    api_url: str = "http://localhost:8000"
    service_token: str = "change_me_service_token"
    http_timeout_seconds: float = 5.0
    http_retries: int = 2
    # 通知轮询（notifier.py）
    notify_poll_interval_seconds: float = 15.0
    notify_max_per_poll: int = 20
