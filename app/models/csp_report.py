import datetime as dt

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.models.base import AppBase


class CspReport(AppBase):
    """Daily aggregate of CSP / Trusted Types violations; no IP, cookie or User-Agent is kept."""

    __tablename__ = "csp_reports"
    __table_args__ = (
        UniqueConstraint(
            "day",
            "env",
            "route",
            "directive",
            "disposition",
            "sink",
            "blocked",
            "policy_name",
            name="uq_csp_reports_key",
        ),
    )

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    day: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    env: Mapped[str] = mapped_column(String(8), nullable=False)
    route: Mapped[str] = mapped_column(String(120), nullable=False)
    directive: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    # Key columns are NOT NULL ('' when absent) so the unique constraint also dedupes the "absent" case.
    sink: Mapped[str] = mapped_column(String(48), nullable=False, server_default="")
    blocked: Mapped[str] = mapped_column(String(128), nullable=False, server_default="")
    policy_name: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    sample: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
