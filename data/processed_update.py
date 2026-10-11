from datetime import datetime

from bafser import SqlAlchemyBase
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column

from data import Tables


class ProcessedUpdate(SqlAlchemyBase):
    __tablename__ = Tables.ProcessedUpdate

    update_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    processed_at: Mapped[datetime] = mapped_column(index=True)
    deliveries: Mapped[list[dict] | None] = mapped_column(JSON(none_as_null=True), default=None)
