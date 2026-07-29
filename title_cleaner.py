"""AI 기반 뉴스 제목 정제 모듈.

로컬 OpenAI 호환 LLM 서버를 사용하여:
- VC 공고: 번호, 기관명, 날짜 등 군더더기를 제거하고 순수 '제목'만 추출
- KIP News: 뒤에 붙은 본문 요약/매체명/시간을 제거하고 순수 '헤드라인'만 추출
"""

from __future__ import annotations

import os
import logging
import re
from typing import Optional

# 통합 .env 에서 LOCAL_LLM_* 등을 프로세스 환경에 주입한다(서비스가 EnvironmentFile을
# 지정하지 않아도 동작하도록). 미설치/실패 시 조용히 무시하고 기존 환경을 사용한다.
try:
    from dotenv import load_dotenv
    load_dotenv("/home/arcosium/projects/.env")
except Exception:
    pass

from openai import OpenAI
logger = logging.getLogger("vcnews.title_cleaner")

# ─── 로컬 OpenAI 호환 클라이언트 ─────────────────────────────
#
# llama.cpp, vLLM, Ollama(OpenAI compatibility mode) 등 OpenAI 호환
# /v1/chat/completions 엔드포인트를 사용한다. 이 프로그램은 어떤 외부 API 키도
# 읽거나 전송하지 않는다. OpenAI SDK는 api_key 인자를 요구하므로 로컬 서버에
# 전달해도 비밀값이 아닌 고정 더미 문자열만 사용한다.

_LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
_LOCAL_LLM_API_KEY = "local"

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=_LOCAL_LLM_BASE_URL,
            api_key=_LOCAL_LLM_API_KEY,
        )
    return _client


# 모델명은 로컬 서버에 로드한 모델 식별자와 같아야 한다. 서버가 다른 별칭을
# 노출하면 LOCAL_LLM_MODEL 환경변수로 바꾼다.
_MODEL = os.getenv(
    "LOCAL_LLM_MODEL",
    "qwen3.6-35b-a3b-uncensored",
)


# ─── VC(KVCA/KVIC 공고) 전용 규칙 정제 — LLM 불필요 ───────────────────────
#
# raw 제목은 `_parse_table_rows` 가 만든 `" | ".join(테이블 셀)` 이라 **구조화**돼 있다:
#   '493 | 중소기업중앙회 | 2026년도 … 국내 블라인드 펀드 선정 공고 | 2026-07-10 | 2026-07-31'
# LLM 이 하던 일(일련번호·[태그]·날짜·기관명 셀 제거, 제목 셀 추출)은 전부 규칙으로 된다.
# 실측(KVCA/KVIC 라이브 20건) 100% 일치 확인 — LLM 경합 아까워 규칙으로 대체(사장 지시 2026-07-10).
_VC_SERIAL = re.compile(r"^\d{1,7}$")                       # 게시판 일련번호 셀
_VC_DATE = re.compile(r"^\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}$")  # 날짜 셀
_VC_BRACKET_ONLY = re.compile(r"^[\[\(（].*[\]\)）]$")        # [출자계획]·[서류결과] 처럼 태그만인 셀
_VC_STATUS = re.compile(r"^(마감|접수중|진행중|예정|D-?\d+|조회\s*\d+|첨부|新|N|HOT|공지)$")
_VC_LEAD_BRACKET = re.compile(r"^\s*[\[\(（][^\]\)）]{1,20}[\]\)）]\s*")  # 셀 안 선행 [기관명]


def clean_vc_title(raw: str) -> str:
    """` | ` 로 이어진 공고 게시판 셀에서 실제 공고 제목만 추출 — 규칙만(LLM 없음)."""
    cells = [c.strip() for c in str(raw).split("|")]
    keep = [c for c in cells if c and not (
        _VC_SERIAL.match(c) or _VC_DATE.match(c)
        or _VC_BRACKET_ONLY.match(c) or _VC_STATUS.match(c))]
    if not keep:
        return str(raw).strip()
    title = max(keep, key=len)          # 제목 문장이 기관명·상태 셀보다 길다
    prev = None                          # 셀 안 선행 [기관명] 제거 (여러 겹일 수 있어 반복)
    while prev != title:
        prev = title
        title = _VC_LEAD_BRACKET.sub("", title).strip()
    return title or str(raw).strip()


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
    - 청크 단위로 호출해 모델의 품질 저하를 줄임.
    - 로컬 LLM 호출이 실패해도 최소한 후행 시각은 제거된 제목을 남김.
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
                # 제목 경계 추출은 추론이 전혀 필요 없다. llama-server(--jinja)의
                # chat_template_kwargs.enable_thinking=False 로 추론을 **템플릿 레벨에서 강제 차단**한다
                # (프롬프트 /no_think 는 이 모델에서 불신 — ArQuant 2026-07-09 실측: 이 방식만 추론 0토큰).
                # 추론이 꺼지면 출력이 정제 제목뿐이라 24000→512 로 줄여도 충분하고, 호출이 수분→수초로 준다
                # (공유 llamaserver 슬롯 점유 시간 최소화 — 사장 지시 2026-07-10).
                max_tokens=512,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
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

    # KIP(벤처뉴스)는 헤드라인↔본문 경계가 의미적이라 LLM 이 실제로 필요 → 하이브리드 파이프라인 유지.
    if source_type == "kip":
        return _clean_kip_titles(articles)
    if source_type != "vc":
        return articles

    # VC(KVCA/KVIC 공고)는 `|` 로 구분된 구조화 셀이라 **규칙으로 완전 대체**(LLM 호출 안 함).
    for a in articles:
        a["title"] = clean_vc_title(a.get("title", ""))
    logger.info(f"제목 정제 완료 (vc·규칙): {len(articles)}건")
    return articles