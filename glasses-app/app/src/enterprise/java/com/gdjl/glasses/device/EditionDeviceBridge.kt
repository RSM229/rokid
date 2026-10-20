package com.gdjl.glasses.device

import android.content.Context
import android.os.RemoteException
import android.util.Log
import com.gdjl.glasses.BuildConfig
import com.rokid.security.glass3.open.sdk.GlassSdk
import com.rokid.security.glass3.open.sdk.client.IServiceConnectionCallback
import com.rokid.security.glass3.sdk.base.data.media.PhotoResolution
import com.rokid.security.system.server.IClientCallback
import com.rokid.security.system.server.media.callback.PhotoFileCallback
import com.rokid.security.system.server.message.listener.IMessageListener
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/** Enterprise Glass3 strong-typed SDK transport. */
internal class EditionDeviceBridge : RokidDeviceBridge {
    companion object {
        private const val TAG = "EnterpriseRokidBridge"
    }

    override val edition: String = "enterprise"
    private lateinit var events: RokidDeviceEvents
    private val pendingPhotos = ConcurrentHashMap<String, (Result<File>) -> Unit>()
    private var registered = false

    private val messageListener = object : IMessageListener.Stub() {
        override fun onTextMessage(message: String?) {
            message?.takeIf(String::isNotBlank)?.let(events::onCommand)
        }

        override fun onAudioStream(data: ByteArray?) = Unit

        override fun onStreamDataReceived(tag: String?, data: ByteArray?) {
            if (tag == "gdjl.command" && data != null && data.isNotEmpty()) {
                events.onCommand(data.toString(Charsets.UTF_8))
            }
        }
    }

    private val photoCallback = object : PhotoFileCallback.Stub() {
        override fun getCallbackId(): String = "gdjl-${BuildConfig.APPLICATION_ID}"

        override fun onTakePhoto(path: String?) {
            completePhoto(path)
        }

        override fun onTakePhotoV2(path: String?, width: Int, height: Int) {
            completePhoto(path)
        }
    }

    override fun initialize(context: Context, events: RokidDeviceEvents) {
        this.events = events
        events.onStateChanged(RokidConnectionState.CONNECTING)
        GlassSdk.bindSecurityService(
            context,
            object : IServiceConnectionCallback {
                override fun onServiceConnected() {
                    GlassSdk.registerClient(
                        BuildConfig.ROKID_ENTERPRISE_GLASS_CLIENT_ID,
                        object : IClientCallback.Stub() {
                            override fun onReady() {
                                registerServices()
                            }
                        },
                    )
                }

                override fun onServiceDisconnected() {
                    registered = false
                    events.onStateChanged(RokidConnectionState.DISCONNECTED)
                }

                override fun onBindingDied() {
                    registered = false
                    events.onStateChanged(RokidConnectionState.ERROR, "企业版安全服务绑定失效")
                }
            },
        )
    }

    private fun registerServices() {
        try {
            GlassSdk.getGlassMessageService()?.setMessageListener(messageListener)
            GlassSdk.getGlassMediaService()?.addPhotoCallback(photoCallback)
            registered = true
            events.onStateChanged(RokidConnectionState.READY)
        } catch (error: RemoteException) {
            Log.e(TAG, "Register Enterprise Glass services failed", error)
            events.onStateChanged(RokidConnectionState.ERROR, error.message.orEmpty())
        }
    }

    override fun sendReport(message: String): Boolean {
        if (!registered) return false
        return try {
            val service = GlassSdk.getGlassMessageService() ?: return false
            runCatching { service.sendTextMessageByP2P(message) }
                .recoverCatching { service.sendTextMessageByClassicBT(message) }
                .getOrThrow()
            true
        } catch (error: Exception) {
            Log.e(TAG, "Enterprise report failed", error)
            false
        }
    }

    override fun capturePhoto(outputFile: File, callback: (Result<File>) -> Unit): Boolean {
        if (!registered) {
            callback(Result.failure(IllegalStateException("企业版眼镜服务未就绪")))
            return true
        }
        outputFile.parentFile?.mkdirs()
        pendingPhotos[outputFile.absolutePath] = callback
        return try {
            GlassSdk.getGlassMediaService()?.takePhoto(
                PhotoResolution.RESOLUTION_1080P_Land,
                outputFile.absolutePath,
            ) ?: throw IllegalStateException("企业版媒体服务不可用")
            true
        } catch (error: Exception) {
            pendingPhotos.remove(outputFile.absolutePath)
            callback(Result.failure(error))
            true
        }
    }

    private fun completePhoto(path: String?) {
        val normalized = path?.takeIf(String::isNotBlank)
        if (normalized == null) {
            pendingPhotos.entries.firstOrNull()?.let { (key, callback) ->
                pendingPhotos.remove(key)
                callback(Result.failure(IllegalStateException("企业版媒体服务未返回照片路径")))
            }
            return
        }
        val direct = pendingPhotos.remove(normalized)
        val fallback = if (direct == null && pendingPhotos.size == 1) {
            val entry = pendingPhotos.entries.first()
            pendingPhotos.remove(entry.key)
            entry.value
        } else null
        (direct ?: fallback)?.invoke(Result.success(File(normalized)))
    }

    override fun release() {
        runCatching {
            GlassSdk.getGlassMessageService()?.removeMessageListener(messageListener)
            GlassSdk.getGlassMediaService()?.removePhotoCallback(photoCallback)
        }
        pendingPhotos.forEach { (_, callback) ->
            callback(Result.failure(IllegalStateException("企业版 SDK 已释放")))
        }
        pendingPhotos.clear()
        GlassSdk.release()
        registered = false
    }
}
