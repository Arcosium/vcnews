package uk.ai_ve.vcnews

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.util.Log
import android.webkit.CookieManager
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.Constraints
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.Worker
import androidx.work.WorkerParameters
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.TimeUnit

/**
 * 백그라운드에서 주기적으로 서버 `/api/articles/new` 를 호출해 신규 기사를 알림으로 표시한다.
 *
 * 동작:
 *  - SharedPreferences 의 `last_seen_article_id` 보다 큰 신규 기사를 가져온다.
 *  - 최초 실행 (last_seen == 0) 에는 latest_id 만 저장하고 알림을 표시하지 않는다 (백로그 폭주 방지).
 *  - 그 외에는 응답으로 받은 기사를 알림으로 표시하고, 표시한 최대 id 로 베이스라인을 갱신한다.
 *
 * 알림 정책 (마스터 토글 / 카테고리 / 키워드) 은 서버 측에서 이미 필터링됨.
 */
class NewsCheckWorker(context: Context, params: WorkerParameters) : Worker(context, params) {

    override fun doWork(): Result {
        return try {
            val prefs = applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val lastSeen = prefs.getLong(KEY_LAST_SEEN_ID, 0L)

            val json = fetchNewArticles(lastSeen) ?: return Result.retry()
            val latestId = json.optLong("latest_id", lastSeen)
            val articles = json.optJSONArray("articles") ?: JSONArray()

            // 첫 실행: 베이스라인만 저장하고 알림 표시 안함
            if (lastSeen == 0L) {
                prefs.edit().putLong(KEY_LAST_SEEN_ID, latestId).apply()
                return Result.success()
            }

            if (articles.length() == 0) {
                if (latestId > lastSeen) {
                    prefs.edit().putLong(KEY_LAST_SEEN_ID, latestId).apply()
                }
                return Result.success()
            }

            createChannelIfNeeded()

            // 응답은 id DESC — 가장 새로운 것부터.
            //  • 알림: 백로그 폭주 방지를 위해 상위 NOTIF_LIMIT 건만 표시.
            //  • 자동 스크랩: 알림 한도와 무관하게 가져온 신규 기사 전체 보관.
            //    (베이스라인이 batch 최댓값으로 점프하므로, 알림에서 잘린
            //     6번째 이하 기사도 스크랩해 두지 않으면 영영 유실됨.)
            val toShow = minOf(articles.length(), NOTIF_LIMIT)
            var maxSeenId = lastSeen
            for (i in 0 until articles.length()) {
                val a = articles.getJSONObject(i)
                val id = a.optLong("id", 0L)

                if (i < toShow) showNotification(a)

                // 자동 스크랩 — best-effort. 세션 만료/네트워크 오류로
                // 실패해도 로그만 남기고 계속한다 (알림은 이미 표시됨,
                // 워커를 재시도시켜 중복 알림을 내지 않는다).
                if (id > 0L && !ensureScrapped(id)) {
                    Log.w(TAG, "auto-scrap skipped for article $id (scrap call failed)")
                }

                if (id > maxSeenId) maxSeenId = id
            }
            prefs.edit().putLong(KEY_LAST_SEEN_ID, maxSeenId).apply()

            Result.success()
        } catch (e: Exception) {
            Log.e(TAG, "Worker failed", e)
            Result.retry()
        }
    }

    /**
     * 세션 쿠키를 실어 공용 헤더/타임아웃으로 연결을 열고 [block] 을 실행한다.
     *
     * WebView 에서 로그인 후 저장된 세션 쿠키를 그대로 사용한다. 쿠키가
     * 없으면(미로그인) 호출하지 않고 null. 예외/타임아웃도 로그 후 null.
     * 이 한 곳이 fetchNewArticles/ensureScrapped 의 연결 보일러플레이트를 모은다.
     */
    private fun <T> withSession(
        path: String,
        method: String,
        zeroLengthBody: Boolean = false,
        block: (HttpURLConnection) -> T,
    ): T? {
        val sessionCookie = CookieManager.getInstance().getCookie(API_BASE)
        if (sessionCookie.isNullOrBlank()) {
            Log.i(TAG, "no session cookie; user not logged in yet")
            return null
        }

        val conn = URL("$API_BASE$path").openConnection() as HttpURLConnection
        return try {
            conn.connectTimeout = 10_000
            conn.readTimeout = 10_000
            conn.requestMethod = method
            conn.setRequestProperty("Accept", "application/json")
            conn.setRequestProperty("User-Agent", "VCNewsApp-Worker/1.0")
            conn.setRequestProperty("Cookie", sessionCookie)
            if (zeroLengthBody) conn.setFixedLengthStreamingMode(0)
            block(conn)
        } catch (e: Exception) {
            Log.w(TAG, "$method $path failed: ${e.message}")
            null
        } finally {
            conn.disconnect()
        }
    }

    /**
     * 멱등 스크랩 엔드포인트 호출 — 기사를 사용자 스크랩함에 보관한다.
     *
     * 서버의 `POST /api/scraps/{id}/ensure` 는 toggle 이 아니라 add-only 라서
     * 같은 기사에 여러 번 호출해도 안전하다 (워커 재시도 대비).
     *
     * @return 보관 성공(또는 이미 보관됨)이면 true, 인증/네트워크 실패면 false.
     */
    private fun ensureScrapped(articleId: Long): Boolean {
        val ok = withSession("/api/scraps/$articleId/ensure", "POST", zeroLengthBody = true) { conn ->
            val code = conn.responseCode
            if (code != 200) {
                Log.w(TAG, "ensureScrapped($articleId) returned $code")
                false
            } else {
                true
            }
        }
        return ok == true
    }

    private fun fetchNewArticles(sinceId: Long): JSONObject? =
        withSession("$ENDPOINT?since_id=$sinceId&limit=20", "GET") { conn ->
            when (val code = conn.responseCode) {
                200 -> JSONObject(conn.inputStream.bufferedReader().use { it.readText() })
                401 -> {
                    Log.w(TAG, "API returned 401 — session expired")
                    null
                }
                else -> {
                    Log.w(TAG, "API returned $code")
                    null
                }
            }
        }

    private fun createChannelIfNeeded() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val mgr = applicationContext.getSystemService(NotificationManager::class.java)
            if (mgr.getNotificationChannel(CHANNEL_ID) == null) {
                val ch = NotificationChannel(
                    CHANNEL_ID, CHANNEL_NAME, NotificationManager.IMPORTANCE_DEFAULT
                ).apply { description = "신규 VC 뉴스 알림" }
                mgr.createNotificationChannel(ch)
            }
        }
    }

    private fun showNotification(article: JSONObject) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val granted = ContextCompat.checkSelfPermission(
                applicationContext, Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) return
        }

        val id = article.optLong("id", System.currentTimeMillis())
        val title = article.optString("title", "(제목 없음)")
        val link = article.optString("link", "")
        val sourceLabel = article.optString("source_label", "VC News")

        val intent = Intent(applicationContext, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            if (link.isNotEmpty()) putExtra(MainActivity.EXTRA_TARGET_URL, link)
        }
        val pi = PendingIntent.getActivity(
            applicationContext, id.toInt(), intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        val notif = NotificationCompat.Builder(applicationContext, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(sourceLabel)
            .setContentText(title)
            .setStyle(NotificationCompat.BigTextStyle().bigText(title))
            .setAutoCancel(true)
            .setContentIntent(pi)
            .build()

        NotificationManagerCompat.from(applicationContext).notify(id.toInt(), notif)
    }

    companion object {
        private const val TAG = "NewsCheckWorker"
        private const val CHANNEL_ID = "vc_news_channel"
        private const val CHANNEL_NAME = "VC 뉴스 알림"
        private const val PREFS = "vcnews_prefs"
        private const val KEY_LAST_SEEN_ID = "last_seen_article_id"
        private const val API_BASE = "https://vcnews.ai-ve.uk"
        private const val ENDPOINT = "/api/articles/new"
        private const val NOTIF_LIMIT = 5
        private const val UNIQUE_WORK_NAME = "vcnews_news_check"

        /**
         * 앱 시작 시 1회 호출 — 30분 주기 폴링 작업을 큐에 등록.
         * `KEEP` 정책이라 이미 등록되어 있으면 그대로 둔다.
         */
        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                .setRequiredNetworkType(NetworkType.CONNECTED)
                .build()
            val request = PeriodicWorkRequestBuilder<NewsCheckWorker>(30, TimeUnit.MINUTES)
                .setConstraints(constraints)
                .setBackoffCriteria(
                    androidx.work.BackoffPolicy.EXPONENTIAL,
                    10, TimeUnit.MINUTES,
                )
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                UNIQUE_WORK_NAME,
                ExistingPeriodicWorkPolicy.KEEP,
                request,
            )
        }
    }
}
