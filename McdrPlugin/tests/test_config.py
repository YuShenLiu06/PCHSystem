"""config.json.example 与代码默认值的一致性守卫。

防止 example 与 HtcmcAuthConfig 默认值静默漂移（曾发生：notify_poll_interval_seconds
example 写成 15.0 而默认值 2.0，部署复制 example 后实际生效 15s，导致通知延迟 ~12s）。

行为参数（影响运行时语义）必须在 example 与代码默认间保持一致；
部署敏感字段（api_url=容器网络名、service_token=占位符）允许不一致，明确排除。
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import tests  # noqa: F401,E402  -- 触发 __init__ 安装 mcdreforged 替身 + sys.path

from htcmc_auth.config import HtcmcAuthConfig  # noqa: E402

_EXAMPLE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "htcmc_auth", "config.json.example"
)

# 必须与代码默认一致的「行为参数」字段；新增行为参数时同步登记于此。
_BEHAVIOR_FIELDS = (
    "http_timeout_seconds",
    "http_retries",
    "notify_poll_interval_seconds",
    "notify_max_per_poll",
)


class ExampleConsistencyTest(unittest.TestCase):
    def test_example_behavior_fields_match_code_defaults(self):
        # Arrange
        with open(_EXAMPLE_PATH, encoding="utf-8") as f:
            example = json.load(f)
        defaults = HtcmcAuthConfig()

        # Assert：每个行为参数在 example 与代码默认间必须一致
        for field in _BEHAVIOR_FIELDS:
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    example,
                    msg=f"{field} 缺失于 config.json.example，请补上（与代码默认值一致）",
                )
                self.assertEqual(
                    example[field],
                    getattr(defaults, field),
                    msg=(
                        f"{field} 漂移：config.json.example={example[field]!r}，"
                        f"代码默认={getattr(defaults, field)!r}。"
                        f"行为参数必须一致，避免部署复制 example 后实际生效值与设计意图不符。"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
