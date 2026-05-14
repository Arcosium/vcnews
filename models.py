"""VC News Platform — SQLAlchemy 모델 정의.

Google Sheets를 대체하는 SQLite 기반 데이터 모델:
- Article: 크롤링된 뉴스 기사
- Scrap: 사용자 스크랩 기사
- Settings: 앱 설정 (크롤링 주기, 알림 등)
- NotificationKeyword: 알림 키워드
"""

from __future__ import annotations

import datetime
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean, Float,
    Text, create_engine, Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker

KST = datetime.timezone(datetime.timedelta(hours=9))

Base = declarative_base()


class Article(Base):
    """크롤링된 뉴스 기사."""
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False, index=True)       # 'kvca', 'kvic', 'kip'
    source_label = Column(String(100), nullable=False, default="")  # 표시용: '벤처협회공고', 'KIP News'
    date = Column(String(20), nullable=False)
    title = Column(Text, nullable=False)
    link = Column(Text, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(KST))

    __table_args__ = (
        Index("ix_articles_link", "link", unique=True),
        Index("ix_articles_title", "title"),
    )


class Scrap(Base):
    """사용자 스크랩 기사."""
    __tablename__ = "scraps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    article_id = Column(Integer, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(KST))


class Settings(Base):
    """앱 전역 설정 (단일 행)."""
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, default=1)
    crawl_interval_minutes = Column(Integer, default=60)      # 기본 1시간
    notifications_enabled = Column(Boolean, default=True)      # 전체 알림 마스터 토글
    notify_vc_notices = Column(Boolean, default=True)          # 벤처협회공고 알림
    notify_kip_news = Column(Boolean, default=True)            # 한투파 뉴스 알림


class NotificationKeyword(Base):
    """알림 키워드 목록."""
    __tablename__ = "notification_keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(200), nullable=False, unique=True)


# ─── DB 초기화 ──────────────────────────────────────────────

import os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_THIS_DIR, "vcnews.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


def init_db():
    """테이블 생성 및 기본 설정 행 보장."""
    Base.metadata.create_all(engine)
    session = SessionLocal()
    try:
        if not session.query(Settings).first():
            session.add(Settings(id=1))
            session.commit()
    finally:
        session.close()
