"""用户与角色模型。角色以字符串存储，避免枚举迁移复杂度。"""
from datetime import datetime

from sqlalchemy import DateTime, func, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # role: admin（管理员）/ customer（普通顾客）
    role: Mapped[str] = mapped_column(String(20), default="customer", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
