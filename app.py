"""VC News Platform — FastAPI 웹앱 서버.

기능:
  - RESTful API: 기사 목록 조회, 스크랩 관리, 설정 관리
  - 백그라운드 크롤링 스케줄러 (APScheduler)
  - 프론트엔드 정적 파일 서빙

사용법:
  uvicorn app:app --host 0.0.0.0 --port 8585 --reload
"""

from __future__ import annotations

import datetime
import threading
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, or_

from apscheduler.schedulers.background import BackgroundScheduler

from .models import (
    SessionLocal, init_db, Article, Scrap, Settings, NotificationKeyword,
)
from .news_crawler import run_news_crawl

import os

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(_THIS_DIR, "static")
KST = datetime.timezone(datetime.timedelta(hours=9))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vcnews")

# ─── 스케줄러 ──────────────────────────────────────────────

scheduler = BackgroundScheduler(timezone="Asia/Seoul")


def _scheduled_crawl():
    """백그라운드 크롤링 작업."""
    logger.info("⏰ 스케줄된 크롤링 시작")
    try:
        result = run_news_crawl()
        logger.info(f"크롤링 완료:\n{result}")
    except Exception as e:
        logger.error(f"크롤링 오류: {e}")


def _update_scheduler_interval():
    """DB 설정에서 크롤링 주기를 읽어 스케줄러 업데이트."""
    session = SessionLocal()
    try:
        settings = session.query(Settings).first()
        interval = settings.crawl_interval_minutes if settings else 60
    finally:
        session.close()

    # 기존 작업 제거 후 재등록
    if scheduler.get_job("crawl_job"):
        scheduler.remove_job("crawl_job")

    scheduler.add_job(
        _scheduled_crawl,
        "interval",
        minutes=interval,
        id="crawl_job",
        replace_existing=True,
    )
    logger.info(f"📅 크롤링 스케줄: {interval}분 간격")


# ─── FastAPI 앱 ────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 시작/종료 시 실행."""
    # 시작 시 DB 초기화
    init_db()
    
    # 즉시 크롤링을 백그라운드 스레드에서 실행하여 서버 구동 차단 방지
    def background_crawl():
        try:
            run_news_crawl()
        except Exception as e:
            logger.error(f"초기 크롤링 오류: {e}")
            
    threading.Thread(target=background_crawl, daemon=True).start()

    # 스케줄러 설정
    _update_scheduler_interval()
    scheduler.start()
    
    yield
    scheduler.shutdown()


app = FastAPI(title="VC News Platform", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Pydantic 스키마 ───────────────────────────────────────


class ArticleResponse(BaseModel):
    id: int
    source: str
    source_label: str
    date: str
    title: str
    link: str
    is_scrapped: bool = False

    class Config:
        from_attributes = True


class SettingsUpdate(BaseModel):
    crawl_interval_minutes: Optional[int] = None
    notifications_enabled: Optional[bool] = None
    notify_vc_notices: Optional[bool] = None
    notify_kip_news: Optional[bool] = None


class SettingsResponse(BaseModel):
    crawl_interval_minutes: int
    notifications_enabled: bool
    notify_vc_notices: bool
    notify_kip_news: bool
    keywords: list[str]

    class Config:
        from_attributes = True


class KeywordRequest(BaseModel):
    keyword: str


# ─── API 엔드포인트 ────────────────────────────────────────

# 기사 목록 (탭별)
@app.get("/api/articles")
def get_articles(
    tab: str = Query("vc_notices", description="vc_notices 또는 kip_news"),
    search: str = Query("", description="검색어"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    session = SessionLocal()
    try:
        # 탭별 소스 필터
        if tab == "vc_notices":
            source_keys = ["kvca", "kvic"]
        elif tab == "kip_news":
            source_keys = ["kip"]
        else:
            raise HTTPException(400, "유효하지 않은 탭: vc_notices 또는 kip_news")

        q = session.query(Article).filter(Article.source.in_(source_keys))

        # 검색
        if search.strip():
            q = q.filter(Article.title.contains(search.strip()))

        total = q.count()
        articles = (
            q.order_by(desc(Article.date), desc(Article.id))
            .offset((page - 1) * size)
            .limit(size)
            .all()
        )

        # 스크랩 여부 조회
        article_ids = [a.id for a in articles]
        scrapped_ids = set(
            row[0] for row in session.query(Scrap.article_id)
            .filter(Scrap.article_id.in_(article_ids))
            .all()
        ) if article_ids else set()

        result = []
        for a in articles:
            result.append({
                "id": a.id,
                "source": a.source,
                "source_label": a.source_label,
                "date": a.date,
                "title": a.title,
                "link": a.link,
                "is_scrapped": a.id in scrapped_ids,
            })

        return {"total": total, "page": page, "size": size, "articles": result}
    finally:
        session.close()


# 스크랩 목록
@app.get("/api/scraps")
def get_scraps(
    search: str = Query("", description="검색어"),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
):
    session = SessionLocal()
    try:
        q = (
            session.query(Article)
            .join(Scrap, Article.id == Scrap.article_id)
        )

        if search.strip():
            q = q.filter(Article.title.contains(search.strip()))

        total = q.count()
        articles = (
            q.order_by(desc(Scrap.created_at))
            .offset((page - 1) * size)
            .limit(size)
            .all()
        )

        result = []
        for a in articles:
            result.append({
                "id": a.id,
                "source": a.source,
                "source_label": a.source_label,
                "date": a.date,
                "title": a.title,
                "link": a.link,
                "is_scrapped": True,
            })

        return {"total": total, "page": page, "size": size, "articles": result}
    finally:
        session.close()


# 스크랩 토글
@app.post("/api/scraps/{article_id}")
def toggle_scrap(article_id: int):
    session = SessionLocal()
    try:
        article = session.query(Article).get(article_id)
        if not article:
            raise HTTPException(404, "기사를 찾을 수 없습니다")

        existing = session.query(Scrap).filter(Scrap.article_id == article_id).first()
        if existing:
            session.delete(existing)
            session.commit()
            return {"scrapped": False, "message": "스크랩 해제"}
        else:
            session.add(Scrap(article_id=article_id))
            session.commit()
            return {"scrapped": True, "message": "스크랩 완료"}
    finally:
        session.close()


# 설정 조회
@app.get("/api/settings")
def get_settings():
    session = SessionLocal()
    try:
        settings = session.query(Settings).first()
        keywords = [kw.keyword for kw in session.query(NotificationKeyword).all()]
        return {
            "crawl_interval_minutes": settings.crawl_interval_minutes,
            "notifications_enabled": settings.notifications_enabled,
            "notify_vc_notices": settings.notify_vc_notices,
            "notify_kip_news": settings.notify_kip_news,
            "keywords": keywords,
        }
    finally:
        session.close()


# 설정 업데이트
@app.put("/api/settings")
def update_settings(data: SettingsUpdate):
    session = SessionLocal()
    try:
        settings = session.query(Settings).first()
        if data.crawl_interval_minutes is not None:
            settings.crawl_interval_minutes = max(30, data.crawl_interval_minutes)
        if data.notifications_enabled is not None:
            settings.notifications_enabled = data.notifications_enabled
        if data.notify_vc_notices is not None:
            settings.notify_vc_notices = data.notify_vc_notices
        if data.notify_kip_news is not None:
            settings.notify_kip_news = data.notify_kip_news
        session.commit()

        # 스케줄러 간격 업데이트
        if data.crawl_interval_minutes is not None:
            _update_scheduler_interval()

        return {"message": "설정 저장 완료"}
    finally:
        session.close()


# 키워드 추가
@app.post("/api/keywords")
def add_keyword(data: KeywordRequest):
    session = SessionLocal()
    try:
        kw = data.keyword.strip()
        if not kw:
            raise HTTPException(400, "키워드를 입력하세요")
        existing = session.query(NotificationKeyword).filter(
            NotificationKeyword.keyword == kw
        ).first()
        if existing:
            raise HTTPException(409, "이미 등록된 키워드입니다")
        session.add(NotificationKeyword(keyword=kw))
        session.commit()
        return {"message": f"키워드 '{kw}' 추가 완료"}
    finally:
        session.close()


# 키워드 삭제
@app.delete("/api/keywords/{keyword}")
def delete_keyword(keyword: str):
    session = SessionLocal()
    try:
        kw = session.query(NotificationKeyword).filter(
            NotificationKeyword.keyword == keyword
        ).first()
        if not kw:
            raise HTTPException(404, "키워드를 찾을 수 없습니다")
        session.delete(kw)
        session.commit()
        return {"message": f"키워드 '{keyword}' 삭제 완료"}
    finally:
        session.close()


# 수동 크롤링 트리거
@app.post("/api/crawl")
def trigger_crawl():
    try:
        result = run_news_crawl()
        return {"message": "크롤링 완료", "log": result}
    except Exception as e:
        raise HTTPException(500, f"크롤링 오류: {e}")


# ─── 정적 파일 & SPA 폴백 ──────────────────────────────────

# static 폴더가 있으면 마운트
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
