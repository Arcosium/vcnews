# VC News Platform & Mobile App Implementation Plan

본 문서는 기존 Google Sheet 기반 뉴스 크롤링 시스템을 전면 개편하여, 전용 웹앱(https://vcnews.ai-ve.uk) 및 Android 모바일 앱으로 구축하기 위한 상세 구현 명세서입니다. 요구사항이 1M 컨텍스트 수준으로 완벽히 반영되도록, 아키텍처부터 백엔드, 프론트엔드, 모바일 빌드, 그리고 알림 시스템까지 모든 과정을 아주 상세히 정의합니다.

---

## 1. 시스템 아키텍처 개요 (System Architecture)

### 1.1 플랫폼 구성
- **Web App (웹 기반 프론트엔드 및 백엔드)**: 
  - URL: `https://vcnews.ai-ve.uk`
  - 기술 스택: React(Next.js) + Node.js 또는 기존 환경에 맞춘 Python 기반(Flask/FastAPI) 백엔드.
  - 역할: 크롤링된 데이터를 제공하는 RESTful API 서빙 및 모바일 앱 내 WebView에 렌더링될 UI/UX 제공.
- **Database (데이터베이스)**:
  - 기존의 Google Sheets에서 RDBMS(PostgreSQL 또는 SQLite)나 NoSQL(MongoDB)로 마이그레이션.
  - 중복 데이터 검증, 크롤링 주기, 사용자별 알림 키워드 및 설정 저장.
- **Android App (모바일 앱)**:
  - 위치: `/mobile/` 폴더 내 빌드
  - 기술 스택: Android Native (Kotlin) + WebView 기반 하이브리드 앱
  - 역할: 웹앱(https://vcnews.ai-ve.uk) 렌더링 및 네이티브 푸시 알림(FCM 연동) 처리.

---

## 2. 크롤링 백엔드 고도화 (Crawling Engine)

기존 `news_crawler.py`의 핵심 로직(Date, Title, Link 추출)을 승계하되, Google Sheets I/O를 DB 연동 및 자동화 스케줄러로 교체합니다.

### 2.1 크롤링 대상 소스
1. **벤처협회공고 (Tab 1 대상)**
   - 소스 A (KVCA): `https://www.kvca.or.kr/Program/invest/list.html?a_gb=board&a_cd=8&a_item=0&sm=2_2_2`
   - 소스 B (KVIC): `https://www.kvic.or.kr/notice/kvic-notice/investment-business-notice`
2. **한투파 뉴스 (Tab 2 대상)**
   - 소스 C (네이트 검색): `https://news.nate.com/search?q=%ED%95%9C%EA%B5%AD%ED%88%AC%EC%9E%90%ED%8C%8C%ED%8A%B8%EB%84%88%EC%8A%A4`

### 2.2 크롤링 스케줄링 (Crawling Scheduler)
- **기본 주기**: 1시간마다 자동 실행 (Cron job 또는 Celery/APScheduler 등 백그라운드 태스크 사용).
- **로직**:
  - 각 소스에서 페이지 파싱 후 `날짜`, `제목`, `링크` 추출.
  - DB에 `링크` 또는 `제목` 기준으로 중복 여부 확인 후 새로운 뉴스만 Insert.
  - 신규 데이터 삽입 시, **알림 트리거(Notification Trigger)** 이벤트 발생.

---

## 3. 사용자 인터페이스 (UI/UX) - 4개의 탭 구성

모바일 앱 및 웹앱은 하단 네비게이션 바(Bottom Navigation)를 통해 4개의 핵심 탭으로 구성됩니다. 디자인은 시인성이 높고 사용자 친화적인 모던 리스트 뷰를 적용합니다. 리스트 상단에는 **뉴스 검색 기능(Search Bar)** 이 제공되어, 크롤링된 전체 뉴스 중 원하는 키워드로 기사를 빠르게 찾을 수 있습니다.

### 3.1 [Tab 1] 벤처협회공고
- **데이터 소스**: KVCA 및 KVIC 파싱 데이터.
- **UI 요소**: 
  - 각 리스트 아이템에 **날짜(Date)**, **제목(Title)** 표시.
  - **별표(Scrap) 버튼**: 제목 앞에 별표(★) 모양의 아이콘 버튼을 배치하여, 클릭 시 해당 기사를 '스크랩' 탭에 저장.
  - **링크 이동 버튼**: 전체 URL 텍스트를 노출하는 대신, 제목 옆에 깔끔한 **[링크 이동] 버튼**을 배치하여 클릭 시 해당 링크로 이동(모바일 기본 브라우저 또는 인앱 브라우저 오픈).
  - 풀다운 리프레시(Pull-to-refresh)를 통한 수동 업데이트 지원.

### 3.2 [Tab 2] KIP News
- **데이터 소스**: 네이트 '한국투자파트너스' 검색 결과 데이터.
- **UI 요소**:
  - Tab 1과 동일한 디자인 컴포넌트 활용 (날짜, 제목, 별표 스크랩 버튼, 링크 이동 버튼 적용).

### 3.3 [Tab 3] 스크랩 (Scrap)
- **역할**: Tab 1 및 Tab 2에서 사용자가 제목 앞의 '별표(★) 버튼'을 눌러 저장한 기사들만 모아서 보여주는 전용 보관함 탭입니다.
- **UI 요소**: Tab 1, 2와 동일한 리스트 뷰 인터페이스를 제공하며, 스크랩 해제(별표 다시 누르기) 시 리스트에서 제거됩니다.

### 3.4 [Tab 4] 설정 (Settings)
앱의 동작 및 알림을 세밀하게 제어할 수 있는 메뉴입니다.

1. **크롤링 주기 설정 (Crawl Interval)**
   - 드롭다운 또는 슬라이더 형태.
   - 기본 1시간. 사용자가 30분, 3시간, 6시간, 12시간 등 직접 변경 가능. (단, 서버 부하를 고려해 최소 주기 제한 설정).
2. **앱 알림 (Push Notification) 켜기/끄기**
   - **전체 알림 끄기**: 모든 알림을 차단하는 마스터 토글 (알림 끄기 설정).
   - **소스별 알림 선택**: 
     - [ ] 한투파 뉴스 알림 받기
     - [ ] 벤처협회공고 알림 받기
3. **알림 키워드 설정 (Keyword Notification)**
   - 입력 필드(Text Input) 제공.
   - 사용자가 "투자", "펀드", "모태" 등 특정 키워드를 등록 가능.
   - **작동 방식**: 새로 크롤링된 기사의 제목에 해당 '키워드'가 포함되어 있을 때만 푸시 알림을 발송. (빈칸일 경우 소스별 알림 선택에 따라 모든 신규 뉴스 발송).

---

## 4. 안드로이드 모바일 앱 구현 명세 (`/mobile/` 폴더)

기존 모바일 WebView 연동 경험(ArcVoiceBridge 등)을 살려 빠르고 가벼운 앱을 구축합니다.

### 4.1 앱 빌드 환경
- **디렉토리**: `/home/opc/ArcAI.ve/Daily/VC_Crawling/mobile/` 내에 Android Studio 프로젝트(Gradle) 세팅.
- **메인 구조**: `MainActivity.kt` 내부에서 `WebView`를 띄워 `https://vcnews.ai-ve.uk`를 로드합니다.

### 4.2 푸시 알림 연동 (FCM: Firebase Cloud Messaging)
- 앱 내에 Firebase SDK 연동 (`google-services.json` 추가).
- 사용자가 설정 탭(Tab 4)에서 알림을 설정하면 웹앱 백엔드가 사용자의 FCM 토큰과 설정 데이터(키워드, 소스 등)를 DB에 매핑합니다.
- 백엔드 크롤러가 신규 뉴스를 발견하면 다음 로직을 거칩니다:
  1. 신규 뉴스의 소스 확인 (벤처협회공고 vs 한투파 뉴스).
  2. 사용자의 알림 수신 동의 여부 확인.
  3. 사용자가 설정한 키워드가 제목에 포함되어 있는지 검사.
  4. 조건을 만족하면 Firebase Admin SDK를 통해 해당 디바이스의 FCM 토큰으로 푸시 발송.
- **네이티브 처리**: Android `FirebaseMessagingService`에서 알림을 수신하여 상단 Status Bar에 알림을 띄우며, 클릭 시 해당 앱 탭으로 랜딩.

---

## 5. 마이그레이션 및 배포 파이프라인

1. **데이터베이스 이전**: 기존 Google Sheet 연동 인증(`news_crawler_token.json`) 방식에서 벗어나, 자체 로컬 DB(SQLite/PostgreSQL)로 시스템을 자립화.
2. **웹서버 배포**: Nginx 설정 업데이트 및 SSL 적용을 통해 `vcnews.ai-ve.uk` 서빙.
3. **모바일 릴리즈**: 안드로이드 `mobile` 폴더에서 `assembleRelease`를 통해 APK 빌드 후 사용자 기기 설치.

---

### 검토 완료 (User Requirement Checklist)
- [x] **Google Sheet 기반에서 웹앱(https://vcnews.ai-ve.uk)으로 이전**: 시스템 아키텍처에 반영됨.
- [x] **mobile 폴더 안에 Android 앱으로 빌드**: 안드로이드 WebView + 네이티브 프로젝트 구성 반영됨.
- [x] **4개의 탭 구성**: 벤처협회공고, KIP News, 스크랩, 설정 탭 명시됨.
- [x] **벤처협회공고 소스 2개 반영**: KVCA, KVIC 정확한 링크 포함됨.
- [x] **KIP News 소스 1개 반영**: 네이트 뉴스 정확한 링크 포함됨.
- [x] **기본 1시간마다 크롤링**: 스케줄러 기본 세팅 1시간 반영됨.
- [x] **UI 고도화**: 날짜, 제목 표시 외에 별표 스크랩 버튼, 링크 이동 버튼 및 상단 뉴스 검색 기능 반영됨.
- [x] **설정 탭 - 크롤링 주기 설정**: 드롭다운/슬라이더 설정 메뉴 반영됨.
- [x] **설정 탭 - 앱 푸시 알림 (소스 선택)**: 새 소식 발생 시 소스별 푸시 알림 반영됨.
- [x] **설정 탭 - 키워드 설정**: 특정 키워드 포함 시 푸시 알림 조건 로직 반영됨.
- [x] **설정 탭 - 알림 끄기**: 전체 알림 오프닝 마스터 토글 반영됨.
