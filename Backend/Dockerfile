FROM python:3.11-slim

# 系统包：
# - git：归档 publisher 把 projects/<id>/ 提交推送到 wiki 内容 git 仓（Phase B）。
# - fontconfig：matplotlib 注册字体用（fc-cache）。
# - wget：拉单一 CJK 字体（不装 fonts-noto-cjk 全包 ~300MB）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        git wget fontconfig ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# CJK 字体（matplotlib 归档饼图中文）：多源 best-effort，任一成功即止；全失败也不挂构建
# （图表仍生成，仅中文豆腐）。优先小体积单一 Noto Sans CJK SC（~16MB），依次试：
#   1) ghfast.top（GitHub 代理，对齐脚本层 PCH_GH_MIRRORS 首选；国内快）
#   2) ghproxy.com（备用代理）
#   3) GitHub 直连
#   4) apt fonts-noto-cjk（~300MB 兜底，走 Debian 镜像）
# 注：脚本层 PCH_GH_MIRRORS 仅经 git insteadOf 重写 git clone/fetch，【不覆盖】本 wget；
#     故在此显式列 GitHub 代理源。HTTPS_PROXY 经 compose_build 透传时 wget 亦自动用。
RUN mkdir -p /usr/share/fonts/truetype/noto-cjk \
    && FONT=/usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf \
    && RAW=https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf \
    && ( wget -q -T 20 -O "$FONT" "https://ghfast.top/${RAW}" \
        || wget -q -T 20 -O "$FONT" "https://ghproxy.com/${RAW}" \
        || wget -q -T 20 -O "$FONT" "$RAW" \
        || ( echo "Noto wget all failed; trying apt fonts-noto-cjk" \
             && apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
             && rm -rf /var/lib/apt/lists/* ) \
        || echo "All CJK font sources failed; charts will render with fallback font" ) \
    && fc-cache -f

WORKDIR /app
ENV PYTHONUNBUFFERED=1
# pip 镜像：install.sh/setup_mirrors 经 compose_build 透传 PIP_INDEX_URL（清华源），国内加速
# （Docker 仅对 HTTP(S)_PROXY 自动注入；PIP_INDEX_URL 须显式 ARG + 消费）
ARG PIP_INDEX_URL=""
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN if [ -n "$PIP_INDEX_URL" ]; then pip config set global.index-url "$PIP_INDEX_URL"; fi \
 && pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
