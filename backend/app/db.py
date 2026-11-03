
from datetime import datetime
from pathlib import Path

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Boolean,
    DateTime,
    ForeignKey,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship


# Database file location
BASE_DIR = Path(__file__).resolve().parent.parent
DATABASE_URL = f"sqlite:///{BASE_DIR / 'vigil.db'}"


# Create database connection
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

# Create database sessions
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Base class for database models
Base = declarative_base()


# DVR table
class DVR(Base):
    __tablename__ = "dvrs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    brand = Column(String, nullable=False)
    ip = Column(String, nullable=False)
    port = Column(Integer, default=554)
    username = Column(String, nullable=False)
    password_encrypted = Column(String, nullable=False)
    channel_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    cameras = relationship(
        "Camera",
        back_populates="dvr",
        cascade="all, delete-orphan"
    )


# Camera table
class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)

    dvr_id = Column(Integer, ForeignKey("dvrs.id"), nullable=True)

    channel = Column(Integer, nullable=False, default=1)
    name = Column(String, nullable=False)
    zone = Column(String, nullable=True)

    main_url = Column(String, nullable=True)
    sub_url = Column(String, nullable=True)

    rtsp_url_encrypted = Column(String, nullable=True)

    codec = Column(String, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    fps = Column(Integer, nullable=True)

    status = Column(String, default="unknown", server_default="unknown")
    last_seen = Column(DateTime, nullable=True)
    fail_count = Column(Integer, default=0, server_default="0")

    analysis_enabled = Column(Boolean, default=False, server_default="0")

    dvr = relationship("DVR", back_populates="cameras")

    group_members = relationship(
        "CameraGroupMember",
        back_populates="camera",
        cascade="all, delete-orphan"
    )

    safety_prompts = relationship(
        "SafetyPrompt",
        back_populates="camera",
        cascade="all, delete-orphan"
    )


# Camera groups table
class CameraGroup(Base):
    __tablename__ = "camera_groups"

    id = Column(Integer, primary_key=True, index=True)
    group_code = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    members = relationship(
        "CameraGroupMember",
        back_populates="group",
        cascade="all, delete-orphan"
    )

    prompts = relationship(
        "SafetyPrompt",
        back_populates="group",
        cascade="all, delete-orphan"
    )


# Camera group membership table
class CameraGroupMember(Base):
    __tablename__ = "camera_group_members"

    id = Column(Integer, primary_key=True, index=True)

    group_id = Column(
        Integer,
        ForeignKey("camera_groups.id"),
        nullable=False
    )

    camera_id = Column(
        Integer,
        ForeignKey("cameras.id"),
        nullable=False
    )

    group = relationship("CameraGroup", back_populates="members")
    camera = relationship("Camera", back_populates="group_members")


# Safety prompts table
class SafetyPrompt(Base):
    __tablename__ = "safety_prompts"

    id = Column(Integer, primary_key=True, index=True)

    title = Column(String, nullable=False)
    text = Column(String, nullable=False)

    # universal, group, or individual
    prompt_type = Column(String, nullable=False)

    group_id = Column(
        Integer,
        ForeignKey("camera_groups.id"),
        nullable=True
    )

    camera_id = Column(
        Integer,
        ForeignKey("cameras.id"),
        nullable=True
    )

    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    group = relationship("CameraGroup", back_populates="prompts")
    camera = relationship("Camera", back_populates="safety_prompts")


# Create all database tables
def init_db():
    # Register the authentication tables with Base
    import app.auth.models  # noqa: F401

    Base.metadata.create_all(bind=engine)


# Database dependency for FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
# Camera health history table
class CameraHealthHistory(Base):
    __tablename__ = "camera_health_history"

    id = Column(Integer, primary_key=True, index=True)

    camera_id = Column(
        Integer,
        ForeignKey("cameras.id", ondelete="CASCADE"),
        nullable=False
    )

    status = Column(String, nullable=False)

    recorded_at = Column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )

    camera = relationship("Camera")