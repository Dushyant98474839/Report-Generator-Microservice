from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime
from database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    disabled = Column(Boolean, default=False)

class Job(Base):
    __tablename__ = "jobs"
    
    id = Column(String, primary_key=True)
    status = Column(String)  # "queued", "processing", "completed", "failed"
    filename = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    error = Column(String, nullable=True)