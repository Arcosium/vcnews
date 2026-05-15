# VC News iOS 빌드 가이드

`vcnews_mobile` (Android Kotlin) 의 iOS 대응 버전. SwiftUI + `WKWebView` 로 `https://vcnews.ai-ve.uk` 를 감싸는 네이티브 래퍼.

## ⚠️ 중요한 전제

**iOS 앱은 macOS + Xcode 에서만 빌드·서명·배포 가능합니다.** 이 Linux 서버에는 소스만 두고, 실제 빌드는 Mac 으로 가져가서 진행해야 합니다.

또한 App Store 배포에는 **Apple Developer Program 가입 ($99/년)** 이 필요합니다. 본인 기기에서만 테스트하려면 무료 Apple ID 로 가능하지만 7일마다 재서명 필요.

## 프로젝트 구조

```
vcnews_mobile_iphone/
├── README.md
├── .gitignore
└── VCNews/
    ├── VCNewsApp.swift             # @main + AppDelegate (Android VCNewsApp.kt)
    ├── ContentView.swift           # SwiftUI 루트 + 오프라인 화면 + NWPathMonitor
    ├── WebViewContainer.swift      # UIViewRepresentable for WKWebView
    ├── WebViewCoordinator.swift    # WKNavigationDelegate (Android VCNewsWebViewClient)
    ├── NewsCheckTask.swift         # BGAppRefreshTask (Android NewsCheckWorker)
    ├── NotificationManager.swift   # UNUserNotificationCenter
    ├── Info.plist                  # Bundle / BGTask ID / 백그라운드 모드 / Launch
    ├── VCNews.entitlements         # Universal Links + APNs
    └── Assets.xcassets/
        ├── AppIcon.appiconset/     # 1024x1024 아이콘 넣을 자리
        ├── AccentColor.colorset/   # #6366f1 (Android `accent`)
        └── BgPrimary.colorset/     # #0a0e1a (Android `bg_primary`)
```

## Xcode 에서 프로젝트 만들기

이 폴더에는 소스만 들어있고 `.xcodeproj` 파일은 없습니다 (Xcode 가 생성). 권장 절차:

1. **Mac 에서 Xcode 15+ 실행** → `File → New → Project`
2. **iOS → App** 선택, 다음 설정:
   - Product Name: `VCNews`
   - Team: 본인 Apple ID
   - Organization Identifier: `uk.ai-ve`
   - Bundle Identifier: `uk.ai-ve.vcnews` (자동 생성됨)
   - Interface: **SwiftUI**
   - Language: **Swift**
   - Storage: **None**
3. 저장 위치는 임시 폴더. 만들어진 프로젝트의 기본 `ContentView.swift`, `VCNewsApp.swift` 를 **삭제**.
4. Finder 에서 이 폴더의 `VCNews/` 내 모든 `.swift` / `.plist` / `.entitlements` / `Assets.xcassets` 를 Xcode 의 `VCNews` 그룹으로 **드래그 → Copy items if needed 체크**.
5. **Target 설정**:
   - **General → Identity**: Display Name = `VC News`, Bundle ID = `uk.ai-ve.vcnews`
   - **General → Deployment Info**: iOS 15.0+ , iPhone 만, Portrait
   - **Signing & Capabilities → + Capability**:
     - `Background Modes` → `Background fetch` + `Remote notifications` 체크
     - `Push Notifications` 추가
     - `Associated Domains` 추가 → `applinks:vcnews.ai-ve.uk`
   - **Build Settings → Code Signing Entitlements**: `VCNews/VCNews.entitlements` 지정
   - **Info → Custom iOS Target Properties**: 이 폴더의 `Info.plist` 키들이 들어가 있는지 확인 (특히 `BGTaskSchedulerPermittedIdentifiers`, `UIBackgroundModes`, `UILaunchScreen`, `NSAppTransportSecurity`).
6. **Run** (▶) → 시뮬레이터 또는 실기기 선택.

> 매번 새 프로젝트 만들기가 번거로우면 `xcodegen` 또는 `tuist` 로 `project.yml` 을 두는 방식도 가능. 이 저장소는 일단 수동 방식으로 두었습니다.

## Android vs iOS 기능 매핑

| 기능 | Android (Kotlin) | iOS (Swift) |
|------|------------------|-------------|
| 앱 진입점 | `VCNewsApp : Application` + `MainActivity` | `@main struct VCNewsApp: App` + `AppDelegate` |
| 웹 호스트 | `WebView` (xml layout) | `WKWebView` (UIViewRepresentable) |
| 네비게이션 콜백 | `WebViewClient.shouldOverrideUrlLoading` | `WKNavigationDelegate.decidePolicyFor` |
| Edge-to-edge | `WindowCompat.setDecorFitsSystemWindows` | `.ignoresSafeArea()` |
| 스플래시 | `androidx.core.splashscreen` | `UILaunchScreen` (Info.plist) |
| 오프라인 감지 | `ConnectivityManager` | `NWPathMonitor` |
| 딥링크 | `intent-filter autoVerify` | Associated Domains + `applinks:` |
| 알림 | `NotificationCompat` + 채널 | `UNUserNotificationCenter` |
| **백그라운드 폴링** | `WorkManager` (15분 주기 보장) | `BGAppRefreshTask` (시스템이 시점 결정) |
| 외부 링크 | `Intent.ACTION_VIEW` | `UIApplication.shared.open(url)` |

## 백그라운드 폴링의 큰 차이

`NewsCheckTask.swift` 에 자세히 적었지만 요약:

- **Android `WorkManager`**: 등록만 하면 OS 가 15분 간격으로 강제로 깨워줌.
- **iOS `BGAppRefreshTask`**: 시스템이 사용자의 앱 사용 패턴 / 배터리 / 네트워크 상태를 보고 **언제 깨울지 알아서 결정**. 며칠간 안 깨우는 경우도 흔함.

진짜 즉시 알림이 필요하면 정답은 **서버에서 APNs 푸시 발송**:
1. Apple Developer Console 에서 APNs Auth Key (`.p8`) 발급
2. 서버 (`app.py`)에 새 엔드포인트 — 기기 등록 시 푸시 토큰 수신·저장
3. 신규 기사 발생 시 APNs HTTP/2 API 로 푸시 전송 (Python 라이브러리: `aioapns`, `httpx` 등)
4. iOS 쪽은 `UIApplication.registerForRemoteNotifications()` + `application(_:didRegisterForRemoteNotificationsWithDeviceToken:)` 추가

지금 코드는 `aps-environment: development` 로 두었으니 APNs 도입 시 entitlements 만 켜면 됨.

## Universal Links 동작시키려면

서버 (`vcnews.ai-ve.uk`) 의 정적 경로에 `apple-app-site-association` 파일을 두어야 합니다:

```
https://vcnews.ai-ve.uk/.well-known/apple-app-site-association
```

내용 예시:
```json
{
  "applinks": {
    "apps": [],
    "details": [{
      "appID": "TEAMID.uk.ai-ve.vcnews",
      "paths": ["/*"]
    }]
  }
}
```

- `TEAMID` 는 Apple Developer Console 의 본인 Team ID
- Content-Type: `application/json` (확장자 없는 정적 파일이어야 함)
- HTTPS 만 허용, 리다이렉트 금지

`app.py` 의 Flask 라우트로 추가하는 게 가장 쉽습니다.

## 아이콘 만들기

`vcnews_mobile/vcnews_icon.png` 를 1024×1024 PNG 로 변환해서
`VCNews/Assets.xcassets/AppIcon.appiconset/` 에 넣고 `Contents.json` 의 `images[0]` 에 `"filename": "icon.png"` 추가하면 됩니다.

Xcode 14+ 는 단일 1024 이미지에서 모든 사이즈를 자동 생성합니다.

## 빌드 / 배포 명령어

```bash
# Mac 의 vcnews_mobile_iphone/ 폴더 안에서
xcodebuild -project VCNews.xcodeproj \
           -scheme VCNews \
           -configuration Release \
           -sdk iphoneos \
           archive -archivePath build/VCNews.xcarchive

# App Store 업로드용 ipa 추출
xcodebuild -exportArchive \
           -archivePath build/VCNews.xcarchive \
           -exportPath build/ipa \
           -exportOptionsPlist ExportOptions.plist
```

평소엔 Xcode UI 의 **Product → Archive → Distribute App** 로 충분.

## 남아있는 user TODO

- `NewsCheckTask.swift` 내부 ⚠️ USER TODO 마커 — Android `NewsCheckWorker` 의 "첫 실행에는 알림 X / 베이스라인만 저장" 정책을 iOS 에서 구현. 5–10 줄.
- `AppIcon.appiconset` 에 실제 아이콘 PNG 추가.
- (선택) APNs 서버 측 구현.
