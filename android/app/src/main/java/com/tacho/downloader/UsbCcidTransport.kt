package com.tacho.downloader

import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import java.io.Closeable

private const val CCID_CLASS = 0x0b
private const val CCID_POWER_ON = 0x62
private const val CCID_POWER_OFF = 0x63
private const val CCID_GET_SLOT_STATUS = 0x65
private const val CCID_XFR_BLOCK = 0x6f

private const val CCID_DATA_BLOCK = 0x80
private const val CCID_SLOT_STATUS = 0x81
private const val ICC_STATUS_PRESENT_ACTIVE = 0x00
private const val ICC_STATUS_PRESENT_INACTIVE = 0x01
private const val ICC_STATUS_ABSENT = 0x02

class UsbCcidTransport(
    private val usbManager: UsbManager,
    private val device: UsbDevice
) : Closeable {

    private val connection: UsbDeviceConnection
    private val ccidInterface: UsbInterface
    private val endpointIn: UsbEndpoint
    private val endpointOut: UsbEndpoint
    private val endpointInterrupt: UsbEndpoint?
    private var seq: Byte = 0

    init {
        ccidInterface = findCcidInterface(device)
            ?: throw IllegalStateException("No CCID interface found on device")

        var epIn: UsbEndpoint? = null
        var epOut: UsbEndpoint? = null
        var epInt: UsbEndpoint? = null

        for (i in 0 until ccidInterface.endpointCount) {
            val ep = ccidInterface.getEndpoint(i)
            when (ep.type) {
                UsbConstants.USB_ENDPOINT_XFER_BULK -> {
                    if (ep.direction == UsbConstants.USB_DIR_IN) {
                        epIn = ep
                    } else if (ep.direction == UsbConstants.USB_DIR_OUT) {
                        epOut = ep
                    }
                }
                UsbConstants.USB_ENDPOINT_XFER_INT -> {
                    epInt = ep
                }
            }
        }

        if (epIn == null || epOut == null) {
            throw IllegalStateException("CCID bulk endpoints not found")
        }

        endpointIn = epIn
        endpointOut = epOut
        endpointInterrupt = epInt

        connection = usbManager.openDevice(device)
            ?: throw IllegalStateException("Failed to open USB device")
        if (!connection.claimInterface(ccidInterface, true)) {
            connection.close()
            throw IllegalStateException("Failed to claim CCID interface")
        }
    }

    fun powerOn(timeoutMs: Int = 4000): ByteArray {
        val response = sendCommand(CCID_POWER_ON, byteArrayOf(), 0, 0, 0, timeoutMs)
        if (response.messageType != CCID_DATA_BLOCK) {
            throw IllegalStateException("Unexpected CCID response: ${response.messageType}")
        }
        ensureOk(response)
        return response.data
    }

    fun powerOff(timeoutMs: Int = 2000) {
        val response = sendCommand(CCID_POWER_OFF, byteArrayOf(), 0, 0, 0, timeoutMs)
        if (response.messageType != CCID_SLOT_STATUS) {
            throw IllegalStateException("Unexpected CCID response: ${response.messageType}")
        }
    }

    fun getSlotStatus(timeoutMs: Int = 2000) {
        val response = sendCommand(CCID_GET_SLOT_STATUS, byteArrayOf(), 0, 0, 0, timeoutMs)
        if (response.messageType != CCID_SLOT_STATUS) {
            throw IllegalStateException("Unexpected CCID response: ${response.messageType}")
        }
        ensureOk(response)
    }

    fun readSlotStatus(timeoutMs: Int = 2000): SlotStatus {
        val response = sendCommand(CCID_GET_SLOT_STATUS, byteArrayOf(), 0, 0, 0, timeoutMs)
        if (response.messageType != CCID_SLOT_STATUS) {
            throw IllegalStateException("Unexpected CCID response: ${response.messageType}")
        }
        ensureOk(response)
        val iccStatus = response.status and 0x03
        return SlotStatus(
            iccStatus = iccStatus,
            commandStatus = (response.status and 0xC0) shr 6,
            error = response.error
        )
    }

    fun isCardPresent(status: SlotStatus): Boolean {
        return status.iccStatus == ICC_STATUS_PRESENT_ACTIVE ||
            status.iccStatus == ICC_STATUS_PRESENT_INACTIVE
    }

    fun isCardInactive(status: SlotStatus): Boolean {
        return status.iccStatus == ICC_STATUS_PRESENT_INACTIVE
    }

    fun isCardAbsent(status: SlotStatus): Boolean {
        return status.iccStatus == ICC_STATUS_ABSENT
    }

    fun transmit(apdu: ByteArray, timeoutMs: Int = 4000): ByteArray {
        val response = sendCommand(CCID_XFR_BLOCK, apdu, 0, 0, 0, timeoutMs)
        if (response.messageType != CCID_DATA_BLOCK) {
            throw IllegalStateException("Unexpected CCID response: ${response.messageType}")
        }
        ensureOk(response)
        return response.data
    }

    private fun sendCommand(
        messageType: Int,
        payload: ByteArray,
        param0: Int,
        param1: Int,
        param2: Int,
        timeoutMs: Int
    ): CcidResponse {
        val header = ByteArray(10)
        header[0] = messageType.toByte()
        writeUInt32Le(payload.size, header, 1)
        header[5] = 0 // slot 0
        header[6] = seq
        header[7] = param0.toByte()
        header[8] = param1.toByte()
        header[9] = param2.toByte()

        seq = (seq + 1).toByte()

        val out = ByteArray(header.size + payload.size)
        System.arraycopy(header, 0, out, 0, header.size)
        System.arraycopy(payload, 0, out, header.size, payload.size)

        val sent = connection.bulkTransfer(endpointOut, out, out.size, timeoutMs)
        if (sent != out.size) {
            throw IllegalStateException("USB write failed: $sent/${out.size}")
        }

        var response = readResponse(timeoutMs)
        var tries = 0
        while ((response.status and 0xC0) == 0x80 && tries < 3) {
            response = readResponse(timeoutMs)
            tries += 1
        }
        return response
    }

    private fun readResponse(timeoutMs: Int): CcidResponse {
        val buffer = ByteArray(8192)
        val read = connection.bulkTransfer(endpointIn, buffer, buffer.size, timeoutMs)
        if (read < 10) {
            throw IllegalStateException("USB read failed: $read")
        }

        val length = readUInt32Le(buffer, 1)
        val total = 10 + length
        val full = if (read >= total) {
            buffer.copyOf(read)
        } else {
            val temp = ByteArray(total)
            System.arraycopy(buffer, 0, temp, 0, read)
            var offset = read
            while (offset < total) {
                val remaining = total - offset
                val chunk = ByteArray(remaining)
                val got = connection.bulkTransfer(endpointIn, chunk, chunk.size, timeoutMs)
                if (got <= 0) {
                    break
                }
                System.arraycopy(chunk, 0, temp, offset, got)
                offset += got
            }
            temp
        }

        val messageType = full[0].toInt() and 0xff
        val status = full[7].toInt() and 0xff
        val error = full[8].toInt() and 0xff
        val dataLength = readUInt32Le(full, 1)
        val data = if (dataLength > 0) {
            full.copyOfRange(10, 10 + dataLength)
        } else {
            byteArrayOf()
        }

        return CcidResponse(messageType, status, error, data)
    }

    private fun ensureOk(response: CcidResponse) {
        val status = response.status
        val statusBits = status and 0xC0
        if (statusBits != 0x00 || response.error != 0x00) {
            throw IllegalStateException("CCID error status=0x${status.toString(16)} error=0x${response.error.toString(16)}")
        }
    }

    override fun close() {
        try {
            connection.releaseInterface(ccidInterface)
        } catch (_: Exception) {
        }
        connection.close()
    }

    private fun findCcidInterface(device: UsbDevice): UsbInterface? {
        for (i in 0 until device.interfaceCount) {
            val iface = device.getInterface(i)
            if (iface.interfaceClass == CCID_CLASS) {
                return iface
            }
        }
        return null
    }

    private fun writeUInt32Le(value: Int, buffer: ByteArray, offset: Int) {
        buffer[offset] = (value and 0xff).toByte()
        buffer[offset + 1] = ((value shr 8) and 0xff).toByte()
        buffer[offset + 2] = ((value shr 16) and 0xff).toByte()
        buffer[offset + 3] = ((value shr 24) and 0xff).toByte()
    }

    private fun readUInt32Le(buffer: ByteArray, offset: Int): Int {
        return (buffer[offset].toInt() and 0xff) or
            ((buffer[offset + 1].toInt() and 0xff) shl 8) or
            ((buffer[offset + 2].toInt() and 0xff) shl 16) or
            ((buffer[offset + 3].toInt() and 0xff) shl 24)
    }
}

data class CcidResponse(
    val messageType: Int,
    val status: Int,
    val error: Int,
    val data: ByteArray
)

data class SlotStatus(
    val iccStatus: Int,
    val commandStatus: Int,
    val error: Int
)
