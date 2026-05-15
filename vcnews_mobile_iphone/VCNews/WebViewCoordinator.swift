import Foundation
import UIKit
@preconcurrency import WebKit

/// Android `VCNewsWebViewClient` + `VCNewsChromeClient` 통합 역할.
/// - 같은 도메인 → 앱 내 WebView
/// - 외부 링크 → 시스템 Safari
/// - 메인 프레임 실패 → 부모에 .failed 통지 (오프라인 화면 노출 트리거)
final class WebViewCoordinator: NSObject, WKNavigationDelegate, WKUIDelegate {

    private static let allowedHost = "vcnews.ai-ve.uk"

    weak var webView: WKWebView?
    var lastForcedURL: URL?
    private let onLoadStateChange: (WebViewLoadState) -> Void

    init(onLoadStateChange: @escaping (WebViewLoadState) -> Void) {
        self.onLoadStateChange = onLoadStateChange
    }

    // MARK: - 네비게이션 정책 (shouldOverrideUrlLoading 대응)

    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {

        guard let url = navigationAction.request.url else {
            decisionHandler(.cancel)
            return
        }

        // 첫 로드/리다이렉트는 통과
        if navigationAction.navigationType == .other {
            decisionHandler(.allow)
            return
        }

        if url.host?.contains(Self.allowedHost) == true {
            decisionHandler(.allow)
        } else {
            // 외부 링크: 시스템 브라우저로 위임
            UIApplication.shared.open(url, options: [:], completionHandler: nil)
            decisionHandler(.cancel)
        }
    }

    // MARK: - 로딩 상태 전달

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        onLoadStateChange(.loading)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        onLoadStateChange(.finished)

        // viewport 메타 보정 — Android onPageFinished 와 동일
        let js = """
        (function() {
            var meta = document.querySelector('meta[name="viewport"]');
            if (meta) {
                meta.setAttribute('content',
                    'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover');
            }
        })();
        """
        webView.evaluateJavaScript(js, completionHandler: nil)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled {
            onLoadStateChange(.failed)
        }
    }

    func webView(_ webView: WKWebView,
                 didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        if (error as NSError).code != NSURLErrorCancelled {
            onLoadStateChange(.failed)
        }
    }

    // MARK: - 새 창 요청도 같은 WebView 에서 처리 (Android target="_blank" 대응)

    func webView(_ webView: WKWebView,
                 createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction,
                 windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url {
            if url.host?.contains(Self.allowedHost) == true {
                webView.load(navigationAction.request)
            } else {
                UIApplication.shared.open(url, options: [:], completionHandler: nil)
            }
        }
        return nil
    }
}
