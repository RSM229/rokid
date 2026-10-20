package com.gdjl.glasses

import android.app.Activity
import android.app.Application
import android.os.Bundle
import android.util.Log
import com.gdjl.glasses.device.RokidDeviceRuntime
import com.gdjl.glasses.device.RokidRtcRuntime
import com.gdjl.glasses.network.ApiClient
import com.gdjl.glasses.network.AuthManager
import com.gdjl.glasses.service.WorkRecordService
import com.gdjl.glasses.voice.AppForegroundTracker
import com.gdjl.glasses.voice.VoiceWakeService
import com.rokid.cxr.link.CXRLink

/**
 * Process-level bootstrap. The Rokid transport must remain available when the
 * business Activity is recreated or temporarily backgrounded.
 */
class GdjlApplication : Application() {

    companion object {
        lateinit var instance: GdjlApplication
            private set
        private const val TAG = "GdjlApplication"
    }

    var sharedLink: CXRLink? = null
    var isSessionReady: Boolean = false

    fun requireReadyLink(): CXRLink {
        check(sharedLink != null && isSessionReady) {
            "CXRLink session not ready: complete configCXRSession and connect first"
        }
        return sharedLink!!
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        ApiClient.init(this)
        AuthManager.init(this)
        RokidDeviceRuntime.initialize(this)
        RokidRtcRuntime.initialize(this)

        // 全程语音：前后台追踪（唤起服务据此让麦/监听）
        registerActivityLifecycleCallbacks(object : ActivityLifecycleCallbacks {
            override fun onActivityStarted(activity: Activity) = AppForegroundTracker.onActivityStarted()
            override fun onActivityStopped(activity: Activity) = AppForegroundTracker.onActivityStopped()
            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
            override fun onActivityResumed(activity: Activity) {}
            override fun onActivityPaused(activity: Activity) {}
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
            override fun onActivityDestroyed(activity: Activity) {}
        })

        // Rokid SDK 进程级初始化（单客户端槽位，必须在语音监听前完成注册）
        WorkRecordService.ensureInitialized(this)

        // 全程语音唤起：应用在前台启动服务（麦克风类前台服务须前台启动），退后台后开始监听
        VoiceWakeService.start(this)
    }
}
