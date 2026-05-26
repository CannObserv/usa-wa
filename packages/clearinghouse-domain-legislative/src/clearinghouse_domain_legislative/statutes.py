"""Statute corpus cluster — enacted law as it currently stands.

RCW is the WA instance of :class:`StatuteCode`. The model is jurisdiction-generic
so federal (USC), Oregon (ORS), and other state codes slot in cleanly.

All tables live in the ``canonical`` Postgres schema.
"""

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "canonical"


def _new_ulid() -> _ULID:
    return _ULID()


class StatuteCode(Base, TimestampMixin):
    """Top-level identifier of a statutory body — e.g., (jurisdiction='usa-wa', code='RCW')."""

    __tablename__ = "statute_codes"
    __table_args__ = (
        UniqueConstraint("jurisdiction_id", "code", name="uq_statute_codes_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)  # RCW, ORS, USC, ...
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class StatuteTitle(Base, TimestampMixin):
    """A title within a statutory code (e.g., RCW Title 46 — Motor Vehicles)."""

    __tablename__ = "statute_titles"
    __table_args__ = (
        UniqueConstraint("statute_code_id", "number", name="uq_statute_titles_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    statute_code_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.statute_codes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number: Mapped[str] = mapped_column(String(32), nullable=False)  # "46"
    heading: Mapped[str] = mapped_column(Text, nullable=False)


class StatuteChapter(Base, TimestampMixin):
    """A chapter within a statute title (e.g., RCW 46.16)."""

    __tablename__ = "statute_chapters"
    __table_args__ = (
        UniqueConstraint("statute_title_id", "number", name="uq_statute_chapters_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    statute_title_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.statute_titles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number: Mapped[str] = mapped_column(String(32), nullable=False)  # "46.16"
    heading: Mapped[str] = mapped_column(Text, nullable=False)


class StatuteSection(Base, TimestampMixin):
    """A statute section (e.g., RCW 46.16.005). ``text`` holds the current text."""

    __tablename__ = "statute_sections"
    __table_args__ = (
        UniqueConstraint("statute_chapter_id", "number", name="uq_statute_sections_natural_key"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    statute_chapter_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.statute_chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    number: Mapped[str] = mapped_column(String(64), nullable=False)  # "46.16.005"
    heading: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)


class BillStatuteChange(Base, TimestampMixin):
    """Links a bill to the statute section(s) it creates, amends, repeals, or recodifies."""

    __tablename__ = "bill_statute_changes"
    __table_args__ = (
        UniqueConstraint(
            "bill_id",
            "statute_section_id",
            "change_type",
            name="uq_bill_statute_changes_natural_key",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    jurisdiction_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    bill_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.bills.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    statute_section_id: Mapped[_ULID] = mapped_column(
        ULID(),
        ForeignKey(f"{SCHEMA}.statute_sections.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # change_type vocab: creates | amends | repeals | recodifies
