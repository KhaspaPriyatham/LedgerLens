import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, Column, String, Float, DateTime, Text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import DATABASE_URL

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=new_id)
    filename = Column(String, nullable=False)
    status = Column(String, nullable=False, default="processing")
    # "processing" | "blocked" | "auto_approved" | "pending_review" | "approved"

    vendor = Column(String, nullable=True)
    total = Column(Float, nullable=True)
    currency = Column(String, nullable=True)

    moderation_verdict = Column(String, nullable=True)
    moderation_max_score = Column(Float, nullable=True)

    extracted_json = Column(Text, nullable=True)   # raw model output (InvoiceSchema.json())
    reviewed_json = Column(Text, nullable=True)     # post-reviewer-correction final record

    cost_usd = Column(Float, default=0.0)
    image_path = Column(String, nullable=True)      # watermarked image path

    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ReviewQueueItem(Base):
    __tablename__ = "review_queue"

    id = Column(String, primary_key=True, default=new_id)
    document_id = Column(String, nullable=False)
    field_path = Column(String, nullable=False)
    original_confidence = Column(Float, nullable=False)
    status = Column(String, default="pending")  # "pending" | "resolved"
    created_at = Column(DateTime, default=utcnow)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
