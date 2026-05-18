"""AI 기반 뉴스 제목 정제 모듈.

OpenRouter API (openrouter/free 모델)를 사용하여:
- VC 공고: 번호, 기관명, 날짜 등 군더더기를 제거하고 순수 '제목'만 추출
- KIP News: 뒤에 붙은 본문 요약/매체명/시간을 제거하고 순수 '헤드라인'만 추출
"""

from __future__ import annotations

import os
import json
import logging
import re
from typing import Optional

from openai import OpenAI
from dotenv import load_dotenv

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_THIS_DIR, ".env"))

logger = logging.getLogger("vcnews.title_cleaner")

# ─── OpenRouter 클라이언트 ─────────────────────────────────

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY가 .env에 설정되지 않았습니다")
        _client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
        )
    return _client


# 모델 — 기본값은 기존과 동일. 더 강한 무료 모델로 바꾸려면 .env 에
# TITLE_CLEANER_MODEL=... 만 추가하면 됨 (코드 변경 불필요).
_MODEL = os.getenv("TITLE_CLEANER_MODEL", "openrouter/free")


# ─── KIP(벤처뉴스) 전용 헬퍼 ───────────────────────────────
#
# 네이트 검색 결과 raw 제목 구조 (구분자 없이 전부 붙어 있음):
#   <헤드라인><본문 첫 문장들><언론사명><작성시각>
# 예) '중기부·한국벤처투자맞손…지역벤처투자인프라 강화중소벤처기업부와
#      한국벤처투자는 …밝혔다....디지털데일리2026.05.14 14:33'
#
# 작성시각은 매우 규칙적이라 결정적(regex)으로 먼저 제거하고,
# 어려운 '헤드라인↔본문' 경계만 LLM 에 맡긴다.

_TRAILING_META_RE = re.compile(
    r"\s*(?:"
    r"\d+\s*(?:초|분|시간|일|주|개월|달|년)\s*전"          # 11시간전, 23시간전, 3일전
    r"|어제|그제|그저께|오늘"
    r"|\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}(?:\s*\d{1,2}:\d{2})?"  # 2026.05.14 14:26
    r")\s*$"
)


def _strip_trailing_time(s: str) -> str:
    """맨 끝에 붙은 작성시각/상대시간 토큰만 결정적으로 제거.

    이 패턴들은 항상 문자열 맨 끝 메타데이터로만 등장하므로 오탐이 없다.
    여러 번 붙어 있을 수 있어(드물게) 변화가 없을 때까지 반복.
    """
    prev = None
    while prev != s:
        prev = s
        s = _TRAILING_META_RE.sub("", s).rstrip()
    return s


_IDX_LINE_RE = re.compile(r"^\s*(\d+)\s*[\t.):\-]\s*(.+?)\s*$")


def _parse_indexed(text: str) -> dict[int, str]:
    """`<번호><구분자><정제제목>` 형태 응답을 번호→제목 dict 로 파싱.

    위치가 아니라 번호로 매핑하므로, 모델이 한 줄을 빠뜨리거나 합쳐도
    나머지 제목이 엉뚱한 기사에 들어가지 않는다.
    """
    out: dict[int, str] = {}
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        m = _IDX_LINE_RE.match(line)
        if not m:
            continue
        idx = int(m.group(1))
        val = m.group(2).strip().strip("`").strip().strip('"').strip()
        if val and idx not in out:
            out[idx] = val
    return out


_KIP_SYSTEM_PROMPT = (
    "You clean Korean news headlines crawled from Nate news search.\n"
    "Each raw line has the form:\n"
    "  <INDEX>\\t<HEADLINE><ARTICLE BODY><PUBLISHER>\n"
    "The HEADLINE is a short title-style phrase at the very start. Right after it, "
    "the article BODY (the first reporting sentence(s)) is concatenated, very often "
    "WITH NO SPACE OR SEPARATOR, and finally the news outlet name is appended. "
    "Sometimes a run of dots (`...` or `....`) marks where the preview was truncated.\n\n"
    "TASK: return ONLY the leading HEADLINE. Cut everything from where the article "
    "body begins.\n"
    "Boundary cues (the body usually starts here):\n"
    "  • a full reporting sentence — a subject (organization/person/company name) "
    "followed by 은/는/이/가/와/과/, then ending in …다. / …했다. / …밝혔다. / …말했다. "
    "A Korean HEADLINE is a noun phrase and never contains such a finished sentence; "
    "if you see one, the body already started — stop before it.\n"
    "  • lead-ins like 이번/최근/지난/올해 … or a date token like '14일'.\n"
    "  • the FIRST run of 2+ dots, if present, when it sits at the headline end.\n"
    "RULES:\n"
    "  • Keep headline-internal punctuation EXACTLY: … · — \" ' [ ] ( ) % ‘ ’ “ ”.\n"
    "    (`…` U+2026 is a stylistic part of the headline — never cut at it.)\n"
    "  • Do NOT translate, summarize, paraphrase, reorder, fix spacing, or invent text.\n"
    "  • If you cannot confidently find the boundary, return the text up to the first "
    "run of 2+ dots; if there is none, return the line unchanged.\n"
    "OUTPUT: exactly one line per input, format `<INDEX>\\t<HEADLINE>`, same indices, "
    "nothing else (no commentary, no code fences).\n\n"
    "Examples:\n"
    "Input: 1\t지역벤처투자꽤 쏠쏠했네…지역펀드 수익률 \"최근 5년 11.6%\"수익성이 입증됨에 따라 한국벤처투자권역별투자센터를 확대한다고 14일 밝혔다. 중기부에 따르면 모태펀드는 2006년부터 누적 113개의 지역펀드를 총 1조8000억원 규모로 조성해 지역벤처투자마중물을 공급해왔다....경향신문\n"
    "Output: 1\t지역벤처투자꽤 쏠쏠했네…지역펀드 수익률 \"최근 5년 11.6%\"\n\n"
    "Input: 2\t중기부·한국벤처투자맞손…지역벤처투자인프라 강화중소벤처기업부와 한국벤처투자는 지역벤처투자생태계 고도화를 위해 지역펀드 성과를 기반으로 한국벤처투자권역별투자센터 확대 등을 추진한다고 밝혔다....디지털데일리\n"
    "Output: 2\t중기부·한국벤처투자맞손…지역벤처투자인프라 강화\n\n"
    "Input: 3\t국민성장펀드 간접투자운용사 숏리스트 발표…PE·VC 2배수 선정...리그에서는 도미누스에쿼티파트너스·스카이레이크에쿼티파트너스·에이티넘인베스트먼트·한국투자파트너스가 본선행 티켓을 얻었다....서울경제\n"
    "Output: 3\t국민성장펀드 간접투자운용사 숏리스트 발표…PE·VC 2배수 선정\n\n"
    "Input: 4\t페어스퀘어랩, 미래에셋캐피탈 ·한국투자파트너스등서 시리즈 B투자...벤처캐피털(VC)로부터 시리즈 B투자를 성공적으로 유치했다고 밝혔다. 이번 투자라운드는 CKX파트너스가 리드했으며 미래에셋캐피탈, 한국투자파트너스가 참여했다....동아일보\n"
    "Output: 4\t페어스퀘어랩, 미래에셋캐피탈 ·한국투자파트너스등서 시리즈 B투자"
)


def _clean_kip_titles(articles: list[dict], chunk_size: int = 8) -> list[dict]:
    """KIP(벤처뉴스) 제목 정제 — 하이브리드 (결정적 시각 제거 + LLM 경계 추출).

    - 입력 순서/길이를 그대로 보존 (run_news_crawl 의 위치 매핑과 호환).
    - 청크 단위로 호출해 약한 무료 모델의 품질 저하를 줄임.
    - API 가 실패해도 최소한 후행 시각은 제거된 제목을 남김(기존보다 항상 나음).
    """
    for start in range(0, len(articles), chunk_size):
        chunk = articles[start:start + chunk_size]
        # 1) 결정적 후행 시각 제거 + 내부 개행 제거
        raws = [
            _strip_trailing_time(a["title"].replace("\n", " ").strip())
            for a in chunk
        ]
        # 2) 번호를 붙여 입력 (탭 구분 — 헤드라인에 거의 없는 문자)
        user_content = "\n".join(f"{i + 1}\t{r}" for i, r in enumerate(raws))

        try:
            client = _get_client()
            response = client.chat.completions.create(
                model=_MODEL,
                messages=[
                    {"role": "system", "content": _KIP_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                temperature=0.0,
                max_tokens=2000,
            )
            content = response.choices[0].message.content or ""
            mapping = _parse_indexed(content.strip())

            applied = 0
            for i, art in enumerate(chunk):
                cleaned = mapping.get(i + 1)
                if cleaned:
                    art["title"] = cleaned
                    applied += 1
                else:
                    # 모델이 그 줄을 누락 → 최소한 결정적 정제는 반영
                    art["title"] = raws[i]
            logger.info(
                f"제목 정제 (kip): chunk@{start} {applied}/{len(chunk)}건 LLM 적용"
            )
        except Exception as e:
            logger.warning(
                f"제목 정제 API 오류(kip): {e}. 결정적 시각 제거만 적용."
            )
            for i, art in enumerate(chunk):
                art["title"] = raws[i]

    return articles


# ─── 제목 정제 함수 ────────────────────────────────────────

def clean_titles_batch(articles: list[dict], source_type: str) -> list[dict]:
    """여러 기사의 제목을 일괄 정제.

    - source_type == "vc": 구분자(|)로 나뉜 공고 제목 → 1회 호출 일괄 정제.
    - source_type == "kip": 네이트 뉴스 raw → 전용 하이브리드 파이프라인
      (결정적 시각 제거 + LLM 헤드라인 경계 추출, 청크 단위, 번호 키 매핑).

    Args:
        articles: [{"title": "...", ...}, ...] 형태의 기사 리스트
        source_type: "vc" 또는 "kip"

    Returns:
        입력과 동일한 리스트/순서 (title 필드가 정제된 값으로 교체됨)
    """
    if not articles:
        return articles

    # KIP(벤처뉴스)는 전용 하이브리드 파이프라인 사용 — VC 경로는 그대로 유지
    if source_type == "kip":
        return _clean_kip_titles(articles)
    if source_type != "vc":
        return articles

    # 원본 제목 추출
    raw_titles = [a["title"] for a in articles]

    system_prompt = (
            "You are a Korean news title cleaner. "
            "The user will provide a list of raw crawled VC (벤처캐피탈) notice titles, one per line. "
            "Extract ONLY the core announcement title — remove all serial numbers, dates, "
            "bracketed category tags like [출자계획] or [접수현황], and any organization prefix. "
            "Keep the substantive announcement title clean and readable in Korean. "
            "Do NOT remove the name of the fund (e.g., 모태펀드(보건복지부)). "
            "You MUST output exactly the same number of lines as the input. "
            "Format your output as a numbered list (1., 2., 3., etc.).\n\n"
            "Example 1:\n"
            "Input: 1059 | [출자계획] | 모태펀드(보건복지부) 2026년 5월 수시 출자사업 계획 공고 | 2026-05-11\n"
            "Output: 1. 모태펀드(보건복지부) 2026년 5월 수시 출자사업 계획 공고\n\n"
            "Example 2:\n"
            "Input: 470 | 서초구청 | [서초구청] 2026 서초AICT스타트업 2호 펀드 출자공고 | 2026-05-11 | 2026-06-10\n"
            "Output: 2. 2026 서초AICT스타트업 2호 펀드 출자공고"
        )

    # 한 줄에 하나씩 결합 (내부 개행 제거)
    user_content = "\n".join(t.replace("\n", " ").strip() for t in raw_titles)

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model=_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            max_tokens=4000,
        )

        content = response.choices[0].message.content
        result_text = content.strip() if content else ""
        
        # 줄 단위로 분리
        cleaned_lines = [line.strip() for line in result_text.split("\n") if line.strip()]
        
        # '1.', '-', '*' 등으로 시작하는 경우 제거
        for i in range(len(cleaned_lines)):
            cleaned_lines[i] = re.sub(r"^(\d+[\.\)]|[-*])\s*", "", cleaned_lines[i])

        if len(cleaned_lines) >= len(articles):
            for i, article in enumerate(articles):
                cleaned = cleaned_lines[i]
                if cleaned:
                    article["title"] = cleaned
            logger.info(f"제목 정제 완료 ({source_type}): {len(articles)}건")
        else:
            logger.warning(
                f"AI 응답 길이 불일치 (부족함): 원본 {len(articles)}건, 응답 {len(cleaned_lines)}건.\n"
                f"가능한 부분까지만 정제 적용."
            )
            for i, cleaned in enumerate(cleaned_lines):
                if cleaned and i < len(articles):
                    articles[i]["title"] = cleaned

    except json.JSONDecodeError as e:
        logger.warning(f"AI 응답 JSON 파싱 실패: {e}. 원본 제목 유지.")
    except Exception as e:
        logger.warning(f"제목 정제 API 오류: {e}. 원본 제목 유지.")

    return articles
