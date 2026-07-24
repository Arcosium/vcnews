"""VC(KVCA/KVIC 공고) 제목 정제 = 규칙(LLM 없음) 계약.

2026-07-10: VC News 가 공유 llamaserver 슬롯을 오래 물어 ArQuant/ArkInsight 와 경합했다.
공고 제목은 `_parse_table_rows` 가 만든 `" | ".join(셀)` 이라 구조화돼 있어 LLM 이 불필요하다.
라이브 KVCA/KVIC 20건에 규칙 출력이 LLM 의도와 100% 일치함을 확인하고 대체했다.

Run: python3 -m pytest test_title_cleaner_vc.py -q
"""
import sys

sys.path.insert(0, "/home/arcosium/projects")
from vcnews import title_cleaner as tc  # noqa: E402


def test_strips_serial_org_tag_and_dates():
    raw = "1059 | [출자계획] | 모태펀드(보건복지부) 2026년 5월 수시 출자사업 계획 공고 | 2026-05-11"
    assert tc.clean_vc_title(raw) == "모태펀드(보건복지부) 2026년 5월 수시 출자사업 계획 공고"


def test_strips_leading_bracket_prefix_inside_the_title_cell():
    raw = "470 | 서초구청 | [서초구청] 2026 서초AICT스타트업 2호 펀드 출자공고 | 2026-05-11 | 2026-06-10"
    assert tc.clean_vc_title(raw) == "2026 서초AICT스타트업 2호 펀드 출자공고"


def test_keeps_fund_name_in_parentheses():
    raw = "1082 | [서류결과] | 모태펀드(보건복지부) 2026년 5월 수시 출자사업 서류심사 결과 | 2026-07-02"
    assert tc.clean_vc_title(raw) == "모태펀드(보건복지부) 2026년 5월 수시 출자사업 서류심사 결과"


def test_keeps_headline_internal_brackets():
    """제목 안의 [Next Finance] 같은 대괄호는 태그가 아니라 내용 — 보존해야 한다."""
    raw = "487 | 한국성장금융 | 「핀테크혁신펀드 7차 [Next Finance]」 위탁운용사 선정계획 공고 | 2026-06-17"
    assert tc.clean_vc_title(raw) == "「핀테크혁신펀드 7차 [Next Finance]」 위탁운용사 선정계획 공고"


def test_handles_fullwidth_brackets():
    raw = "489 | 제주특별자치도 | [제주특별자치도]｢제주 로컬기업 성장펀드｣ 업무집행조합원 모집 공고 | 2026-07-01"
    assert tc.clean_vc_title(raw) == "｢제주 로컬기업 성장펀드｣ 업무집행조합원 모집 공고"


def test_no_pipe_returns_input_untouched():
    assert tc.clean_vc_title("그냥 제목") == "그냥 제목"


def test_all_cells_dropped_falls_back_to_raw():
    assert tc.clean_vc_title("123 | 2026-07-10") == "123 | 2026-07-10"


def test_batch_vc_path_never_calls_the_llm(monkeypatch):
    """clean_titles_batch(..., 'vc') 는 LLM 클라이언트를 절대 만들지 않는다."""
    def _boom():
        raise AssertionError("vc 경로가 LLM 클라이언트를 호출했다")
    monkeypatch.setattr(tc, "_get_client", _boom)
    arts = [{"title": "493 | 중소기업중앙회 | 2026년도 중소기업중앙회 국내 블라인드 펀드 선정 공고 | 2026-07-10"}]
    out = tc.clean_titles_batch(arts, "vc")
    assert out[0]["title"] == "2026년도 중소기업중앙회 국내 블라인드 펀드 선정 공고"


def test_batch_preserves_order_and_count():
    arts = [
        {"title": "1 | [출자계획] | A펀드 출자공고 | 2026-07-01", "link": "a"},
        {"title": "2 | B운용 | B펀드 선정 공고 | 2026-07-02", "link": "b"},
    ]
    out = tc.clean_titles_batch(arts, "vc")
    assert len(out) == 2
    assert out[0]["link"] == "a" and out[1]["link"] == "b"
    assert out[0]["title"] == "A펀드 출자공고"
    assert out[1]["title"] == "B펀드 선정 공고"


def test_kip_path_still_uses_the_llm_pipeline(monkeypatch):
    """kip 은 의미 경계라 LLM 이 필요 — 규칙으로 갈아치우지 않았는지 확인."""
    called = {"n": 0}
    monkeypatch.setattr(tc, "_clean_kip_titles", lambda arts, **k: called.__setitem__("n", 1) or arts)
    tc.clean_titles_batch([{"title": "x"}], "kip")
    assert called["n"] == 1, "kip 이 전용 파이프라인을 안 탔다"
