import Foundation
import BackgroundTasks
import UIKit

/// Android `NewsCheckWorker` 의 iOS 대응. BGAppRefreshTask 사용.
///
/// ⚠️ iOS 와 Android 의 큰 차이:
/// - Android `WorkManager`: 최소 15분 주기 *보장* (시스템 정책 + Doze 모드 한계 내).
/// - iOS `BGAppRefreshTask`: 시간은 시스템이 결정. 사용자가 앱을 자주 열수록 자주 깨움.
///   배터리·네트워크 상태에 따라 며칠간 안 깨울 수도 있음.
///
/// 진짜 즉시성이 필요하면 서버에서 APNs 푸시를 보내는 게 정석.
/// 여기선 일단 best-effort 폴링 + 향후 APNs 로 마이그레이션 가능한 구조로 둠.
enum NewsCheckTask {

    static let identifier = "uk.ai-ve.vcnews.news-check"
    private static let apiURL = URL(string: "https://vcnews.ai-ve.uk/api/articles/new")!
    private static let prefsKey = "last_seen_article_id"

    // MARK: - 등록 / 스케줄

    /// AppDelegate didFinishLaunching 에서 호출. Info.plist 의 ID 와 반드시 일치해야 함.
    static func register() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: nil) { task in
            guard let refreshTask = task as? BGAppRefreshTask else {
                task.setTaskCompleted(success: false)
                return
            }
            handle(task: refreshTask)
        }
    }

    /// 다음 백그라운드 fetch 스케줄링. 앱이 백그라운드로 갈 때마다 호출.
    static func scheduleNext() {
        let request = BGAppRefreshTaskRequest(identifier: identifier)
        // 15분 후 *이후* 언제든 실행 가능 — 시스템이 정확한 시간을 결정.
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        do {
            try BGTaskScheduler.shared.submit(request)
        } catch {
            // 시뮬레이터 / 비활성화된 백그라운드 모드면 실패. 무시.
        }
    }

    // MARK: - 실행

    private static func handle(task: BGAppRefreshTask) {
        // 만료 시 in-flight 요청 취소를 위한 핸들러
        let session = URLSession(configuration: .ephemeral)
        task.expirationHandler = {
            session.invalidateAndCancel()
        }

        // 다음 회차 미리 예약 (안 하면 영영 다시 안 깨움)
        scheduleNext()

        let prefs = UserDefaults.standard
        let lastSeen = prefs.object(forKey: prefsKey) as? Int64 ?? 0

        var components = URLComponents(url: apiURL, resolvingAgainstBaseURL: false)!
        components.queryItems = [URLQueryItem(name: "since_id", value: String(lastSeen))]
        guard let url = components.url else {
            task.setTaskCompleted(success: false)
            return
        }

        session.dataTask(with: url) { data, _, error in
            guard let data, error == nil,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                task.setTaskCompleted(success: false)
                return
            }

            let latestId = (json["latest_id"] as? Int64) ?? lastSeen
            let articles = (json["articles"] as? [[String: Any]]) ?? []

            // ─────────────────────────────────────────────────────────
            // 👤 USER TODO — 비즈니스 정책 결정 지점
            //
            // Android NewsCheckWorker 와 동일한 정책을 여기서 구현하세요.
            // 핵심 규칙 (Android 코드 주석 그대로):
            //   1) lastSeen == 0 (첫 실행) → 알림 표시 안 함, latestId 만 베이스라인으로 저장.
            //      이유: 백로그 폭주 방지 (앱 깔자마자 수십 개 알림 날아오는 것 차단)
            //   2) articles 가 비어 있어도 latestId > lastSeen 이면 베이스라인 갱신.
            //   3) 그 외에는 articles 를 알림으로 표시하고 표시한 최대 id 를 베이스라인으로.
            //
            // 사용 가능한 변수:
            //   lastSeen: Int64                            (이전 베이스라인)
            //   latestId: Int64                            (서버가 알려준 최신 id)
            //   articles: [[String: Any]]                  (각 dict 는 id/title/url 등 보유)
            //   prefs: UserDefaults                        (prefs.set(_:forKey: prefsKey))
            //   NotificationManager.shared.postArticleNotification(id:title:articleURL:)
            //
            // 구현 후 마지막에 task.setTaskCompleted(success: true) 호출.
            // ─────────────────────────────────────────────────────────

            task.setTaskCompleted(success: false)  // TODO: 위 정책 구현 후 true 로
        }.resume()
    }
}
