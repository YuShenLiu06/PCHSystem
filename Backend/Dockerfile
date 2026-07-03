FROM python:3.11-slim

# 系统包：
# - git：归档 publisher 把 projects/<id>/ 提交推送到 wiki 内容 git 仓（Phase B）。
# - fontconfig：matplotlib 注册字体用（fc-cache）。
# - wget：拉单一 CJK 字体（不装 fonts-noto-cjk 全包 ~300MB）。
RUN apt-get update && apt-get install -y --no-install-recommends \
        git wget fontconfig ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 单一 CJK 字体：Noto Sans CJK SC Regular（~16MB OTF）。
# matplotlib 归档饼图经 rc_context 指定 "Noto Sans CJK SC" 解析此字体。
# URL 失效会在 build 时 wget 报错（早暴露）；运行时 chart.py 仍会回退字体不崩。
# 如需更换镜像源，改下述 URL 即可（host-agnostic）。
RUN mkdir -p /usr/share/fonts/truetype/noto-cjk \
    && wget -q -O /usr/share/fonts/truetype/noto-cjk/NotoSansCJKsc-Regular.otf \
        "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf" \
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
