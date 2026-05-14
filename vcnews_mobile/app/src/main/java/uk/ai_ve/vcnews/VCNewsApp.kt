package uk.ai_ve.vcnews

import android.app.Application

class VCNewsApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // 추후 Firebase, Analytics 등 글로벌 초기화 지점
    }
}
