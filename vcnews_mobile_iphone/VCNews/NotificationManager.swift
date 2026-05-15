import Foundation
import UIKit
import UserNotifications

/// Android `NotificationCompat` + 알림 채널 코드의 iOS 대응.
final class NotificationManager: NSObject, UNUserNotificationCenterDelegate {

    static let shared = NotificationManager()

    private let center = UNUserNotificationCenter.current()

    /// Foreground 에서도 배너 표시 — Android 식 즉시 알림 노출과 가깝게.
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                willPresent notification: UNNotification,
                                withCompletionHandler completionHandler:
                                @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .sound, .list])
    }

    /// 알림 탭 → ContentView 로 URL 전달 (Android EXTRA_TARGET_URL 대응).
    func userNotificationCenter(_ center: UNUserNotificationCenter,
                                didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        let userInfo = response.notification.request.content.userInfo
        if let urlString = userInfo["target_url"] as? String,
           let url = URL(string: urlString) {
            NotificationCenter.default.post(name: .openArticleURL, object: url)
        }
        completionHandler()
    }

    func requestAuthorizationIfNeeded() {
        center.getNotificationSettings { [weak self] settings in
            guard let self else { return }
            switch settings.authorizationStatus {
            case .notDetermined:
                self.center.requestAuthorization(options: [.alert, .badge, .sound]) { _, _ in }
            default:
                break  // 거부/허용 상태면 그대로 둠. 설정 앱에서 변경 가능.
            }
        }
    }

    /// Android `NewsCheckWorker.showNotification` 대응.
    func postArticleNotification(id: Int64, title: String, articleURL: URL) {
        let content = UNMutableNotificationContent()
        content.title = "VC News"
        content.body = title
        content.sound = .default
        content.userInfo = ["target_url": articleURL.absoluteString, "article_id": id]

        let request = UNNotificationRequest(
            identifier: "vcnews.article.\(id)",
            content: content,
            trigger: nil          // 즉시 발송
        )
        center.add(request, withCompletionHandler: nil)
    }
}
