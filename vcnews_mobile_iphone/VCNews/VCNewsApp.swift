import SwiftUI
import BackgroundTasks
import UserNotifications

@main
struct VCNewsApp: App {

    // SwiftUI 의 새 라이프사이클에서 UIApplicationDelegate 가 필요할 때 쓰는 어댑터.
    // BGTaskScheduler 등록·APNs 토큰 수신은 AppDelegate 단계에서 잡아야 함.
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            ContentView()
                .preferredColorScheme(.dark)
                .ignoresSafeArea()
        }
        .onChange(of: scenePhase) { phase in
            if phase == .background {
                NewsCheckTask.scheduleNext()
            }
        }
    }
}

final class AppDelegate: NSObject, UIApplicationDelegate {

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {

        NewsCheckTask.register()
        NotificationManager.shared.requestAuthorizationIfNeeded()
        UNUserNotificationCenter.current().delegate = NotificationManager.shared

        return true
    }
}
