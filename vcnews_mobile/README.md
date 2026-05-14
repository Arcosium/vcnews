# VC News Android 빌드 가이드

## 프로젝트 구조

```
vcnews_mobile/
├── settings.gradle.kts          # 루트 Gradle 설정
├── build.gradle.kts             # 루트 빌드 스크립트
├── gradle.properties            # Gradle 환경변수
├── gradle/wrapper/
│   └── gradle-wrapper.properties
└── app/
    ├── build.gradle.kts         # 앱 모듈 빌드 (의존성, SDK 설정)
    ├── proguard-rules.pro       # R8 난독화 규칙
    └── src/main/
        ├── AndroidManifest.xml  # 권한, Activity, 딥링크 설정
        ├── java/uk/ai_ve/vcnews/
        │   ├── VCNewsApp.kt     # Application 클래스
        │   └── MainActivity.kt # WebView + 네이티브 통합
        └── res/
            ├── layout/activity_main.xml
            ├── drawable/
            │   ├── ic_launcher_foreground.xml
            │   ├── ic_offline.xml
            │   └── progress_bar.xml
            ├── mipmap-anydpi-v26/
            │   ├── ic_launcher.xml
            │   └── ic_launcher_round.xml
            └── values/
                ├── colors.xml
                ├── strings.xml
                └── themes.xml
```

## Android Studio에서 빌드하기

### 1. 프로젝트 열기
1. Android Studio 열기
2. **File → Open** → `vcnews_mobile` 폴더 선택
3. Gradle Sync가 자동으로 시작됩니다. 완료될 때까지 대기.

### 2. 디버그 빌드 (개발용)
- **Run** 버튼(▶) 클릭 → 에뮬레이터 또는 연결된 기기 선택
- 또는 터미널에서: `./gradlew assembleDebug`
- APK 위치: `app/build/outputs/apk/debug/app-debug.apk`

### 3. 릴리즈 빌드 (배포용)
1. **Build → Generate Signed Bundle / APK**
2. Keystore 생성 또는 기존 것 선택
3. APK 또는 AAB(Google Play용) 선택
4. Release 빌드 타입 선택 → **Finish**

커맨드라인:
```bash
./gradlew assembleRelease
```

## 주요 기능

| 기능 | 설명 |
|------|------|
| **WebView 전체화면** | `https://vcnews.ai-ve.uk` 을 전체화면으로 로드 |
| **Edge-to-Edge** | 상태바 투명 + 네비게이션바 다크 배경으로 몰입감 |
| **Splash Screen** | Material3 기반 스플래시 (웹 로드 완료 시 해제) |
| **SwipeRefresh** | 아래로 당기면 페이지 새로고침 |
| **오프라인 화면** | 네트워크 없을 때 "다시 시도" 버튼 노출 |
| **외부 링크 처리** | 같은 도메인은 앱 내, 외부 링크는 기본 브라우저 |
| **뒤로가기** | WebView history 내 이동, 없으면 홈으로 |
| **딥링크** | `https://vcnews.ai-ve.uk/*` URL을 앱으로 바로 연결 |
| **ProGuard** | 릴리즈 빌드 시 코드 최소화 + 난독화 |

## 빌드 환경 요구사항

- Android Studio Koala (2024.1+) 이상
- JDK 17
- Android SDK 34 (targetSdk)
- Gradle 8.7 / AGP 8.5.0

## 추후 확장 (FCM 푸시 알림)

Firebase 연동 시:
1. Firebase Console에서 앱 등록 → `google-services.json` 다운로드
2. `app/` 폴더에 `google-services.json` 배치
3. `build.gradle.kts`에 Firebase 의존성 추가
4. `FirebaseMessagingService` 구현 → 백엔드와 FCM 토큰 연동
