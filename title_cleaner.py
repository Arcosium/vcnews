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


# ─── 제목 정제 함수 ────────────────────────────────────────

def clean_titles_batch(articles: list[dict], source_type: str) -> list[dict]:
    """여러 기사의 제목을 한 번의 API 호출로 일괄 정제.

    Args:
        articles: [{"title": "...", ...}, ...] 형태의 기사 리스트
        source_type: "vc" 또는 "kip"

    Returns:
        입력과 동일한 리스트 (title 필드가 정제된 값으로 교체됨)
    """
    if not articles:
        return articles

    # 원본 제목 추출
    raw_titles = [a["title"] for a in articles]

    if source_type == "vc":
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
    elif source_type == "kip":
        system_prompt = (
            "You are a Korean news headline extractor. "
            "The user will provide a list of raw crawled news search result strings, one per line. "
            "In each string, the actual HEADLINE appears at the very beginning, followed by an ellipsis (...) or body text. "
            "Your task is to EXTRACT the exact headline from the beginning of the string. "
            "DO NOT summarize. DO NOT paraphrase. DO NOT invent new titles. "
            "Simply identify the headline portion at the start and return it exactly as it is, removing the trailing body content, dates, and publisher names. "
            "You MUST output exactly the same number of lines as the input. "
            "Format your output as a numbered list (1., 2., 3., etc.).\n\n"
            "Example 1:\n"
            "Input: '7대 1의 혈투' 7조원대투자마중물 확보戰 스타트[국민성장펀드...리그에도 17개사가 지원해 8.5대 1의 경쟁률을 보였다...이투데이11시간전\n"
            "Output: 1. '7대 1의 혈투' 7조원대투자마중물 확보戰 스타트[국민성장펀드]\n\n"
            "Example 2:\n"
            "Input: 페어스퀘어랩, 미래에셋캐피탈한국투자파트너스등에서 시리즈 B투자...미래에셋캐피탈,한국투자파트너스...서울신문2026.04.15 10:37\n"
            "Output: 2. 페어스퀘어랩, 미래에셋캐피탈한국투자파트너스등에서 시리즈 B투자"
        )
    else:
        return articles

    # 한 줄에 하나씩 결합 (내부 개행 제거)
    user_content = "\n".join(t.replace("\n", " ").strip() for t in raw_titles)

    try:
        client = _get_client()
        response = client.chat.completions.create(
            model="openrouter/free",
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
