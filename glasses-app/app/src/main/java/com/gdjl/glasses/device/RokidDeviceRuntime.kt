package com.gdjl.glasses.device

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.util.Log
import java.io.File
import java.util.concurrent.CopyOnWriteArraySet
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Edition-neutral process singleton used by the business layer.
 *
 * It decouples all shared screens from Rokid SDK classes, so commercial and
 * enterprise SDKs are never packaged in the same APK.
 */
object RokidDeviceRuntime : RokidDeviceEvents {
    private const val TAG = "RokidDeviceRuntime"
    private val initialized = AtomicBoolean(false)
    private val listeners = CopyOnWriteArraySet<RokidDeviceEvents>()
    private val mainHandler = Handler(Looper.getMainLooper())
    private val bridge: RokidDeviceBridge by lazy { DeviceBridgeProvider.create() }
    @Volatile private var latestState = RokidConnectionState.DISCONNECTED
    @Volatile private var latestDetail = ""

    val edition: String get() = bridge.edition

    fun initialize(context: Context) {
        if (initialized.compareAndSet(false, true)) {
            runCatching { bridge.initialize(context.applicationContext, this) }
                .onFailure {
                    Log.e(TAG, "Rokid bridge initialization failed", it)
                    initialized.set(false)
                    onStateChanged(RokidConnectionState.ERROR, it.message.orEmpty())
                }
        }
    }

    fun addListener(listener: RokidDeviceEvents) {
        listeners += listener
        mainHandler.post { listener.onStateChanged(latestState, latestDetail) }
    }

    fun removeListener(listener: RokidDeviceEvents) {
        listeners -= listener
    }

    fun sendReport(message: String): Boolean =
        runCatching { bridge.sendReport(message) }.getOrElse {
            Log.e(TAG, "Rokid report failed", it)
            false
        }

    fun capturePhoto(outputFile: File, callback: (Result<File>) -> Unit): Boolean =
        runCatching { bridge.capturePhoto(outputFile, callback) }.getOrElse {
            Log.e(TAG, "Vendor capture failed", it)
            callback(Result.failure(it))
            true
        }

    override fun onStateChanged(state: RokidConnectionState, detail: String) {
        latestState = state
        latestDetail = detail
        mainHandler.post {
            listeners.forEach { it.onStateChanged(state, detail) }
        }
    }

    override fun onCommand(payload: String) {
        mainHandler.post {
            listeners.forEach { it.onCommand(payload) }
        }
    }

    override fun onKeyEvent(payload: String) {
        mainHandler.post {
            listeners.forEach { it.onKeyEvent(payload) }
        }
    }
}
