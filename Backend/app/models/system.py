"""运行时设置 ORM（system schema）。

对应迁移 ``0017_construction``。key-value JSONB 存储，``construction.*`` 等
运行时开关落此表；DB 无值时应用层回退 ``app.core.config.Settings`` 默认。
"""
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class SystemSetting(Base):
    """系统设置（system.settings，key→JSONB value）。"""

    __tablename__ = "settings"
    __table_args__ = {"schema": "system"}

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
