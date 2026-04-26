from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from nextballup_db.models.base import Base


class CspReport(Base):
    __tablename__ = "csp_reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    document_uri: Mapped[str | None] = mapped_column(Text)
    violated_directive: Mapped[str | None] = mapped_column(String(256))
    blocked_uri: Mapped[str | None] = mapped_column(String(512))
    source_file: Mapped[str | None] = mapped_column(String(512))
    line_number: Mapped[int | None] = mapped_column(Integer)
    column_number: Mapped[int | None] = mapped_column(Integer)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    reporter_ip: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_csp_reports_received_at", "received_at"),
        Index("ix_csp_reports_user_received", "user_id", "received_at"),
    )
