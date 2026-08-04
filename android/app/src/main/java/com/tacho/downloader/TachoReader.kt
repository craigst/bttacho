package com.tacho.downloader

import java.io.ByteArrayOutputStream
import java.nio.charset.Charset
import java.time.Instant
import java.time.ZoneOffset
import java.time.ZonedDateTime

class TachoReader(private val transport: UsbCcidTransport) {
    private val ddd = ByteArrayOutputStream()

    var driverName: String? = null
        private set
    var driverSurname: String? = null
        private set
    var driverFirstname: String? = null
        private set
    var cardNumber: String? = null
        private set
    var cardExpiry: ZonedDateTime? = null
        private set
    var issuingCountry: String? = null
        private set

    private var params = Params(0, 0, 0, 0, 0)

    fun download(progress: (String, Int, Int, String?) -> Unit): ByteArray {
        ddd.reset()
        var filesDone = 0
        val totalFiles = 16

        fun update(name: String) {
            filesDone += 1
            progress(name, filesDone, totalFiles, driverName)
        }

        update("EF_ICC")
        if (readFile(0x0002, 25, store = true, sign = false) == null) {
            throw IllegalStateException("Failed to read EF_ICC")
        }

        update("EF_IC")
        if (readFile(0x0005, 8, store = true, sign = false) == null) {
            throw IllegalStateException("Failed to read EF_IC")
        }

        val sw = select(0, byName = true)
        if (sw != 0x9000) {
            throw IllegalStateException("Failed to select Tachograph DF")
        }

        update("EF_Application_Identification")
        val appId = readFile(0x0501, 10, store = true, sign = true)
            ?: throw IllegalStateException("Failed to read EF_Application_Identification")

        params = Params(
            events = appId[3].toInt() and 0xff,
            faults = appId[4].toInt() and 0xff,
            activity = ((appId[5].toInt() and 0xff) shl 8) or (appId[6].toInt() and 0xff),
            vehicles = ((appId[7].toInt() and 0xff) shl 8) or (appId[8].toInt() and 0xff),
            places = appId[9].toInt() and 0xff
        )

        update("EF_Card_Certificate")
        readFile(0xC100, 194, store = true, sign = false)

        update("EF_CA_Certificate")
        readFile(0xC108, 194, store = true, sign = false)

        update("EF_Identification")
        val ident = readFile(0x0520, 143, store = true, sign = true)
        if (ident != null && ident.size >= 143) {
            issuingCountry = COUNTRIES[ident[0].toInt() and 0xff] ?: "??"
            val cardBytes = trimNullAndSpace(ident.copyOfRange(1, 17))
            cardNumber = cardBytes.toString(Charset.forName("US-ASCII")).trim()
            cardExpiry = parseTimestamp(ident.copyOfRange(61, 65))
            driverSurname = decodeName(ident.copyOfRange(65, 101))
            driverFirstname = decodeName(ident.copyOfRange(101, 137))
            driverName = listOfNotNull(driverSurname, driverFirstname)
                .joinToString(" ")
                .trim()
        }

        update("EF_Card_Download")
        readFile(0x050E, 4, store = true, sign = false)

        update("EF_Driving_Licence_Info")
        readFile(0x0521, 53, store = true, sign = true)

        update("EF_Events_Data")
        readFile(0x0502, params.events * 24 * 6, store = true, sign = true)

        update("EF_Faults_Data")
        readFile(0x0503, params.faults * 24 * 2, store = true, sign = true)

        update("EF_Driver_Activity_Data")
        readFile(0x0504, params.activity + 4, store = true, sign = true)

        update("EF_Vehicles_Used")
        readFile(0x0505, params.vehicles * 31 + 2, store = true, sign = true)

        update("EF_Places")
        readFile(0x0506, params.places * 10 + 1, store = true, sign = true)

        update("EF_Current_Usage")
        readFile(0x0507, 19, store = true, sign = true)

        update("EF_Control_Activity_Data")
        readFile(0x0508, 46, store = true, sign = true)

        update("EF_Specific_Conditions")
        readFile(0x0522, 280, store = true, sign = true)

        return ddd.toByteArray()
    }

    private fun select(fileId: Int, byName: Boolean): Int {
        val apdu = if (byName) {
            byteArrayOf(0x00, 0xA4.toByte(), 0x04, 0x0C, 0x06, 0xFF.toByte(), 0x54, 0x41, 0x43, 0x48, 0x4F)
        } else {
            byteArrayOf(
                0x00, 0xA4.toByte(), 0x02, 0x0C, 0x02,
                ((fileId shr 8) and 0xff).toByte(),
                (fileId and 0xff).toByte()
            )
        }
        val response = transport.transmit(apdu)
        return statusWord(response)
    }

    private fun readBinary(size: Int): ByteArray? {
        val out = ByteArrayOutputStream()
        var pos = 0
        while (pos < size) {
            val chunk = minOf(200, size - pos)
            val apdu = byteArrayOf(
                0x00, 0xB0.toByte(),
                ((pos shr 8) and 0xff).toByte(),
                (pos and 0xff).toByte(),
                chunk.toByte()
            )
            val response = transport.transmit(apdu)
            val sw = statusWord(response)
            if (sw != 0x9000) {
                return null
            }
            out.write(response.copyOfRange(0, response.size - 2))
            pos += chunk
        }
        return out.toByteArray()
    }

    private fun performHash(): Boolean {
        val apdu = byteArrayOf(0x80.toByte(), 0x2A, 0x90.toByte(), 0x00)
        val response = transport.transmit(apdu)
        return statusWord(response) == 0x9000
    }

    private fun computeSignature(): ByteArray? {
        val apdu = byteArrayOf(0x00, 0x2A, 0x9E.toByte(), 0x9A.toByte(), 0x80.toByte())
        val response = transport.transmit(apdu)
        val sw = statusWord(response)
        return if (sw == 0x9000) response.copyOfRange(0, response.size - 2) else null
    }

    private fun appendDdd(fid: Int, data: ByteArray, isSig: Boolean) {
        ddd.write((fid shr 8) and 0xff)
        ddd.write(fid and 0xff)
        ddd.write(if (isSig) 0x01 else 0x00)
        ddd.write((data.size shr 8) and 0xff)
        ddd.write(data.size and 0xff)
        ddd.write(data)
    }

    private fun readFile(fid: Int, size: Int, store: Boolean, sign: Boolean): ByteArray? {
        val sw = select(fid, byName = false)
        if (sw != 0x9000) {
            return null
        }
        if (sign) {
            performHash()
        }
        val data = readBinary(size) ?: return null
        if (store) {
            appendDdd(fid, data, isSig = false)
        }
        if (sign) {
            val sig = computeSignature()
            if (sig != null) {
                appendDdd(fid, sig, isSig = true)
            }
        }
        return data
    }

    private fun decodeName(raw: ByteArray): String {
        if (raw.size < 2) return ""
        val codepage = raw[0].toInt() and 0xff
        val text = raw.copyOfRange(1, raw.size)
        val trimmed = trimNullAndSpace(text)
        return decodeText(codepage, trimmed)
    }

    private fun decodeText(codepage: Int, bytes: ByteArray): String {
        return try {
            if (codepage == 0) return ""
            bytes.toString(Charset.forName("ISO-8859-$codepage")).trim()
        } catch (_: Exception) {
            bytes.toString(Charset.forName("US-ASCII")).trim()
        }
    }

    private fun trimNullAndSpace(bytes: ByteArray): ByteArray {
        var start = 0
        var end = bytes.size
        while (start < end && bytes[start] == 0.toByte()) {
            start += 1
        }
        while (end > start && (bytes[end - 1] == 0.toByte() || bytes[end - 1] == 0x20.toByte())) {
            end -= 1
        }
        return if (start == 0 && end == bytes.size) bytes else bytes.copyOfRange(start, end)
    }

    private fun parseTimestamp(data: ByteArray): ZonedDateTime? {
        if (data.size < 4) return null
        val ts = ((data[0].toInt() and 0xff) shl 24) or
            ((data[1].toInt() and 0xff) shl 16) or
            ((data[2].toInt() and 0xff) shl 8) or
            (data[3].toInt() and 0xff)
        if (ts == 0) return null
        return Instant.ofEpochSecond(ts.toLong()).atZone(ZoneOffset.UTC)
    }

    fun getDddBytes(): ByteArray = ddd.toByteArray()

    private fun statusWord(response: ByteArray): Int {
        if (response.size < 2) return 0
        val sw1 = response[response.size - 2].toInt() and 0xff
        val sw2 = response[response.size - 1].toInt() and 0xff
        return (sw1 shl 8) or sw2
    }

    data class Params(
        val events: Int,
        val faults: Int,
        val activity: Int,
        val vehicles: Int,
        val places: Int
    )
}

val COUNTRIES: Map<Int, String> = mapOf(
    0x00 to "---", 0x01 to "AT", 0x02 to "AL", 0x03 to "AD", 0x04 to "AM", 0x05 to "AZ",
    0x06 to "BE", 0x07 to "BG", 0x08 to "BA", 0x09 to "BY", 0x0A to "CH", 0x0B to "CY",
    0x0C to "CZ", 0x0D to "DE", 0x0E to "DK", 0x0F to "ES", 0x10 to "EE", 0x11 to "FR",
    0x12 to "FI", 0x13 to "LI", 0x14 to "FO", 0x15 to "GB", 0x16 to "GE", 0x17 to "GR",
    0x18 to "HU", 0x19 to "HR", 0x1A to "IT", 0x1B to "IE", 0x1C to "IS", 0x1D to "KZ",
    0x1E to "LU", 0x1F to "LT", 0x20 to "LV", 0x21 to "MT", 0x22 to "MC", 0x23 to "MD",
    0x24 to "MK", 0x25 to "NO", 0x26 to "NL", 0x27 to "PT", 0x28 to "PL", 0x29 to "RO",
    0x2A to "SM", 0x2B to "RU", 0x2C to "SE", 0x2D to "SK", 0x2E to "SI", 0x2F to "TM",
    0x30 to "TR", 0x31 to "UA", 0x32 to "VA", 0xFD to "EU", 0xFE to "EUR", 0xFF to "WLD"
)
