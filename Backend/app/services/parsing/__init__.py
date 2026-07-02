"""投影材料解析子服务。

把上传的投影文件（.litematic / 未来 .schem / .nbt ...）解析成材料清单，
翻译成中文，供前端预览 → 生成在线表格（sheets）。

设计：
- ``MaterialParser``（ABC）：文件字节 → 分组材料清单（纯 registry id + 数量），不做翻译。
- ``ItemTranslator``（ABC）：registry id → 中文显示名。
- 两者解耦组合（换格式加 parser 子类；换数据源加 translator 子类）。

详见 ``Docs/architecture/api/parsing.md``。
"""
