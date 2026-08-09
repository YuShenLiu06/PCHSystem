#!/usr/bin/env python3
"""按天拉取 GitHub Traffic clone 数据，永久存档到 clone-stats.json。

GitHub Traffic API 只保留最近 14 天数据。本脚本每次运行时：

1. 调用 ``/traffic/clones?per=day``，拿到最近 14 天的每日明细
2. 将每日数据 upsert 到 JSON 的 ``daily{}``（按日期 key，已有则覆盖）
3. 总量 = ``base_offset`` + ``sum(daily)`` —— 无差值运算，不丢数据
4. 首次运行时自动计算 ``base_offset``（= 旧总量 - sum(daily)），保证总量不跳变

用法（在 GitHub Actions 中由 clone-stats.yml 调用）::

    GH_TOKEN=<token> GITHUB_REPOSITORY=<owner>/<repo> python3 clone-stats.py
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
JSON_PATH = REPO_ROOT / ".github" / "clone-stats.json"


def fetch_traffic_daily(repo: str) -> list[dict]:
    """调用 GitHub Traffic API，返回每日 clones 数组。

    每个元素形如 ``{"timestamp": "2026-08-09T00:00:00Z", "count": 10, "uniques": 5}``。
    """
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/traffic/clones?per=day"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    return data.get("clones", [])


def upsert_daily(existing: dict[str, dict], api_entries: list[dict]) -> dict[str, dict]:
    """将 API 每日数据合并进已存档的 daily（API 值优先覆盖）。"""
    merged = {date: entry for date, entry in existing.items()}
    for entry in api_entries:
        date = entry["timestamp"][:10]  # "2026-08-09T00:00:00Z" → "2026-08-09"
        merged[date] = {
            "clones": entry["count"],
            "uniques": entry["uniques"],
        }
    return dict(sorted(merged.items()))


def compute_base_offset(current_clones: int, current_uniques: int, daily: dict[str, dict]) -> dict:
    """首次迁移：计算 base_offset 使总量 = base_offset + sum(daily) = 旧总量。"""
    sum_clones = sum(d["clones"] for d in daily.values())
    sum_uniques = sum(d["uniques"] for d in daily.values())
    return {
        "clones": current_clones - sum_clones,
        "uniques": current_uniques - sum_uniques,
        "note": "补偿 07-01~07-24 丢失数据 + 按 daily 存储前已累计的差值",
    }


def main() -> None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        print("错误：环境变量 GITHUB_REPOSITORY 未设置", file=sys.stderr)
        sys.exit(1)

    # 读取现有存档
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    daily = data.get("daily", {})

    # 拉取 API 每日数据并合并
    api_entries = fetch_traffic_daily(repo)
    daily = upsert_daily(daily, api_entries)

    # 首次迁移：计算 base_offset
    base_offset = data.get("base_offset")
    if base_offset is None:
        base_offset = compute_base_offset(
            data.get("clones", 0),
            data.get("uniques", 0),
            daily,
        )

    # 重算总量
    total_clones = base_offset["clones"] + sum(d["clones"] for d in daily.values())
    total_uniques = base_offset["uniques"] + sum(d["uniques"] for d in daily.values())

    # 构建输出（新对象，不修改原 data）
    output = {
        "clones": total_clones,
        "uniques": total_uniques,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "base_offset": base_offset,
        "daily": daily,
    }

    JSON_PATH.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"已更新：{total_clones} clones / {total_uniques} uniques")
    print(f"每日明细：{len(daily)} 天")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        print(f"API 调用失败：{e.stderr}", file=sys.stderr)
        sys.exit(1)
    except (json.JSONDecodeError, KeyError) as e:
        print(f"数据解析失败：{e}", file=sys.stderr)
        sys.exit(1)
