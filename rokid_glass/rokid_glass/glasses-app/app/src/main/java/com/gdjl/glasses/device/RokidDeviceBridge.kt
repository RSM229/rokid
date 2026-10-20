package com.gdjl.glasses.device

import android.content.Context
import java.io.File

enum class RokidConnectionState {
    CONNECTING,
    READY,
    DISCONNECTED,
    ERROR,
}

interface RokidDeviceEvents {
    fun onStateChanged(state: RokidConnectionState, detail: String = "")
    fun onCommand(payload: String)
    fun onKeyEvent(payload: String)
}

interface RokidDeviceBridge {
    val edition: String
    fun initialize(context: Context, events: RokidDeviceEvents)
    fun sendReport(message: String): Boolean

    /**
     * Starts a vendor camera capture. Returns false when this edition does not
     * own the camera and the caller should fall back to CameraX.
     */
    fun capturePhoto(outputFile: File, callback: (Result<File>) -> Unit): Boolean

    fun release()
}

internal object DeviceBridgeProvider {
   // fun create(): RokidDeviceBridge = EditionDeviceBridge()
   fun create(): RokidDeviceBridge = object : RokidDeviceBridge {
        override val edition: String = "standard"
        override fun initialize(context: Context, events: RokidDeviceEvents) {}
        override fun sendReport(message: String): Boolean = true
        override fun capturePhoto(outputFile: File, callback: (Result<File>) -> Unit): Boolean = false
        override fun release() {}
    }
}
