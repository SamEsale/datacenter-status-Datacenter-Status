from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    String,
    Integer,
    DateTime,
    Text,
    Boolean,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Cachet component id if known
    cachet_component_id: Mapped[Optional[int]] = mapped_column(Integer, unique=True, nullable=True)

    # Canonical key used for matching across sources
    provider_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)

    display_name: Mapped[str] = mapped_column(String(300), nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    incidents: Mapped[List["Incident"]] = relationship(back_populates="provider")

    aliases: Mapped[List["ProviderAlias"]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
    )


class ProviderAlias(Base):
    __tablename__ = "provider_aliases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), nullable=False, index=True)
    provider: Mapped["Provider"] = relationship(back_populates="aliases")

    # Match policy / evidence:
    #   - "from_address" (exact sender)
    #   - "from_domain"  (sender domain)
    #   - "subject_regex" / "body_regex" (allowed but use sparingly)
    match_type: Mapped[str] = mapped_column(String(30), nullable=False)
    match_value: Mapped[str] = mapped_column(Text, nullable=False)

    # Smaller number = higher priority
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("match_type", "match_value", name="uq_provider_aliases_match_type_value"),
    )


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    provider_id: Mapped[int] = mapped_column(ForeignKey("providers.id"), nullable=False, index=True)
    provider: Mapped["Provider"] = relationship(back_populates="incidents")

    # "cachet" | "outlook"
    source: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Cachet incident id or Outlook message id (or conversation id)
    external_id: Mapped[str] = mapped_column(String(200), nullable=False)

    # REQUIRED: persisted incident state
    # state ∈ {"active","resolved"}
    state: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    status_name: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Legacy column retained for backward compatibility only
    affected_postal_codes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # NEW: provider match audit fields (added in alembic rev 36b3bec4ec1b)
    provider_match_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    provider_match_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    provider_confidence: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # NEW: traceability for Graph ingestion (added in alembic rev 36b3bec4ec1b)
    from_address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    subject: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    locations: Mapped[List["IncidentLocation"]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_incidents_source_external_id"),
    )


class IncidentLocation(Base):
    __tablename__ = "incident_locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    incident: Mapped["Incident"] = relationship(back_populates="locations")

    postal_code: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    country: Mapped[str] = mapped_column(Text, nullable=False, default="Sweden")

    confidence: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
