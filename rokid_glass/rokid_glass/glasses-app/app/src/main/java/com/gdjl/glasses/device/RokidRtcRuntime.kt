package com.gdjl.glasses.device

import android.content.Context
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

data class RtcJoinRequest(
    val appId: String,
    val rtcUrl: String,
    val userId: String,
    val roomId: String,
    val token: String,
    val title: String = "监理远程协作",
    val videoEnabled: Boolean = true,
)

interface RokidRtcBridge {
    suspend fun join(request: RtcJoinRequest): Result<Unit>
    fun setMicrophoneEnabled(enabled: Boolean)
    fun setCameraEnabled(enabled: Boolean)
    fun leave()
    fun release()
}

object RokidRtcRuntime {
    private val mutex = Mutex()
    private lateinit var applicationContext: Context
   // private val bridge: RokidRtcBridge by lazy { EditionRtcBridge() }
    private val bridge: RokidRtcBridge by lazy { 
    object : RokidRtcBridge {
        override suspend fun join(request: RtcJoinRequest): Result<Unit> {
            // TODO: 实现真实的 RTC 入会逻辑
            return Result.success(Unit)
        }
        override fun setMicrophoneEnabled(enabled: Boolean) {}
        override fun setCameraEnabled(enabled: Boolean) {}
        override fun leave() {}
        override fun release() {}
    }
}
    fun initialize(context: Context) {
        applicationContext = context.applicationContext
        if (bridge is ContextAwareRtcBridge) {
            (bridge as ContextAwareRtcBridge).attach(applicationContext)
        }
    }

    suspend fun join(request: RtcJoinRequest): Result<Unit> = mutex.withLock {
        require(::applicationContext.isInitialized) { "RTC runtime is not initialized" }
        require(request.appId.isNotBlank()) { "RTC AppID 不能为空" }
        require(request.rtcUrl.isNotBlank()) { "RTC 服务地址不能为空" }
        require(request.userId.isNotBlank()) { "RTC 用户不能为空" }
        require(request.roomId.isNotBlank()) { "RTC 房间不能为空" }
        require(request.token.isNotBlank()) { "RTC 短期令牌不能为空" }
        bridge.join(request)
    }

    fun setMicrophoneEnabled(enabled: Boolean) = bridge.setMicrophoneEnabled(enabled)
    fun setCameraEnabled(enabled: Boolean) = bridge.setCameraEnabled(enabled)
    fun leave() = bridge.leave()
}

internal interface ContextAwareRtcBridge {
    fun attach(context: Context)
}
