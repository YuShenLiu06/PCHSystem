"""后台轮询退避计算（notifier + construction_tracker 共用）。

连续网络失败时指数增大轮询间隔，封顶于配置的 ``backoff_max_seconds``。
"""
_BACKOFF_EXPONENT_CAP = 6


def backoff_interval(
    base_interval: float, consecutive_failures: int, max_seconds: float
) -> float:
    """连续失败时指数增大间隔，封顶 ``max_seconds``。

    ``consecutive_failures`` ≤ 0 → 返回 ``base_interval``（正常频率）。
    指数底 = 2，指数上限 ``_BACKOFF_EXPONENT_CAP``（=6 → 最大 ×64）。
    """
    if consecutive_failures <= 0:
        return base_interval
    return min(
        base_interval * 2 ** min(consecutive_failures, _BACKOFF_EXPONENT_CAP),
        max_seconds,
    )
