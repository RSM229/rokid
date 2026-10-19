package com.gdjl.glasses.device

import android.content.Context
import com.rokid.android.base.anno.RKSubscribeMediaType
import com.rokid.android.meeting.bridge.api.anno.Resolution
import com.rokid.android.meeting.bridge.api.anno.VideoSize
import com.rokid.android.meeting.bridge.api.bean.RKChannelParam
import com.rokid.android.meeting.sdk.core.RKCooperation
import com.rokid.android.meeting.sdk.core.base.RKChannel

/** Production adapter for Rokid Remote Collaboration SDK 6.0.1. */
internal class EditionRtcBridge : RokidRtcBridge, ContextAwareRtcBridge {
    private lateinit var context: Context
    private var initializedKey: String? = null
    private var activeChannel: RKChannel? = null

    override fun attach(context: Context) {
        this.context = context.applicationContext
    }

    override suspend fun join(request: RtcJoinRequest): Result<Unit> = runCatching {
        check(::context.isInitialized) { "RTC 上下文未初始化" }
        val engine = RKCooperation.getRtcEngine()
        val key = "${request.appId}|${request.rtcUrl}|${request.userId}"
        if (initializedKey != key) {
            activeChannel?.leave()
            if (engine.isLogin()) engine.logout()
            engine.init(context, request.appId, request.rtcUrl)
            val loginResult = engine.login(request.userId)
            check(loginResult.isSuccess) {
                "Rokid RTC 登录失败：${loginResult.exceptionOrNull()?.message.orEmpty()}"
            }
            initializedKey = key
        }
        val channelParam = RKChannelParam().apply {
            maxMembers = 16
            frameRate = 24
            maxResolution = Resolution.RESOLUTION_720
            url = request.rtcUrl
            token = request.token
            defaultSubscribeMediaType = RKSubscribeMediaType.Both
            defaultStreamType = VideoSize.SIZE_SMALL
        }
        val joinResult = RKCooperation.getChannelManager().joinChannel(
            request.roomId,
            request.title,
            channelParam,
            20,
        )
        val channel = joinResult.getOrNull()
            ?: error("Rokid RTC 入会失败：${joinResult.exceptionOrNull()?.message.orEmpty()}")
        activeChannel = channel
        val device = engine.getLocalDevice()
        if (request.videoEnabled) device.openCamera() else device.closeCamera()
        device.startAudio()
        channel.enableUploadLocalVideoStream(request.videoEnabled)
        channel.enableUploadLocalAudioStream(true)
        channel.enableAudioOutput(true)
    }

    override fun setMicrophoneEnabled(enabled: Boolean) {
        val device = RKCooperation.getRtcEngine().getLocalDevice()
        if (enabled) device.startAudio() else device.stopAudio()
        activeChannel?.enableUploadLocalAudioStream(enabled)
    }

    override fun setCameraEnabled(enabled: Boolean) {
        val device = RKCooperation.getRtcEngine().getLocalDevice()
        if (enabled) device.openCamera() else device.closeCamera()
        activeChannel?.enableUploadLocalVideoStream(enabled)
    }

    override fun leave() {
        activeChannel?.leave()
        activeChannel = null
        RKCooperation.getRtcEngine().run {
            getLocalDevice().closeCamera()
            getLocalDevice().stopAudio()
        }
    }

    override fun release() {
        leave()
        RKCooperation.getRtcEngine().run {
            if (isLogin()) logout()
            destroy()
        }
        initializedKey = null
    }
}
