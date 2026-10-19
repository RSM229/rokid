package com.gdjl.glasses.device

/** Commercial CXR-S owns its ARTC lifecycle outside the enterprise RTC SDK. */
internal class EditionRtcBridge : RokidRtcBridge {
    override suspend fun join(request: RtcJoinRequest): Result<Unit> =
        Result.failure(UnsupportedOperationException("商业版请使用 CXR-S 配套 ARTC 服务"))

    override fun setMicrophoneEnabled(enabled: Boolean) = Unit
    override fun setCameraEnabled(enabled: Boolean) = Unit
    override fun leave() = Unit
    override fun release() = Unit
}
