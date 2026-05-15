"""뉴스 크롤링 엔진 — 다중 키워드 지원.

크롤링 대상:
  1) KVCA — 벤처협회공고 (한국벤처캐피탈협회)
  2) KVIC — 벤처협회공고 (한국벤처투자)
  3) KIP  — 네이트뉴스 (3개 키워드 검색: 벤처캐피탈 / 벤처투자 / 한국투자파트너스)
           각 기사에 nate_query 컬럼으로 어느 검색어로 들어왔는지 기록.
           link 기준 중복 제거.

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

# 네이트뉴스 검색 키워드 (KIP 소스로 통합 저장)
NATE_QUERIES = ["벤처캐피탈", "벤처투자", "한국투자파트너스"]

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
]

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


class NewsCrawlerError(RuntimeError):
    pass


def _fetch_page(url: str) -> Optional[BeautifulSoup]:
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
    articles = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if not tds or len(tds) < 2:
            continue

        row_texts = []
        link = ""
        date_str = ""

        for td in tds:
            text_ = td.get_text(strip=True)
            if text_:
                row_texts.append(text_)

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

            date_match = re.search(r"\b20\d{2}[-./]\d{2}[-./]\d{2}\b", text_)
            if not date_str and date_match and len(text_) <= 12:
                date_str = date_match.group()

        if not link:
            continue

        title = " | ".join(row_texts)
        if not date_str:
            date_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")

        articles.append({"date": date_str, "title": title, "link": link})

    return articles


def _parse_kvic(soup: BeautifulSoup, site_url: str) -> list[dict]:
    articles = []

    for tr in soup.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if not tds or len(tds) < 2:
            continue

        row_texts = []
        link = ""
        date_str = ""

        for td in tds:
            text_ = td.get_text(strip=True)
            if text_:
                row_texts.append(text_)

            a_tag = td.find("a")
            if a_tag and a_tag.get("href") and not a_tag["href"].startswith("#"):
                href = a_tag["href"]
                if href.startswith("javascript:"):
                    js_num = re.search(r"\d+", href)
                    if js_num:
                        link = site_url + "?id=" + js_num.group()
                else:
                    link = urljoin(site_url, href)

            date_match = re.search(r"\b20\d{2}[-./]\d{2}[-./]\d{2}\b", text_)
            if not date_str and date_match and len(text_) <= 12:
                date_str = date_match.group()

        if not link:
            continue

        title = " | ".join(row_texts)
        if not date_str:
            date_str = datetime.datetime.now(KST).strftime("%Y-%m-%d")

        articles.append({"date": date_str, "title": title, "link": link})

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


def _parse_nate_news(soup: BeautifulSoup, site_url: str, query: str) -> list[dict]:
    """네이트 뉴스 검색 결과 파싱. query 는 검색어 (fallback 매칭에만 사용)."""
    articles = []

    news_items = soup.select("div.news_list dl, ul.resultList li, div.newslist li, .news_cont")

    if not news_items:
        for tr in soup.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if not tds or len(tds) < 2:
                continue
            row_texts = []
            link = ""
            date_str = ""
            for td in tds:
                text_ = td.get_text(strip=True)
                if text_:
                    row_texts.append(text_)
                a_tag = td.find("a")
                if a_tag and a_tag.get("href") and not a_tag["href"].startswith("#"):
                    link = urljoin(site_url, a_tag["href"])
                date_match = re.search(r"\b20\d{2}[-./]\d{2}[-./]\d{2}\b", text_)
                if not date_str and date_match and len(text_) <= 12:
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

    # 폴백: 일반 앵커에서 검색어 포함된 것만
    if not articles:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text_ = a.get_text(strip=True)
            if not text_ or len(text_) < 10:
                continue
            if query in text_:
                link = href if href.startswith("http") else urljoin(site_url, href)
                articles.append({
                    "date": datetime.datetime.now(KST).strftime("%Y-%m-%d"),
                    "title": text_,
                    "link": link,
                })

    return articles


PARSERS = {
    "kvca": _parse_kvca,
    "kvic": _parse_kvic,
}


def _crawl_nate_all() -> list[dict]:
    """3개 검색어로 네이트 크롤링. 결과는 link 기준 중복 제거.

    각 dict 에 nate_query 필드 추가 — 어떤 키워드로 들어왔는지 기록.
    중복 발생 시 *먼저 매칭된* 키워드를 유지 (NATE_QUERIES 순서대로).
    """
    seen_links: set[str] = set()
    merged: list[dict] = []

    for q in NATE_QUERIES:
        url = "https://news.nate.com/search?q=" + quote(q)
        soup = _fetch_page(url)
        if not soup:
            continue
        parsed = _parse_nate_news(soup, url, q)
        for art in parsed:
            if art["link"] in seen_links:
                continue
            seen_links.add(art["link"])
            art["nate_query"] = q
            merged.append(art)

    return merged


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
        existing_links = set(
            row[0] for row in session.query(Article.link).all()
        )

        # ── KVCA / KVIC ───────────────────────────────────
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

            from .title_cleaner import clean_titles_batch
            parsed_articles = clean_titles_batch(parsed_articles, "vc")

            for art in parsed_articles:
                if art["link"] in existing_links:
                    continue

                article = Article(
                    source=key,
                    source_label=label,
                    date=art["date"],
                    title=art["title"],
                    link=art["link"],
                    nate_query=None,
                )
                session.add(article)
                existing_links.add(art["link"])
                site_added += 1

                if test_mode and site_added >= 1:
                    break

            log(f"  · 신규 저장: +{site_added}건")
            total_new += site_added

        # ── KIP (3 키워드 통합) ───────────────────────────
        log(f"\n  소스: KIP News (네이트, 3 키워드)")
        nate_articles = _crawl_nate_all()
        log(f"  · 통합 파싱: {len(nate_articles)}건 (중복 제거 후)")

        if nate_articles:
            from .title_cleaner import clean_titles_batch
            # title cleaner 는 title 만 다루므로 nate_query 보존 위해
            # 원본을 그대로 두고 cleaned 만 매핑.
            cleaned = clean_titles_batch(
                [{"title": a["title"]} for a in nate_articles], "kip"
            )
            for i, cl in enumerate(cleaned):
                nate_articles[i]["title"] = cl["title"]

        site_added = 0
        for art in nate_articles:
            if art["link"] in existing_links:
                continue

            article = Article(
                source="kip",
                source_label="벤처뉴스",
                date=art["date"],
                title=art["title"],
                link=art["link"],
                nate_query=art.get("nate_query"),
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
    session = SessionLocal()
    try:
        articles = (
            session.query(Article)
            .filter(Article.created_at >= since)
            .order_by(Article.created_at.desc())
            .all()
        )
        session.expunge_all()
        return articles
    finally:
        session.close()
