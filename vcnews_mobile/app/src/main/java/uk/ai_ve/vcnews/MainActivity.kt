package uk.ai_ve.vcnews

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.KeyEvent
import android.view.View
import android.view.WindowManager
import android.widget.Toast
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.splashscreen.SplashScreen.Companion.installSplashScreen
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsControllerCompat
import uk.ai_ve.vcnews.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var isPageLoaded = false

    // 두 번 누르면 종료 — 마지막 백 키 시각 (홈 화면에서만 동작).
    private var lastBackPressTime: Long = 0L
    private var backPressToast: Toast? = null

    companion object {
        private const val WEB_URL = "https://vcnews.ai-ve.uk"
        private const val KEY_URL = "current_url"
        private const val BACK_EXIT_WINDOW_MS = 2000L

        /** 알림 탭 시 WebView로 곧장 로드할 기사 URL (Intent extra key). */
        const val EXTRA_TARGET_URL = "vcnews.target_url"
    }

    // POST_NOTIFICATIONS 런타임 권한 결과 — 거부 시 그냥 무시 (사용자가 설정에서 다시 허용 가능).
    private val notifPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* granted -> Unit; 거부되면 알림은 표시되지 않을 뿐 앱은 정상 동작 */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        // Splash screen (Material 3 스타일)
        val splashScreen = installSplashScreen()
        splashScreen.setKeepOnScreenCondition { !isPageLoaded }

        super.onCreate(savedInstanceState)

        // Edge-to-edge: 상태바와 네비게이션 바를 투명하게
        setupEdgeToEdge()

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setupWebView()
        setupSwipeRefresh()
        setupBackNavigation()
        setupRetryButton()

        // 알림 권한 (API 33+) 및 백그라운드 폴링 워커 등록
        requestNotificationPermissionIfNeeded()
        NewsCheckWorker.schedule(applicationContext)

        // 알림 탭으로 시작된 경우 해당 URL, 아니면 저장된 URL, 아니면 기본 URL
        val urlToLoad = intent?.getStringExtra(EXTRA_TARGET_URL)
            ?: savedInstanceState?.getString(KEY_URL)
            ?: WEB_URL
        if (isNetworkAvailable()) {
            loadUrl(urlToLoad)
        } else {
            showErrorState()
        }
    }

    override fun onNewIntent(intent: Intent?) {
        super.onNewIntent(intent)
        // singleTask 모드라 이미 살아 있을 때 알림을 누르면 onNewIntent로 들어옴.
        val target = intent?.getStringExtra(EXTRA_TARGET_URL)
        if (!target.isNullOrEmpty() && isNetworkAvailable()) {
            loadUrl(target)
        }
    }

    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(
            this, Manifest.permission.POST_NOTIFICATIONS
        ) == PackageManager.PERMISSION_GRANTED
        if (!granted) {
            notifPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    // ─── Edge-to-Edge 설정 ──────────────────────────────────

    private fun setupEdgeToEdge() {
        WindowCompat.setDecorFitsSystemWindows(window, false)

        window.statusBarColor = Color.TRANSPARENT
        window.navigationBarColor = Color.parseColor("#0a0e1a")

        val controller = WindowInsetsControllerCompat(window, window.decorView)
        controller.isAppearanceLightStatusBars = false
        controller.isAppearanceLightNavigationBars = false

        window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS)
    }

    // ─── WebView 설정 ───────────────────────────────────────

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        binding.webView.apply {
            setBackgroundColor(Color.parseColor("#0a0e1a"))

            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                databaseEnabled = true

                // 모바일 최적화
                useWideViewPort = true
                loadWithOverviewMode = true
                setSupportZoom(false)
                builtInZoomControls = false
                displayZoomControls = false

                // 캐시 설정
                cacheMode = WebSettings.LOAD_DEFAULT

                // 미디어
                mediaPlaybackRequiresUserGesture = false
                allowContentAccess = true

                // User-Agent에 앱 식별자 추가
                userAgentString = "$userAgentString VCNewsApp/1.0"

                // Mixed content 허용 (HTTPS 페이지 내 HTTP 리소스)
                mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            }

            // 쿠키 허용
            CookieManager.getInstance().let {
                it.setAcceptCookie(true)
                it.setAcceptThirdPartyCookies(this, true)
            }

            webViewClient = VCNewsWebViewClient()
            webChromeClient = VCNewsChromeClient()

            // 스크롤 바 숨김 (웹앱 자체 UI 사용)
            isVerticalScrollBarEnabled = false
            isHorizontalScrollBarEnabled = false
            overScrollMode = View.OVER_SCROLL_NEVER
        }
    }

    // ─── SwipeRefresh 설정 ──────────────────────────────────

    private fun setupSwipeRefresh() {
        // Pull-to-refresh 비활성화 — 헤더의 새로고침 버튼만 사용.
        binding.swipeRefresh.isEnabled = false
    }

    // ─── 뒤로가기 핸들링 ────────────────────────────────────

    private fun setupBackNavigation() {
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (binding.webView.canGoBack()) {
                    binding.webView.goBack()
                    return
                }
                // WebView 최상위 — 2초 내 두 번째 백 키 → 종료
                val now = System.currentTimeMillis()
                if (now - lastBackPressTime <= BACK_EXIT_WINDOW_MS) {
                    backPressToast?.cancel()
                    finish()
                    return
                }
                lastBackPressTime = now
                backPressToast?.cancel()
                backPressToast = Toast.makeText(
                    this@MainActivity,
                    "한 번 더 누르면 종료됩니다",
                    Toast.LENGTH_SHORT,
                ).also { it.show() }
            }
        })
    }

    // ─── 오프라인 재시도 버튼 ────────────────────────────────

    private fun setupRetryButton() {
        binding.btnRetry.setOnClickListener {
            if (isNetworkAvailable()) {
                hideErrorState()
                loadUrl(WEB_URL)
            }
        }
    }

    // ─── URL 로드 ───────────────────────────────────────────

    private fun loadUrl(url: String) {
        binding.errorContainer.visibility = View.GONE
        binding.webView.visibility = View.VISIBLE
        binding.webView.loadUrl(url)
    }

    // ─── 에러 / 오프라인 상태 ───────────────────────────────

    private fun showErrorState() {
        binding.webView.visibility = View.GONE
        binding.errorContainer.visibility = View.VISIBLE
        binding.progressBar.visibility = View.GONE
    }

    private fun hideErrorState() {
        binding.errorContainer.visibility = View.GONE
        binding.webView.visibility = View.VISIBLE
    }

    // ─── 네트워크 확인 ──────────────────────────────────────

    private fun isNetworkAvailable(): Boolean {
        val cm = getSystemService(ConnectivityManager::class.java)
        val network = cm.activeNetwork ?: return false
        val caps = cm.getNetworkCapabilities(network) ?: return false
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    // ─── 상태 저장/복원 ─────────────────────────────────────

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(KEY_URL, binding.webView.url)
        binding.webView.saveState(outState)
    }

    override fun onRestoreInstanceState(savedInstanceState: Bundle) {
        super.onRestoreInstanceState(savedInstanceState)
        binding.webView.restoreState(savedInstanceState)
    }

    // ─── WebViewClient ──────────────────────────────────────

    inner class VCNewsWebViewClient : WebViewClient() {

        override fun onPageStarted(view: WebView?, url: String?, favicon: Bitmap?) {
            super.onPageStarted(view, url, favicon)
            binding.progressBar.visibility = View.VISIBLE
        }

        override fun onPageFinished(view: WebView?, url: String?) {
            super.onPageFinished(view, url)
            isPageLoaded = true
            binding.progressBar.visibility = View.GONE
            binding.swipeRefresh.isRefreshing = false

            // 웹앱의 상태바 영역 패딩 주입
            view?.evaluateJavascript(
                """
                (function() {
                    var meta = document.querySelector('meta[name="viewport"]');
                    if (meta) {
                        meta.setAttribute('content', 
                            'width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover');
                    }
                })();
                """.trimIndent(),
                null
            )
        }

        override fun shouldOverrideUrlLoading(
            view: WebView?,
            request: WebResourceRequest?
        ): Boolean {
            val url = request?.url?.toString() ?: return false

            // 같은 도메인이면 WebView 내에서 로드
            if (url.contains("vcnews.ai-ve.uk")) {
                return false
            }

            // 외부 링크는 시스템 브라우저로 열기
            try {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
            } catch (_: Exception) {
                // 핸들러 없는 URL은 무시
            }
            return true
        }

        override fun onReceivedError(
            view: WebView?,
            request: WebResourceRequest?,
            error: WebResourceError?
        ) {
            super.onReceivedError(view, request, error)
            // 메인 프레임 에러만 처리
            if (request?.isForMainFrame == true) {
                showErrorState()
            }
        }
    }

    // ─── ChromeClient (프로그레스 바) ───────────────────────

    inner class VCNewsChromeClient : WebChromeClient() {
        override fun onProgressChanged(view: WebView?, newProgress: Int) {
            super.onProgressChanged(view, newProgress)
            binding.progressBar.progress = newProgress
            if (newProgress >= 100) {
                binding.progressBar.visibility = View.GONE
            }
        }
    }
}
