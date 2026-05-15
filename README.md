# VC News Platform — 한국 벤처캐피탈 뉴스 통합 알리미

> 흩어져있는 한국 VC 공시·뉴스를 한 곳에 모으고, 키워드별로 알림을 받을 수 있는 멀티유저 웹 + Android 앱.

KVCA(한국벤처캐피탈협회), KVIC(한국벤처투자), 그리고 네이트 뉴스에 흩어져있는
**벤처캐피탈 / 벤처투자 / 한국투자파트너스** 관련 글을 한 백엔드에서 주기적으로 크롤링하고,
사용자별로 **알림 키워드 / 스크랩 / 알림 ON·OFF** 를 따로 관리합니다.

- **백엔드**: FastAPI + SQLite (WAL) + APScheduler
- **인증**: JWT (HttpOnly 쿠키) + PBKDF2-HMAC-SHA256 (600k iterations)
- **AI**: OpenRouter API 로 뉴스 제목 자동 정제 (번호/카테고리/기관 prefix 제거)
- **프론트**: Vanilla JS SPA (웹) + 네이티브 Android 앱 (Kotlin)

---

## 무엇을 수집하나

| 소스 | URL | 분류 |
|---|---|---|
| **KVCA** | kvca.or.kr 공지사항 | "VC 공지" 탭 |
| **KVIC** | kvic.or.kr 공지사항 | "VC 공지" 탭 |
| **Nate News (KIP)** | "벤처캐피탈" / "벤처투자" / "한국투자파트너스" 검색 결과 | "KIP 뉴스" 탭 |

크롤링 결과는 `link` 로 deduplicate 되며, 매 글마다 어떤 검색어에서 나왔는지 (`nate_query`) 기록합니다.
이 필드 덕분에 **"한국투자파트너스" 키워드를 알림에 추가하면 해당 검색어에서 나온 모든 글이
프론트엔드에 노출하지 않고도 알림 매칭** 됩니다 — 사용자에게는 평범한 키워드로 보이지만 백엔드에서는 query-tag 매칭이 일어납니다.

## 기능

### 일반 사용자
- **회원가입 / 로그인** — Remember-me 옵션 (영구 쿠키 vs 세션 쿠키)
- **두 개의 글 탭** — VC 공지 / KIP 뉴스 무한 스크롤
- **검색** — 제목 부분 일치
- **스크랩** — 30일 이후 자동 삭제되지 않는 개인 보관함
- **알림 설정**
  - 전역 ON/OFF
  - 소스별 ON/OFF (VC 공지 / KIP 뉴스)
  - 키워드별 매칭 (소스 + 키워드 unique)

### 관리자 (`is_admin=1`)
- **수동 크롤 트리거**
- **글로벌 크롤 주기 변경** (최소 30분)

### 백그라운드 스케줄러
- 기본 60분 주기 크롤링 (관리자가 변경 가능)
- 매일 03:30 KST — 30일 지난 미스크랩 글 자동 삭제
- 새 글 제목은 OpenRouter 무료 모델로 자동 정제 (예: `[공지] 003. 2025 VC 정기총회 안내` → `2025 VC 정기총회 안내`)

## 빠른 시작

### 1. 의존성

```bash
pip install fastapi uvicorn sqlalchemy beautifulsoup4 requests apscheduler \
            python-jose[cryptography] openai python-dotenv
```

### 2. 환경변수 (`.env`)

```bash
OPENROUTER_API_KEY=sk-or-...                # 제목 정제용 (필수)
VCNEWS_JWT_SECRET=<선택, 미설정시 .jwt_secret 자동 생성>
VCNEWS_DB_PATH=./vcnews.db                  # 선택
VCNEWS_DISABLE_SCHEDULER=                   # 1로 설정시 백그라운드 잡 비활성 (테스트용)
```

### 3. 실행

```bash
bash start_server.sh
# 0.0.0.0:8585 에서 uvicorn 기동 (VC_Crawling.app:app)
# 로그: /tmp/vcnews.log
```

### 4. 데몬 + 자가 회복

```bash
nohup ./supervise.sh > /tmp/vcnews_supervise.log 2>&1 &
# /api/health 가 죽으면 20초 안에 uvicorn 자동 재시작
```

### 5. (선택) Nginx 리버스 프록시

`vcnews.conf` 를 `/etc/nginx/conf.d/` 에 복사하면 `vcnews.ai-ve.uk` → `127.0.0.1:8585` 로 프록시.

## API 엔드포인트 (요약)

| Path | Method | 인증 | 설명 |
|---|---|---|---|
| `/api/health` | GET | - | liveness 체크 |
| `/api/signup` | POST | - | 회원가입 |
| `/api/login` | POST | - | JWT 쿠키 발급 |
| `/api/logout` | POST | ✓ | 쿠키 만료 |
| `/api/me` | GET | ✓ | 사용자 정보 |
| `/api/articles` | GET | ✓ | `?source=vc_notice|kip_news` 페이지네이션 |
| `/api/articles/search` | GET | ✓ | 제목 검색 |
| `/api/scraps` | GET / POST / DELETE | ✓ | 개인 보관함 |
| `/api/keywords` | GET / POST / DELETE | ✓ | 알림 키워드 관리 |
| `/api/preferences` | GET / PATCH | ✓ | 알림 ON/OFF |
| `/api/admin/crawl` | POST | admin | 수동 크롤 |
| `/api/admin/settings` | GET / PATCH | admin | 글로벌 설정 |

## 디렉터리 구조

```
VC_Crawling/
├── app.py                 # FastAPI 라우트 + 스케줄러 lifespan
├── auth.py                # JWT 발급/검증 + PBKDF2 + Depends(get_current_user)
├── models.py              # SQLAlchemy ORM + DB 마이그레이션
├── news_crawler.py        # KVCA / KVIC / Nate 스크레이퍼
├── title_cleaner.py       # OpenRouter 제목 정제
├── start_server.sh        # 8585 포트 정리 + uvicorn 기동
├── supervise.sh           # /api/health 워치독
├── vcnews.conf            # Nginx 프록시 설정
├── static/
│   ├── index.html         # SPA 진입점 (Material Icons)
│   ├── app.js             # 인증 / 탭 전환 / 검색 / 설정
│   └── style.css          # 반응형 (라이트/다크)
└── vcnews_mobile/         # Android 네이티브 앱 (Kotlin + Gradle)
    ├── MainActivity.kt
    ├── VCNewsApp.kt
    └── NewsCheckWorker.kt # 백그라운드 푸시 체크
```

## 데이터 모델 (SQLite)

| 테이블 | 키 컬럼 | 비고 |
|---|---|---|
| `users` | username, password_hash, is_admin | PBKDF2 600k iter |
| `user_preferences` | user_id, notify_global, notify_vc_notice, notify_kip_news | |
| `notification_keywords` | user_id, source, keyword | (source, keyword) unique per user |
| `articles` | source, date, title, link, nate_query | link unique 인덱스 |
| `scraps` | user_id, article_id | 스크랩된 글은 30일 cleanup 에서 보호됨 |
| `settings` | key, value | crawl_interval_minutes 등 |

## 보안 노트

- 비밀번호는 PBKDF2-HMAC-SHA256 600k iterations + per-user salt
- JWT는 `HS256`, HttpOnly + SameSite=Lax 쿠키. CSRF 위험을 줄이기 위해 모든 mutation API는 POST/PATCH/DELETE
- JWT 시크릿은 `.jwt_secret` (mode 0600) 또는 `VCNEWS_JWT_SECRET` 환경변수
- DB 파일이 유출돼도 비밀번호는 PBKDF2 로 보호되며, 시크릿 파일이 함께 유출되지 않으면 토큰 위조 불가

## Android 모바일 앱

`vcnews_mobile/` 는 별도 Gradle 프로젝트입니다. Android Studio 에서 열고 빌드하면 됩니다.
- `WorkManager` 의 `NewsCheckWorker` 가 주기적으로 백엔드를 폴링해 새 글이 있으면 푸시 알림
- 동일한 JWT 쿠키 인증 사용

---

## English Summary

Multi-user web + Android platform that aggregates Korean VC news from KVCA, KVIC,
and Nate News into a single feed with per-user notification keywords and a saved
articles library. FastAPI + SQLite backend with JWT auth (HttpOnly cookies),
APScheduler for background crawling, and OpenRouter API for automatic title
cleanup. UI is Korean-only.

**Stack:** FastAPI · SQLAlchemy · BeautifulSoup · APScheduler · Vanilla JS · Kotlin (Android)

## License

Personal project. Use at your own risk. Sources are public Korean VC news pages.
