"""方块归一化配置（R-6：剥离 BlockState properties 在解析层用 ``BlockState.id`` 天然满足，
此处只管「跳过/合并」规则）。

v1 仅维护 ``SKIP_BLOCKS``（空 / 流体 / 结构空）。多部分方块（门 / 床 / 双层台阶会被
计为多块）留 ``MERGE_RULES`` 扩展点，当前不实现 —— 记为已知近似
（见 ``Docs/architecture/api/parsing.md`` §限制）。
"""

# 不计入材料清单的「空」方块（air 变体 / 水 / 岩浆 / 结构空）。
SKIP_BLOCKS: frozenset[str] = frozenset(
    {
        "minecraft:air",
        "minecraft:cave_air",
        "minecraft:void_air",
        "minecraft:water",
        "minecraft:lava",
        "minecraft:structure_void",
    }
)

# 预留：多部分方块合并（如门/床两面共享 id 被计为 2）。
# 形如 {"minecraft:oak_door": 2} 表示该 id 计数 // 2。v1 为空 = 不合并。
MERGE_RULES: dict[str, int] = {}
