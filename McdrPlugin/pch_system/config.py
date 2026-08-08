from mcdreforged.api.utils import Serializable


class PchSystemConfig(Serializable):
    api_url: str = "http://localhost:8000"
    service_token: str = "change_me_service_token"
    http_timeout_seconds: float = 5.0
    http_retries: int = 2
    # 通知轮询（notifier.py）
    notify_poll_interval_seconds: float = 2.0
    notify_max_per_poll: int = 20
    # 施工进度追踪器（v0.9.0，construction_tracker.py）
    construction_enabled: bool = True                # 总开关（关 → 只 status 不采集）
    construction_flush_interval_seconds: float = 30.0  # flush 间隔（C-5；GET /settings 是 admin-only，追踪器调不了，用本地默认）
    world_stats_dir: str = "world/stats"             # stats 目录（相对服务端 cwd，或绝对路径）
    construction_max_batch: int = 1900               # 单次 POST 上限（< 后端 2000，留余量）
    construction_track_breaking: bool = False        # 挖掘预留（本期关；mined block id≠item id，开启需归一化）
    # 退避上限：后台轮询检测到后端连续不可达时，指数增大间隔，封顶于此值
    backoff_max_seconds: float = 60.0
