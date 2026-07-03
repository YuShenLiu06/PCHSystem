FROM python:3.11-slim

# 系统包：
# - git：归档 publisher 把 projects/<id>/ 提交推送到 wiki 内容 git 仓（Phase B）。
# - fontconfig：matplotlib 注册字体用（fc-cache）。
# - wget：拉单一 CJK 字体（不装 fonts-noto-cjk 全包 ~300MB）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        git wget fontconfig ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# CJK 字体（matplotlib 归档饼图中文）：三段 best-effort，任一成功即止；全失败也不挂构建
# （图表仍生成，仅中文豆腐）。优先 wget 单一 Noto Sans CJK SC（~16MB，小）；GitHub 不可达
# 时回退 apt fonts-noto-cjk（大但走 Debian 镜像）；再败则跳过。
RUN mkdir -p /usr/share/fonts/truetype/noto-cjk \
    && ( wget -q -T 20 -O /usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf \
            "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf" \
        || ( echo "Noto wget failed; trying apt fonts-noto-cjk" \
             && apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
             && rm -rf /var/lib/apt/lists/* ) \
        || echo "All CJK font sources failed; charts will render with fallback font" ) \
    && fc-cache -f

WORKDIR /app
ENV PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
