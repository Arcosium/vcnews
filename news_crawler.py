"""뉴스 크롤링 엔진 — DB 기반 웹앱 버전.

Google Sheets 의존성을 제거하고, SQLite DB(models.py)에 직접 저장합니다.
크롤링 대상:
  1) KVCA — 벤처협회공고 (한국벤처캐피탈협회)
  2) KVIC — 벤처협회공고 (한국벤처투자)
  3) KIP  — 한국투자파트너스 뉴스 (네이트 검색)

사용법:
    from news_crawler import run_news_crawl
    result_text = run_news_crawl()
"""

from __future__ import annotations

import datetime
import re
from urllib.parse import urljoin, quote
from typing import Optional

import requests
import urllib3
from bs4 import BeautifulSoup

from .models import SessionLocal, Article, init_db

urllib3.disable_warnings()

KST = datetime.timezone(datetime.timedelta(hours=9))

# ─── 크롤링 대상 소스 정의 ─────────────────────────────────────

CRAWL_SOURCES = [
    {
        "key": "kvca",
        "label": "벤처협회공고",
        "tab": "vc_notices",
        "url": "https://www.kvca.or.kr/Program/invest/list.html?a_gb=board&a_cd=8&a_item=0&sm=2_2_2",
    },
    {
        "key": "kvic",
        "label": "벤처협회공고",
        "tab": "vc_notices",
        "url": "https://www.kvic.or.kr/notice/kvic-notice/investment-business-notice",
    },
    {
        "key": "kip",
        "label": "KIP News",
        "tab": "kip_news",
        "url": "https://news.nate.com/search?q=" + quote("한국투자파트너스"),
    },
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class NewsCrawlerError(RuntimeError):
    pass


def _fetch_page(url: str) -> Optional[BeautifulSoup]:
    """URL에서 HTML을 가져와 BeautifulSoup 객체로 반환."""
    try:
        r = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            verify=False,
            timeout=15,
        )
        r.raise_for_status()
        return BeautifulSoup(r.text, "html.parser")
    except Exception:
        return None


def _parse_kvca(soup: BeautifulSoup, site_url: str) -> list[dict]:
    """KVCA 사이트에서 기사 파싱."""
    articles = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if not tds or len(tds) < 2:
            continue

        row_texts = []
        link = ""
        date_str = ""

        for td in tds:
            text = td.get_text(strip=True)
            if text:
                row_texts.append(text)

            a_tag = td.find("a")
            if a_tag and a_tag.get("href") and not a_tag["href"].startswith("#"):
                href = a_tag["href"]
                if href.startswith("javascript:"):
                    js_num = re.search(r"\d+", href)
                    if js_num:
                        link = site_url + "?id=" + js_num.group()
                    else:
                        link = site_url + " (직접 방문 필요)"
                else:
                    link = urljoin(site_url, href)

            date_match = re.search(r"\b20\d{2}[-./]\d{2}[-./]\d{2}\b", text)
            if not date_str and date_match and len(text) <= 12:
                date_str = date_match.group()

        if not link:
            continue

        title = " | ".join(row_texts)
        if not date_str:
            date_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")

        articles.append({"date": date_str, "title": title, "link": link})

    return articles


def _parse_kvic(soup: BeautifulSoup, site_url: str) -> list[dict]:
    """KVIC 사이트에서 기사 파싱."""
    articles = []

    # KVIC는 테이블 구조 또는 리스트 구조를 사용할 수 있음
    for tr in soup.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if not tds or len(tds) < 2:
            continue

        row_texts = []
        link = ""
        date_str = ""

        for td in tds:
            text = td.get_text(strip=True)
            if text:
                row_texts.append(text)

            a_tag = td.find("a")
            if a_tag and a_tag.get("href") and not a_tag["href"].startswith("#"):
                href = a_tag["href"]
                if href.startswith("javascript:"):
                    js_num = re.search(r"\d+", href)
                    if js_num:
                        link = site_url + "?id=" + js_num.group()
                else:
                    link = urljoin(site_url, href)

            date_match = re.search(r"\b20\d{2}[-./]\d{2}[-./]\d{2}\b", text)
            if not date_str and date_match and len(text) <= 12:
                date_str = date_match.group()

        if not link:
            continue

        title = " | ".join(row_texts)
        if not date_str:
            date_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")

        articles.append({"date": date_str, "title": title, "link": link})

    # 리스트 뷰 형태 폴백 (ul > li)
    for li in soup.select("ul.board-list li, .list-wrap li, .bbs-list li"):
        a_tag = li.find("a")
        if not a_tag or not a_tag.get("href"):
            continue

        title = a_tag.get_text(strip=True)
        href = a_tag["href"]
        link = urljoin(site_url, href) if not href.startswith("http") else href

        date_el = li.find(class_=re.compile(r"date|time|day"))
        date_str = ""
        if date_el:
            date_match = re.search(r"\b20\d{2}[-./]\d{2}[-./]\d{2}\b", date_el.get_text())
            if date_match:
                date_str = date_match.group()
        if not date_str:
            date_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")

        if title and link:
            articles.append({"date": date_str, "title": title, "link": link})

    return articles


def _parse_nate_news(soup: BeautifulSoup, site_url: str) -> list[dict]:
    """네이트 뉴스 검색 결과에서 기사 파싱."""
    articles = []

    # 네이트 검색결과 파싱 — 다양한 셀렉터 시도
    news_items = soup.select("div.news_list dl, ul.resultList li, div.newslist li, .news_cont")

    if not news_items:
        # 테이블 폴백
        for tr in soup.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if not tds or len(tds) < 2:
                continue
            row_texts = []
            link = ""
            date_str = ""
            for td in tds:
                text = td.get_text(strip=True)
                if text:
                    row_texts.append(text)
                a_tag = td.find("a")
                if a_tag and a_tag.get("href") and not a_tag["href"].startswith("#"):
                    link = urljoin(site_url, a_tag["href"])
                date_match = re.search(r"\b20\d{2}[-./]\d{2}[-./]\d{2}\b", text)
                if not date_str and date_match and len(text) <= 12:
                    date_str = date_match.group()
            if link:
                title = " | ".join(row_texts)
                if not date_str:
                    date_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")
                articles.append({"date": date_str, "title": title, "link": link})
    else:
        for item in news_items:
            a_tag = item.find("a")
            if not a_tag or not a_tag.get("href"):
                continue

            title = a_tag.get_text(strip=True)
            link = a_tag["href"]
            if not link.startswith("http"):
                link = urljoin(site_url, link)

            date_str = ""
            date_el = item.find(class_=re.compile(r"date|time|day|info"))
            if date_el:
                dm = re.search(r"\b20\d{2}[-./]\d{2}[-./]\d{2}\b", date_el.get_text())
                if dm:
                    date_str = dm.group()
            if not date_str:
                full_text = item.get_text()
                dm = re.search(r"\b20\d{2}[-./]\d{2}[-./]\d{2}\b", full_text)
                if dm:
                    date_str = dm.group()
            if not date_str:
                date_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")

            if title:
                articles.append({"date": date_str, "title": title, "link": link})

    # 일반 앵커 링크 폴백 — 뉴스 관련 링크 추출
    if not articles:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if not text or len(text) < 10:
                continue
            if "한국투자파트너스" in text or "한투" in text:
                link = href if href.startswith("http") else urljoin(site_url, href)
                articles.append({
                    "date": datetime.datetime.now(KST).strftime("%Y-%m-%d"),
                    "title": text,
                    "link": link,
                })

    return articles


# 소스별 파서 맵핑
PARSERS = {
    "kvca": _parse_kvca,
    "kvic": _parse_kvic,
    "kip": _parse_nate_news,
}


def run_news_crawl(test_mode: bool = False) -> str:
    """크롤링 1회 실행. 결과 문자열 반환. DB에 신규 기사 저장."""
    init_db()

    out_lines: list[str] = []

    def log(msg: str) -> None:
        out_lines.append(str(msg))

    now_kst = datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    log(f"[{now_kst} KST] 크롤링 시작")

    session = SessionLocal()
    total_new = 0

    try:
        # 기존 링크 캐시 (중복 검사용)
        existing_links = set(
            row[0] for row in session.query(Article.link).all()
        )

        for source in CRAWL_SOURCES:
            key = source["key"]
            url = source["url"]
            label = source["label"]
            site_added = 0

            log(f"\n  소스: {label} ({key})")
            soup = _fetch_page(url)
            if not soup:
                log(f"  · ERR  {url}  (페이지 로드 실패)")
                continue

            parser = PARSERS.get(key)
            if not parser:
                log(f"  · ERR  파서 없음: {key}")
                continue

            parsed_articles = parser(soup, url)
            log(f"  · 파싱된 항목: {len(parsed_articles)}건")

            # AI를 사용하여 제목 정제
            from .title_cleaner import clean_titles_batch
            source_type = "kip" if key == "kip" else "vc"
            parsed_articles = clean_titles_batch(parsed_articles, source_type)

            for art in parsed_articles:
                if art["link"] in existing_links:
                    continue

                article = Article(
                    source=key,
                    source_label=label,
                    date=art["date"],
                    title=art["title"],
                    link=art["link"],
                )
                session.add(article)
                existing_links.add(art["link"])
                site_added += 1

                if test_mode and site_added >= 1:
                    break

            log(f"  · 신규 저장: +{site_added}건")
            total_new += site_added

        session.commit()
        log(f"\n총 신규 항목: {total_new}건 저장 완료")

    except Exception as e:
        session.rollback()
        log(f"\nERROR: {e}")
    finally:
        session.close()

    if test_mode:
        log("(test_mode: 소스당 최대 1건만 추출)")

    return "\n".join(out_lines)


def get_new_articles_since(since: datetime.datetime) -> list[Article]:
    """지정 시각 이후에 생성된 기사 목록 반환 (알림 트리거용)."""
    session = SessionLocal()
    try:
        articles = (
            session.query(Article)
            .filter(Article.created_at >= since)
            .order_by(Article.created_at.desc())
            .all()
        )
        # detach from session
        session.expunge_all()
        return articles
    finally:
        session.close()
