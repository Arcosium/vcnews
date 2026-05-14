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

            // 응답은 id DESC — 가장 새로운 것부터. 표시 한도는 NOTIF_LIMIT.
            val toShow = minOf(articles.length(), NOTIF_LIMIT)
            var maxShownId = lastSeen
            for (i in 0 until toShow) {
                val a = articles.getJSONObject(i)
                showNotification(a)
                val id = a.optLong("id", 0L)
                if (id > maxShownId) maxShownId = id
            }
            prefs.edit().putLong(KEY_LAST_SEEN_ID, maxShownId).apply()

            Result.success()
        } catch (e: Exception) {
            Log.e(TAG, "Worker failed", e)
            Result.retry()
        }
    }

    private fun fetchNewArticles(sinceId: Long): JSONObject? {
        val url = URL("$API_BASE$ENDPOINT?since_id=$sinceId&limit=20")
        val conn = url.openConnection() as HttpURLConnection
        return try {
            conn.connectTimeout = 10_000
            conn.readTimeout = 10_000
            conn.requestMethod = "GET"
            conn.setRequestProperty("Accept", "application/json")
            conn.setRequestProperty("User-Agent", "VCNewsApp-Worker/1.0")
            val code = conn.responseCode
            if (code != 200) {
                Log.w(TAG, "API returned $code")
                return null
            }
            val text = conn.inputStream.bufferedReader().use { it.readText() }
            JSONObject(text)
        } catch (e: Exception) {
            Log.w(TAG, "fetchNewArticles failed: ${e.message}")
            null
        } finally {
            conn.disconnect()
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
