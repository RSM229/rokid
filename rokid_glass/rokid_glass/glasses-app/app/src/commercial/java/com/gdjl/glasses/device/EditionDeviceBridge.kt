package com.gdjl.glasses.device

import android.content.Context
import android.util.Log
import com.rokid.cxr.Caps
import com.rokid.cxr.CXRServiceBridge
import java.io.File
import java.nio.charset.StandardCharsets

/** Commercial CXR-S transport. */
internal class EditionDeviceBridge : RokidDeviceBridge {
    companion object {
        private const val TAG = "CommercialRokidBridge"
        private const val COMMAND_TOPIC = "gdjl.command"
        private const val KEY_TOPIC = "gdjl.key_event"
        private const val REPORT_TOPIC = "gdjl.report"
    }

    override val edition: String = "commercial"
    private var bridge: CXRServiceBridge? = null

    override fun initialize(context: Context, events: RokidDeviceEvents) {
        val service = CXRServiceBridge()
        bridge = service
        events.onStateChanged(RokidConnectionState.CONNECTING)
        service.subscribe(COMMAND_TOPIC, CXRServiceBridge.MsgCallback { _, caps, data ->
            decodePayload(caps, data).takeIf(String::isNotBlank)?.let(events::onCommand)
        })
        service.subscribe(KEY_TOPIC, CXRServiceBridge.MsgCallback { _, caps, data ->
            decodePayload(caps, data).takeIf(String::isNotBlank)?.let(events::onKeyEvent)
        })
        service.setStatusListener(object : CXRServiceBridge.StatusListener {
            override fun onConnected(mac: String?, name: String?, type: Int) {
                events.onStateChanged(RokidConnectionState.READY, name.orEmpty())
            }

            override fun onDisconnected() {
                events.onStateChanged(RokidConnectionState.DISCONNECTED)
            }

            override fun onConnecting(mac: String?, name: String?, type: Int) {
                events.onStateChanged(RokidConnectionState.CONNECTING, name.orEmpty())
            }

            override fun onARTCStatus(value: Float, connected: Boolean) {
                Log.d(TAG, "ARTC status value=$value connected=$connected")
            }

            override fun onRokidAccountChanged(account: String?) {
                Log.i(TAG, "Rokid account changed")
            }
        })
    }

    override fun sendReport(message: String): Boolean {
        val service = bridge ?: return false
        val caps = Caps().apply { write(message) }
        return service.sendMessage(REPORT_TOPIC, caps) >= 0
    }

    override fun capturePhoto(outputFile: File, callback: (Result<File>) -> Unit): Boolean = false

    override fun release() {
        bridge?.disconnectCXRDevice()
        bridge = null
    }

    private fun decodePayload(caps: Caps?, data: ByteArray?): String {
        if (data != null && data.isNotEmpty()) return String(data, StandardCharsets.UTF_8)
        return runCatching {
            if (caps != null && caps.size() > 0) caps.at(0).string else ""
        }.getOrDefault("")
    }
}
