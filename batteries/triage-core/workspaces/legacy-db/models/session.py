"""ORM models for session management."""

from sqlalchemy import Column, DateTime, Integer, String, func
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class LegacySession(Base):
    """Deprecated session table — superseded by ActiveSession.

    Still present in the database; pending removal as part of the
    legacy clean-up tracked in JIRA-1042.
    """

    __tablename__ = "legacy_sessions"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, nullable=False, index=True)
    token      = Column(String(255), nullable=False, unique=True)
    created_at = Column(DateTime, server_default=func.now())


class ActiveSession(Base):
    """Current session table."""

    __tablename__ = "sessions"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, nullable=False, index=True)
    token      = Column(String(255), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
