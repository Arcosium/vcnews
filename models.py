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
    Text, create_engine, Index, event, inspect, text,
)
from sqlalchemy.engine import Engine
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


# 알림 키워드 소스 식별자 — 탭 ID와 동일 ('vc_notices' | 'kip_news')
NOTIFICATION_SOURCES = ("vc_notices", "kip_news")


class NotificationKeyword(Base):
    """알림 키워드 목록 — 소스별로 독립 관리.

    동일 키워드가 두 소스에 동시 등록 가능 (예: '삼성' → 벤처공고/KIP 양쪽).
    그래서 unique 는 (keyword, source) 복합으로 설정한다.
    """
    __tablename__ = "notification_keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(200), nullable=False)
    source = Column(String(20), nullable=False, default="vc_notices")

    __table_args__ = (
        Index("ix_keyword_source", "keyword", "source", unique=True),
    )


# ─── DB 초기화 ──────────────────────────────────────────────

import os
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_THIS_DIR, "vcnews.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    """WAL 모드 + busy_timeout으로 크롤러 쓰기 중 API 읽기 잠금 회피."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _migrate_notification_keywords():
    """기존 단일-키워드 테이블을 (keyword, source) 스키마로 마이그레이션.

    기존 행에는 source 가 없었으므로 양쪽 소스 (vc_notices, kip_news) 로
    복제해 기존 알림 동작을 보존한다.
    """
    insp = inspect(engine)
    if "notification_keywords" not in insp.get_table_names():
        return  # 새 DB 라면 마이그레이션 불필요

    cols = [c["name"] for c in insp.get_columns("notification_keywords")]
    if "source" in cols:
        return  # 이미 마이그레이션됨

    # 기존 키워드 수집 후 테이블 재생성
    with engine.begin() as conn:
        old_rows = conn.execute(text("SELECT keyword FROM notification_keywords")).fetchall()
        conn.execute(text("DROP TABLE notification_keywords"))

    Base.metadata.tables["notification_keywords"].create(engine)

    if not old_rows:
        return

    session = SessionLocal()
    try:
        for (kw,) in old_rows:
            for src in NOTIFICATION_SOURCES:
                session.add(NotificationKeyword(keyword=kw, source=src))
        session.commit()
    finally:
        session.close()


def init_db():
    """테이블 생성 및 기본 설정 행 보장."""
    Base.metadata.create_all(engine)
    _migrate_notification_keywords()
    session = SessionLocal()
    try:
        if not session.query(Settings).first():
            session.add(Settings(id=1))
            session.commit()
    finally:
        session.close()
