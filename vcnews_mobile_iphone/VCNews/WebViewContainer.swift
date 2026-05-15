import SwiftUI
@preconcurrency import WebKit

/// SwiftUI ↔ UIKit 브릿지. Android 의 `binding.webView` (MainActivity setupWebView) 와 1:1 대응.
///
/// 외부에서 `targetURL` 을 바꾸면 자동 reload. `onLoadStateChange` 로 로딩/실패를 부모에 통지.
struct WebViewContainer: UIViewRepresentable {

    let initialURL: URL
    /// 외부 알림 탭 등으로 강제 로드해야 할 URL. nil 이면 무시.
    let pendingURL: URL?
    let onLoadStateChange: (WebViewLoadState) -> Void

    func makeCoordinator() -> WebViewCoordinator {
        WebViewCoordinator(onLoadStateChange: onLoadStateChange)
    }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []
        config.websiteDataStore = .default()      // 쿠키·localStorage 영속

        let prefs = WKWebpagePreferences()
        prefs.allowsContentJavaScript = true      // Android javaScriptEnabled = true
        config.defaultWebpagePreferences = prefs

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true  // iOS 식 좌측 스와이프 뒤로가기
        webView.scrollView.bounces = false                  // overScrollMode = NEVER 대응
        webView.scrollView.showsVerticalScrollIndicator = false
        webView.scrollView.showsHorizontalScrollIndicator = false
        webView.isOpaque = false
        webView.backgroundColor = UIColor(red: 0x0a / 255.0,
                                          green: 0x0e / 255.0,
                                          blue: 0x1a / 255.0,
                                          alpha: 1.0)
        webView.scrollView.backgroundColor = webView.backgroundColor

        // User-Agent 에 앱 식별자 (Android 와 동일)
        webView.customUserAgent = nil   // 시스템 UA 유지 + 추가
        webView.evaluateJavaScript("navigator.userAgent") { value, _ in
            if let ua = value as? String {
                webView.customUserAgent = "\(ua) VCNewsApp/1.0"
            }
        }

        context.coordinator.webView = webView
        webView.load(URLRequest(url: initialURL))

        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard let pending = pendingURL else { return }
        if context.coordinator.lastForcedURL != pending {
            context.coordinator.lastForcedURL = pending
            webView.load(URLRequest(url: pending))
        }
    }
}

enum WebViewLoadState {
    case loading
    case finished
    case failed
}
