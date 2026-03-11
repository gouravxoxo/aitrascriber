import os
import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, Float, DateTime, Boolean,
    ForeignKey, Text, create_engine
)
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./voiceiq.db")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


# ─── MODELS ───────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"
    id            = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username      = Column(String, unique=True, nullable=False)
    email         = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow)
    is_active     = Column(Boolean, default=True)
    calls         = relationship("Call", back_populates="user")


class Call(Base):
    __tablename__ = "calls"
    id             = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id        = Column(String, ForeignKey("users.id"), nullable=False)
    filename       = Column(String, nullable=False)
    publisher_id   = Column(String, nullable=True)   # optional, user-provided
    caller_id      = Column(String, nullable=True)   # optional, user-provided
    call_date      = Column(DateTime, nullable=True) # parsed from filename
    duration_sec   = Column(Float, nullable=True)
    status         = Column(String, default="pending")  # pending|processing|done|error
    agent_channel  = Column(Integer, nullable=True)     # 0 or 1
    audio_path     = Column(String, nullable=True)      # original file path
    ch0_path       = Column(String, nullable=True)      # split channel 0
    ch1_path       = Column(String, nullable=True)      # split channel 1
    error_msg      = Column(Text, nullable=True)
    created_at     = Column(DateTime, default=datetime.utcnow)
    user           = relationship("User", back_populates="calls")
    segments       = relationship("Segment", back_populates="call", cascade="all, delete-orphan")
    job            = relationship("Job", back_populates="call", uselist=False, cascade="all, delete-orphan")


class Segment(Base):
    __tablename__ = "segments"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    call_id    = Column(String, ForeignKey("calls.id"), nullable=False)
    channel    = Column(Integer)           # 0 or 1
    role       = Column(String)            # 'agent' or 'caller'
    start_sec  = Column(Float)
    end_sec    = Column(Float)
    text       = Column(Text)
    word_count = Column(Integer)
    seq        = Column(Integer)           # ordering index after merge
    call       = relationship("Call", back_populates="segments")


class Job(Base):
    __tablename__ = "jobs"
    id          = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    call_id     = Column(String, ForeignKey("calls.id"), nullable=False)
    status      = Column(String, default="queued")   # queued|splitting|transcribing_agent|transcribing_caller|merging|detecting_roles|done|error
    progress    = Column(Integer, default=0)          # 0-100
    step_msg    = Column(String, default="Queued")
    created_at  = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    call        = relationship("Call", back_populates="job")


# ─── INIT ─────────────────────────────────────────────────────────────────────

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
