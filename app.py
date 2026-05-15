"""VC News Platform — FastAPI 웹앱 서버 (다중 사용자).

기능:
  - 인증: /api/auth/signup, /api/auth/login, /api/auth/logout, /api/auth/me
  - 기사: /api/articles (공용), /api/articles/new (유저별 알림 필터)
  - 유저별: 스크랩, 알림 키워드, 알림 토글
  - 서버 전역: 크롤링 주기 (admin 만 변경)
  - 백그라운드 크롤링 (APScheduler)
"""

from __future__ import annotations

import datetime
import logging
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, func, or_

from apscheduler.schedulers.background import BackgroundScheduler

from .auth import (
    JWT_COOKIE_NAME, JWT_EXPIRE_DAYS,
    get_current_user, get_optional_user, hash_password, issue_token,
    verify_password,
)
from .models import (
    NOTIFICATION_SOURCES, Article, NotificationKeyword, Scrap,
    SessionLocal, Settings, User, UserPreferences,
    ensure_admin_user, init_db,
)
from .news_crawler import run_news_crawl

RETENTION_DAYS = 30

import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(_THIS_DIR, "static")
KST = datetime.timezone(datetime.timedelta(hours=9))

# 키워드 — 한국투자파트너스 라는 문자열로 알림 키워드를 등록하면
# nate_query 가 같은 기사도 알림 매칭에 포함 (제목 검색만이 아니라).
# 다른 키워드는 해당 시그널이 없음. UI/스키마에는 노출 X.
_KIP_QUERY_HIDDEN_KEYWORD = "한국투자파트너스"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vcnews")

# ─── 스케줄러 ──────────────────────────────────────────────

scheduler = BackgroundScheduler(timezone="Asia/Seoul")


def _scheduled_crawl():
    logger.info("⏰ 스케줄된 크롤링 시작")
    try:
        result = run_news_crawl()
        logger.info(f"크롤링 완료:\n{result}")
    except Exception as e:
        logger.error(f"크롤링 오류: {e}")


def cleanup_old_articles(days: int = RETENTION_DAYS) -> int:
    """오래된 기사 정리 — 스크랩된 기사는 보존."""
    cutoff = (datetime.datetime.now(KST) - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    session = SessionLocal()
    try:
        scrapped_subq = session.query(Scrap.article_id).distinct().subquery()
        deleted = (
            session.query(Article)
            .filter(Article.date < cutoff)
            .filter(~Article.id.in_(scrapped_subq.select()))
            .delete(synchronize_session=False)
        )
        session.commit()
        logger.info(f"🧹 오래된 기사 {deleted}건 삭제 (cutoff={cutoff})")
        return deleted
    except Exception as e:
        session.rollback()
        logger.error(f"기사 정리 오류: {e}")
        return 0
    finally:
        session.close()


def _scheduled_cleanup():
    cleanup_old_articles()


def _update_scheduler_interval():
    session = SessionLocal()
    try:
        settings = session.query(Settings).first()
        interval = settings.crawl_interval_minutes if settings else 60
    finally:
        session.close()

    if scheduler.get_job("crawl_job"):
        scheduler.remove_job("crawl_job")

    scheduler.add_job(
        _scheduled_crawl, "interval", minutes=interval,
        id="crawl_job", replace_existing=True,
    )
    logger.info(f"📅 크롤링 스케줄: {interval}분 간격")


# ─── FastAPI 앱 ────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    # 테스트 환경 (VCNEWS_DISABLE_SCHEDULER=1) 에선 백그라운드 작업 비활성화
    if os.environ.get("VCNEWS_DISABLE_SCHEDULER") != "1":
        def background_crawl():
            try:
                run_news_crawl()
            except Exception as e:
                logger.error(f"초기 크롤링 오류: {e}")
        threading.Thread(target=background_crawl, daemon=True).start()

        try:
            cleanup_old_articles()
        except Exception as e:
            logger.error(f"시작 시 정리 오류: {e}")

        _update_scheduler_interval()
        scheduler.add_job(
            _scheduled_cleanup, "cron", hour=3, minute=30,
            id="cleanup_job", replace_existing=True,
        )
        scheduler.start()

    yield

    if scheduler.running:
        scheduler.shutdown()


app = FastAPI(title="VC News Platform", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic 스키마 ───────────────────────────────────────


class SignupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=4, max_length=128)
    remember: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str
    remember: bool = False


class SettingsUpdate(BaseModel):
    # 서버 전역 (admin)
    crawl_interval_minutes: Optional[int] = None
    # 유저별
    notifications_enabled: Optional[bool] = None
    notify_vc_notices: Optional[bool] = None
    notify_kip_news: Optional[bool] = None


class KeywordRequest(BaseModel):
    keyword: str
    source: str


# ─── 인증 엔드포인트 ───────────────────────────────────────

def _set_session_cookie(response: Response, token: str, remember: bool = False):
    """remember=True 면 30일 영속 쿠키, False 면 세션 쿠키(브라우저/앱 종료 시 소멸).

    세션 쿠키: max_age/expires 둘 다 안 보내면 브라우저가 세션 종료 시 폐기.
    영속 쿠키: max_age 로 만료 명시 → 디바이스 재부팅 후에도 유지.
    """
    kwargs = dict(
        key=JWT_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )
    if remember:
        kwargs["max_age"] = JWT_EXPIRE_DAYS * 24 * 3600
    response.set_cookie(**kwargs)


@app.post("/api/auth/signup")
def signup(data: SignupRequest, response: Response):
    username = data.username.strip().lower()
    if len(username) < 3:
        raise HTTPException(400, "아이디는 3자 이상이어야 합니다")
    if not username.replace("_", "").isalnum():
        raise HTTPException(400, "아이디는 영문/숫자/_ 만 사용 가능합니다")

    session = SessionLocal()
    try:
        if session.query(User).filter(User.username == username).first():
            raise HTTPException(409, "이미 사용 중인 아이디입니다")
        user = User(
            username=username,
            password_hash=hash_password(data.password),
            is_admin=False,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(UserPreferences(user_id=user.id))
        session.commit()

        token = issue_token(user.id, user.username)
        _set_session_cookie(response, token, remember=data.remember)
        return {"id": user.id, "username": user.username, "is_admin": user.is_admin}
    finally:
        session.close()


@app.post("/api/auth/login")
def login(data: LoginRequest, response: Response):
    username = data.username.strip().lower()
    session = SessionLocal()
    try:
        user = session.query(User).filter(User.username == username).first()
        if not user or not verify_password(data.password, user.password_hash):
            raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다")
        # 환경설정 행 보장 (레거시 admin 대비)
        if not session.query(UserPreferences).filter(
            UserPreferences.user_id == user.id
        ).first():
            session.add(UserPreferences(user_id=user.id))
            session.commit()

        token = issue_token(user.id, user.username)
        _set_session_cookie(response, token, remember=data.remember)
        return {"id": user.id, "username": user.username, "is_admin": user.is_admin}
    finally:
        session.close()


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(JWT_COOKIE_NAME, path="/")
    return {"message": "로그아웃 완료"}


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin}


# ─── 헬스체크 (인증 불필요) ────────────────────────────────

@app.get("/api/health")
def health():
    return {"status": "ok"}


# ─── 기사 목록 (인증 필요) ─────────────────────────────────


def _scrapped_ids_for_user(session, user_id: int, article_ids: list[int]) -> set[int]:
    if not article_ids:
        return set()
    return {
        row[0] for row in session.query(Scrap.article_id)
        .filter(Scrap.user_id == user_id)
        .filter(Scrap.article_id.in_(article_ids))
        .all()
    }


@app.get("/api/articles")
def get_articles(
    tab: str = Query("vc_notices"),
    search: str = Query(""),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
):
    session = SessionLocal()
    try:
        if tab == "vc_notices":
            source_keys = ["kvca", "kvic"]
        elif tab == "kip_news":
            source_keys = ["kip"]
        else:
            raise HTTPException(400, "유효하지 않은 탭")

        q = session.query(Article).filter(Article.source.in_(source_keys))
        if search.strip():
            q = q.filter(Article.title.contains(search.strip()))

        total = q.count()
        articles = (
            q.order_by(desc(Article.date), desc(Article.id))
            .offset((page - 1) * size).limit(size).all()
        )

        scrapped_ids = _scrapped_ids_for_user(
            session, user.id, [a.id for a in articles],
        )

        return {
            "total": total, "page": page, "size": size,
            "articles": [{
                "id": a.id, "source": a.source, "source_label": a.source_label,
                "date": a.date, "title": a.title, "link": a.link,
                "is_scrapped": a.id in scrapped_ids,
            } for a in articles],
        }
    finally:
        session.close()


@app.get("/api/articles/new")
def get_new_articles(
    since_id: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
):
    """유저별 알림 필터 적용 — 마지막으로 본 id 이후의 신규 기사.

    내부 규칙 (응답에는 노출 안 됨):
      kip_news 키워드에 '한국투자파트너스' 가 포함되어 있으면,
      해당 키워드로 크롤링된 모든 기사(article.nate_query == '한국투자파트너스')도 알림 매칭.
    """
    session = SessionLocal()
    try:
        latest_id = session.query(func.max(Article.id)).scalar() or 0

        prefs = session.query(UserPreferences).filter(
            UserPreferences.user_id == user.id
        ).first()
        if not prefs or not prefs.notifications_enabled:
            return {"articles": [], "latest_id": latest_id}

        vc_keywords: list[str] = []
        kip_keywords: list[str] = []
        for kw in session.query(NotificationKeyword).filter(
            NotificationKeyword.user_id == user.id
        ).all():
            if kw.source == "vc_notices":
                vc_keywords.append(kw.keyword)
            elif kw.source == "kip_news":
                kip_keywords.append(kw.keyword)

        clauses = []

        if prefs.notify_vc_notices:
            vc_clause = Article.source.in_(["kvca", "kvic"])
            if vc_keywords:
                vc_clause = and_(
                    vc_clause,
                    or_(*[Article.title.contains(k) for k in vc_keywords]),
                )
            clauses.append(vc_clause)

        if prefs.notify_kip_news:
            kip_clause = Article.source == "kip"
            if kip_keywords:
                title_or = or_(*[Article.title.contains(k) for k in kip_keywords])
                # 비공개 규칙: '한국투자파트너스' 키워드 → nate_query 기반 매칭 추가
                if _KIP_QUERY_HIDDEN_KEYWORD in kip_keywords:
                    title_or = or_(
                        title_or,
                        Article.nate_query == _KIP_QUERY_HIDDEN_KEYWORD,
                    )
                kip_clause = and_(kip_clause, title_or)
            clauses.append(kip_clause)

        if not clauses:
            return {"articles": [], "latest_id": latest_id}

        articles = (
            session.query(Article)
            .filter(Article.id > since_id)
            .filter(or_(*clauses))
            .order_by(desc(Article.id))
            .limit(limit)
            .all()
        )

        return {
            "latest_id": latest_id,
            "articles": [{
                "id": a.id, "source": a.source, "source_label": a.source_label,
                "date": a.date, "title": a.title, "link": a.link,
            } for a in articles],
        }
    finally:
        session.close()


# ─── 스크랩 ────────────────────────────────────────────────


@app.get("/api/scraps")
def get_scraps(
    search: str = Query(""),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
):
    session = SessionLocal()
    try:
        q = (
            session.query(Article)
            .join(Scrap, Article.id == Scrap.article_id)
            .filter(Scrap.user_id == user.id)
        )
        if search.strip():
            q = q.filter(Article.title.contains(search.strip()))

        total = q.count()
        articles = (
            q.order_by(desc(Scrap.created_at))
            .offset((page - 1) * size).limit(size).all()
        )

        return {
            "total": total, "page": page, "size": size,
            "articles": [{
                "id": a.id, "source": a.source, "source_label": a.source_label,
                "date": a.date, "title": a.title, "link": a.link,
                "is_scrapped": True,
            } for a in articles],
        }
    finally:
        session.close()


@app.post("/api/scraps/{article_id}")
def toggle_scrap(article_id: int, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        article = session.query(Article).get(article_id)
        if not article:
            raise HTTPException(404, "기사를 찾을 수 없습니다")

        existing = session.query(Scrap).filter(
            Scrap.user_id == user.id,
            Scrap.article_id == article_id,
        ).first()
        if existing:
            session.delete(existing)
            session.commit()
            return {"scrapped": False, "message": "스크랩 해제"}
        else:
            session.add(Scrap(user_id=user.id, article_id=article_id))
            session.commit()
            return {"scrapped": True, "message": "스크랩 완료"}
    finally:
        session.close()


# ─── 설정 (유저별 + 서버) ──────────────────────────────────


@app.get("/api/settings")
def get_settings(user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        prefs = session.query(UserPreferences).filter(
            UserPreferences.user_id == user.id
        ).first()
        if not prefs:
            prefs = UserPreferences(user_id=user.id)
            session.add(prefs)
            session.commit()

        server_settings = session.query(Settings).first()

        keywords: dict[str, list[str]] = {src: [] for src in NOTIFICATION_SOURCES}
        for kw in session.query(NotificationKeyword).filter(
            NotificationKeyword.user_id == user.id
        ).order_by(NotificationKeyword.id).all():
            if kw.source in keywords:
                keywords[kw.source].append(kw.keyword)

        return {
            "crawl_interval_minutes": server_settings.crawl_interval_minutes,
            "notifications_enabled": prefs.notifications_enabled,
            "notify_vc_notices": prefs.notify_vc_notices,
            "notify_kip_news": prefs.notify_kip_news,
            "keywords": keywords,
            "is_admin": user.is_admin,
        }
    finally:
        session.close()


@app.put("/api/settings")
def update_settings(data: SettingsUpdate, user: User = Depends(get_current_user)):
    session = SessionLocal()
    try:
        prefs = session.query(UserPreferences).filter(
            UserPreferences.user_id == user.id
        ).first()
        if not prefs:
            prefs = UserPreferences(user_id=user.id)
            session.add(prefs)
            session.flush()

        if data.notifications_enabled is not None:
            prefs.notifications_enabled = data.notifications_enabled
        if data.notify_vc_notices is not None:
            prefs.notify_vc_notices = data.notify_vc_notices
        if data.notify_kip_news is not None:
            prefs.notify_kip_news = data.notify_kip_news

        # 크롤링 주기 변경은 admin 만
        if data.crawl_interval_minutes is not None:
            if not user.is_admin:
                raise HTTPException(403, "크롤링 주기는 관리자만 변경할 수 있습니다")
            server_settings = session.query(Settings).first()
            server_settings.crawl_interval_minutes = max(30, data.crawl_interval_minutes)

        session.commit()

        if data.crawl_interval_minutes is not None and scheduler.running:
            _update_scheduler_interval()

        return {"message": "설정 저장 완료"}
    finally:
        session.close()


# ─── 키워드 (유저별) ───────────────────────────────────────


@app.post("/api/keywords")
def add_keyword(data: KeywordRequest, user: User = Depends(get_current_user)):
    if data.source not in NOTIFICATION_SOURCES:
        raise HTTPException(400, f"유효하지 않은 source: {data.source}")
    kw = data.keyword.strip()
    if not kw:
        raise HTTPException(400, "키워드를 입력하세요")

    session = SessionLocal()
    try:
        existing = session.query(NotificationKeyword).filter(
            NotificationKeyword.user_id == user.id,
            NotificationKeyword.keyword == kw,
            NotificationKeyword.source == data.source,
        ).first()
        if existing:
            raise HTTPException(409, "이미 등록된 키워드입니다")
        session.add(NotificationKeyword(
            user_id=user.id, keyword=kw, source=data.source,
        ))
        session.commit()
        return {"message": f"키워드 '{kw}' 추가 완료", "source": data.source}
    finally:
        session.close()


@app.delete("/api/keywords/{source}/{keyword}")
def delete_keyword(source: str, keyword: str, user: User = Depends(get_current_user)):
    if source not in NOTIFICATION_SOURCES:
        raise HTTPException(400, f"유효하지 않은 source: {source}")
    session = SessionLocal()
    try:
        kw = session.query(NotificationKeyword).filter(
            NotificationKeyword.user_id == user.id,
            NotificationKeyword.keyword == keyword,
            NotificationKeyword.source == source,
        ).first()
        if not kw:
            raise HTTPException(404, "키워드를 찾을 수 없습니다")
        session.delete(kw)
        session.commit()
        return {"message": f"키워드 '{keyword}' 삭제 완료", "source": source}
    finally:
        session.close()


# ─── 수동 크롤링 (admin) ──────────────────────────────────


@app.post("/api/crawl")
def trigger_crawl(user: User = Depends(get_current_user)):
    if not user.is_admin:
        raise HTTPException(403, "관리자만 수동 크롤링이 가능합니다")
    try:
        result = run_news_crawl()
        return {"message": "크롤링 완료", "log": result}
    except Exception as e:
        raise HTTPException(500, f"크롤링 오류: {e}")


# ─── 정적 파일 & SPA 폴백 ──────────────────────────────────

if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def serve_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"message": "VC News Platform API", "docs": "/docs"})


@app.get("/favicon.ico")
def favicon():
    fav_path = os.path.join(STATIC_DIR, "favicon.ico")
    if os.path.exists(fav_path):
        return FileResponse(fav_path)
    return JSONResponse(status_code=204, content=None)
