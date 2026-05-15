"""VC News Platform — SQLAlchemy 모델 정의.

다중 사용자 지원 (admin/일반 유저) + 크롤링된 뉴스는 공통 풀.

테이블:
  - User                  : 회원 (username/password_hash)
  - UserPreferences       : 유저별 알림 토글
  - NotificationKeyword   : 유저별 키워드 (소스별)
  - Scrap                 : 유저별 스크랩
  - Article               : 공용 뉴스 (nate_query 컬럼으로 어느 검색어로 들어왔는지 기록)
  - Settings              : 서버 전역 설정 (크롤링 주기만 — admin 만 변경)
"""

from __future__ import annotations

import datetime
import os
from sqlalchemy import (
    Column, Integer, String, DateTime, Boolean,
    Text, ForeignKey, create_engine, Index, UniqueConstraint,
    event, inspect, text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

KST = datetime.timezone(datetime.timedelta(hours=9))

Base = declarative_base()


# ─── 모델 ───────────────────────────────────────────────────


class User(Base):
    """사용자 계정."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), nullable=False, unique=True, index=True)
    password_hash = Column(String(255), nullable=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(KST))


class UserPreferences(Base):
    """사용자별 알림 토글."""
    __tablename__ = "user_preferences"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True)
    notifications_enabled = Column(Boolean, nullable=False, default=True)
    notify_vc_notices = Column(Boolean, nullable=False, default=True)
    notify_kip_news = Column(Boolean, nullable=False, default=True)


class Article(Base):
    """크롤링된 뉴스 기사 (전 사용자 공용)."""
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False, index=True)        # 'kvca', 'kvic', 'kip'
    source_label = Column(String(100), nullable=False, default="")
    date = Column(String(20), nullable=False)
    title = Column(Text, nullable=False)
    link = Column(Text, nullable=False)
    # Nate 검색에서 어떤 키워드로 들어왔는지 기록. KIP 외 소스는 NULL.
    # 노출 X — 내부 알림 매칭용 시그널.
    nate_query = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(KST))

    __table_args__ = (
        Index("ix_articles_link", "link", unique=True),
        Index("ix_articles_title", "title"),
    )


class Scrap(Base):
    """유저별 스크랩."""
    __tablename__ = "scraps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    article_id = Column(Integer, ForeignKey("articles.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.datetime.now(KST))

    __table_args__ = (
        UniqueConstraint("user_id", "article_id", name="uq_scrap_user_article"),
    )


class Settings(Base):
    """서버 전역 설정 (단일 행, admin 만 수정)."""
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, default=1)
    crawl_interval_minutes = Column(Integer, default=60)


# 알림 키워드 소스 ID
NOTIFICATION_SOURCES = ("vc_notices", "kip_news")


class NotificationKeyword(Base):
    """유저별 알림 키워드 — (user_id, source, keyword) 유니크."""
    __tablename__ = "notification_keywords"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    keyword = Column(String(200), nullable=False)
    source = Column(String(20), nullable=False, default="vc_notices")

    __table_args__ = (
        Index("ix_keyword_user_source", "user_id", "source", "keyword", unique=True),
    )


# ─── DB 초기화 / 마이그레이션 ───────────────────────────────


_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# 환경변수로 오버라이드 가능 — 테스트는 임시 DB 사용
DB_PATH = os.environ.get("VCNEWS_DB_PATH", os.path.join(_THIS_DIR, "vcnews.db"))
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, echo=False,
                       connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _column_exists(table: str, column: str) -> bool:
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def _migrate_add_columns():
    """기존 테이블에 새 컬럼 추가 — idempotent."""
    with engine.begin() as conn:
        if not _column_exists("articles", "nate_query"):
            conn.execute(text("ALTER TABLE articles ADD COLUMN nate_query VARCHAR(50)"))


def _migrate_legacy_per_user(admin_user_id: int):
    """기존 단일-유저 스키마의 keywords/scraps 를 admin 으로 이전.

    SQLite 에선 ALTER TABLE 로 인라인 UNIQUE 제약을 못 떼므로,
    canonical "create _new + copy + drop + rename" 패턴 사용. PRAGMA foreign_keys
    임시 해제해서 다른 테이블의 FK 가 걸려도 안전하게 swap.

    기존 데이터 처리:
      · notification_keywords: 모든 옛 키워드를 admin 에게 귀속. '국민성장펀드' 는 디폴트 해제 요구사항에 따라 제외.
      · scraps: 모든 옛 스크랩을 admin 에게 귀속.
    """
    insp = inspect(engine)

    def _rebuild_notification_keywords():
        with engine.begin() as conn:
            old_cols = [c["name"] for c in insp.get_columns("notification_keywords")]
            has_source = "source" in old_cols
            # 옛 행 추출 (각 v1/v2 스키마 모두 대응)
            select_sql = (
                "SELECT id, keyword, source FROM notification_keywords"
                if has_source else
                "SELECT id, keyword, 'vc_notices' AS source FROM notification_keywords"
            )
            rows = conn.execute(text(select_sql)).fetchall()

            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(text(
                "CREATE TABLE notification_keywords_new ("
                "  id INTEGER NOT NULL PRIMARY KEY,"
                "  user_id INTEGER NOT NULL,"
                "  keyword VARCHAR(200) NOT NULL,"
                "  source VARCHAR(20) NOT NULL,"
                "  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE"
                ")"
            ))
            for (rid, keyword, source) in rows:
                if keyword == "국민성장펀드":
                    continue  # 디폴트 해제 요구사항
                if source not in NOTIFICATION_SOURCES:
                    source = "vc_notices"
                conn.execute(text(
                    "INSERT INTO notification_keywords_new (id, user_id, keyword, source) "
                    "VALUES (:id, :uid, :kw, :src)"
                ), {"id": rid, "uid": admin_user_id, "kw": keyword, "src": source})

            conn.execute(text("DROP TABLE notification_keywords"))
            conn.execute(text("ALTER TABLE notification_keywords_new RENAME TO notification_keywords"))
            conn.execute(text(
                "CREATE UNIQUE INDEX ix_keyword_user_source "
                "ON notification_keywords (user_id, source, keyword)"
            ))
            conn.execute(text(
                "CREATE INDEX ix_notification_keywords_user_id "
                "ON notification_keywords (user_id)"
            ))
            conn.execute(text("PRAGMA foreign_keys=ON"))

    def _rebuild_scraps():
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT id, article_id, created_at FROM scraps")
            ).fetchall()

            conn.execute(text("PRAGMA foreign_keys=OFF"))
            conn.execute(text(
                "CREATE TABLE scraps_new ("
                "  id INTEGER NOT NULL PRIMARY KEY,"
                "  user_id INTEGER NOT NULL,"
                "  article_id INTEGER NOT NULL,"
                "  created_at DATETIME,"
                "  FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,"
                "  FOREIGN KEY(article_id) REFERENCES articles(id) ON DELETE CASCADE"
                ")"
            ))
            for (rid, aid, ts) in rows:
                conn.execute(text(
                    "INSERT INTO scraps_new (id, user_id, article_id, created_at) "
                    "VALUES (:id, :uid, :aid, :ts)"
                ), {"id": rid, "uid": admin_user_id, "aid": aid, "ts": ts})

            conn.execute(text("DROP TABLE scraps"))
            conn.execute(text("ALTER TABLE scraps_new RENAME TO scraps"))
            conn.execute(text(
                "CREATE UNIQUE INDEX uq_scrap_user_article "
                "ON scraps (user_id, article_id)"
            ))
            conn.execute(text("CREATE INDEX ix_scraps_user_id ON scraps (user_id)"))
            conn.execute(text("CREATE INDEX ix_scraps_article_id ON scraps (article_id)"))
            conn.execute(text("PRAGMA foreign_keys=ON"))

    # ── 실행 분기 ─────────────────────────────────────────
    if "notification_keywords" in insp.get_table_names():
        kw_cols = [c["name"] for c in insp.get_columns("notification_keywords")]
        if "user_id" not in kw_cols:
            _rebuild_notification_keywords()

    if "scraps" in insp.get_table_names():
        scrap_cols = [c["name"] for c in insp.get_columns("scraps")]
        if "user_id" not in scrap_cols:
            _rebuild_scraps()


def _ensure_settings_row():
    session = SessionLocal()
    try:
        if not session.query(Settings).first():
            session.add(Settings(id=1, crawl_interval_minutes=60))
            session.commit()
    finally:
        session.close()


def init_db():
    """테이블 생성 + 마이그레이션 + 기본 설정 보장."""
    # 새 컬럼은 ORM `create_all` 이 안 추가하므로 먼저 ALTER.
    insp_pre = inspect(engine)
    if "articles" in insp_pre.get_table_names():
        _migrate_add_columns()

    Base.metadata.create_all(engine)
    _ensure_settings_row()


def ensure_admin_user(username: str, password_hash: str) -> int:
    """admin 계정 보장 + 기존 익명 데이터 이전. 반환: admin user_id.

    init_db() 후 호출.
    """
    session = SessionLocal()
    try:
        admin = session.query(User).filter(User.username == username).first()
        if not admin:
            admin = User(username=username, password_hash=password_hash, is_admin=True)
            session.add(admin)
            session.commit()
            session.refresh(admin)
            # 기본 환경설정 행
            session.add(UserPreferences(user_id=admin.id))
            session.commit()
        admin_id = admin.id
    finally:
        session.close()

    # 레거시 데이터 이전 (이미 user_id 컬럼이 있으면 no-op)
    _migrate_legacy_per_user(admin_id)
    return admin_id
