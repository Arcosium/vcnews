import SwiftUI
import Network

struct ContentView: View {

    private static let homeURL = URL(string: "https://vcnews.ai-ve.uk")!

    @State private var loadState: WebViewLoadState = .loading
    @State private var isOnline: Bool = true
    @State private var pendingURL: URL?
    @StateObject private var networkMonitor = NetworkMonitor()

    var body: some View {
        ZStack {
            Color(red: 0x0a / 255.0, green: 0x0e / 255.0, blue: 0x1a / 255.0)
                .ignoresSafeArea()

            if isOnline {
                WebViewContainer(
                    initialURL: Self.homeURL,
                    pendingURL: pendingURL,
                    onLoadStateChange: { state in
                        loadState = state
                        if state == .failed { isOnline = false }
                    }
                )
                .ignoresSafeArea(edges: [.top, .horizontal])

                if loadState == .loading {
                    ProgressView()
                        .progressViewStyle(.circular)
                        .tint(.white)
                }
            } else {
                OfflineView(onRetry: retry)
            }
        }
        .onReceive(networkMonitor.$isConnected) { connected in
            if connected && !isOnline {
                retry()
            }
        }
        .onOpenURL { url in
            // Universal Link 로 들어온 기사 URL → WebView 강제 로드
            if url.host?.contains("vcnews.ai-ve.uk") == true {
                pendingURL = url
                isOnline = networkMonitor.isConnected
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .openArticleURL)) { note in
            if let url = note.object as? URL {
                pendingURL = url
                isOnline = networkMonitor.isConnected
            }
        }
    }

    private func retry() {
        guard networkMonitor.isConnected else { return }
        isOnline = true
        pendingURL = nil
        loadState = .loading
    }
}

struct OfflineView: View {
    let onRetry: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "wifi.slash")
                .resizable()
                .scaledToFit()
                .frame(width: 64, height: 64)
                .foregroundColor(Color(red: 0.4, green: 0.45, blue: 0.55))

            Text("인터넷 연결 없음")
                .font(.title3.weight(.semibold))
                .foregroundColor(Color(red: 0.91, green: 0.93, blue: 0.96))

            Text("네트워크 연결을 확인한 후\n다시 시도해주세요.")
                .multilineTextAlignment(.center)
                .foregroundColor(Color(red: 0.55, green: 0.58, blue: 0.66))

            Button(action: onRetry) {
                Text("다시 시도")
                    .font(.body.weight(.semibold))
                    .padding(.horizontal, 28)
                    .padding(.vertical, 12)
                    .background(Color(red: 0.39, green: 0.4, blue: 0.95))
                    .foregroundColor(.white)
                    .clipShape(Capsule())
            }
            .padding(.top, 8)
        }
        .padding(32)
    }
}

/// `NWPathMonitor` 기반 네트워크 가용성 추적 — Android `isNetworkAvailable()` 대응.
final class NetworkMonitor: ObservableObject {
    @Published private(set) var isConnected: Bool = true

    private let monitor = NWPathMonitor()
    private let queue = DispatchQueue(label: "vcnews.network-monitor")

    init() {
        monitor.pathUpdateHandler = { [weak self] path in
            DispatchQueue.main.async {
                self?.isConnected = (path.status == .satisfied)
            }
        }
        monitor.start(queue: queue)
    }

    deinit { monitor.cancel() }
}

extension Notification.Name {
    static let openArticleURL = Notification.Name("vcnews.openArticleURL")
}
