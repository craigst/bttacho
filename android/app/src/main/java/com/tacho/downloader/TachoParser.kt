package com.tacho.downloader

import java.nio.charset.Charset
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.time.format.TextStyle
import java.util.Locale

class TachoParser(private val raw: ByteArray) {
    private val records: Map<Int, ByteArray> = parseRecords(raw)

    private val driverName: String
    private val cardNumber: String
    private val issuingCountry: String
    private val cardExpiry: String
    private val vehicles: List<VehicleRecord>

    init {
        val ident = records[0x0520]
        if (ident != null && ident.size >= 143) {
            issuingCountry = COUNTRIES[ident[0].toInt() and 0xff] ?: "??"
            val cardRaw = trimNullAndSpace(ident.copyOfRange(1, 17))
            cardNumber = cardRaw.toString(Charset.forName("US-ASCII")).trim()
            cardExpiry = parseTimestamp(ident.copyOfRange(61, 65))?.toLocalDate()?.toString() ?: "Unknown"
            val surname = decodeText(ident.copyOfRange(65, 101))
            val firstname = decodeText(ident.copyOfRange(101, 137))
            driverName = listOfNotNull(surname, firstname).joinToString(" ").trim().ifEmpty { "Unknown" }
        } else {
            issuingCountry = "??"
            cardNumber = "Unknown"
            cardExpiry = "Unknown"
            driverName = "Unknown"
        }

        vehicles = parseVehicles(records[0x0505])
    }

    fun getReport(allData: Boolean): DriverReport {
        val cutoff = if (allData) null else ZonedDateTime.now(ZoneOffset.UTC).minusDays(14)
        val trips = vehicles
            .filter { it.firstUse != null && it.registration.isNotBlank() }
            .filter { cutoff == null || it.firstUse!!.isAfter(cutoff) || it.firstUse!!.isEqual(cutoff) }
            .mapNotNull { vehicle ->
                val firstUse = vehicle.firstUse
                val lastUse = vehicle.lastUse
                if (firstUse == null) return@mapNotNull null

                val drivingHours = if (lastUse != null) {
                    val delta = lastUse.toEpochSecond() - firstUse.toEpochSecond()
                    (delta / 3600.0 * 100).toInt() / 100.0
                } else {
                    0.0
                }

                TripRecord(
                    date = firstUse.toLocalDate().format(DateTimeFormatter.ISO_LOCAL_DATE),
                    dayOfWeek = firstUse.dayOfWeek.getDisplayName(TextStyle.FULL, Locale.ENGLISH),
                    vehicleRegistration = vehicle.registration,
                    cardInTime = firstUse.toLocalTime().format(DateTimeFormatter.ofPattern("HH:mm")),
                    cardOutTime = lastUse?.toLocalTime()?.format(DateTimeFormatter.ofPattern("HH:mm")) ?: "--:--",
                    startMileage = vehicle.odometerBegin,
                    endMileage = vehicle.odometerEnd,
                    distanceKm = vehicle.distance,
                    drivingHours = drivingHours
                )
            }
            .sortedByDescending { it.date }

        val totalDistance = trips.sumOf { it.distanceKm }
        val daysSpan = computeDaysSpan(trips, allData)

        return DriverReport(
            driverName = driverName,
            cardNumber = cardNumber,
            country = issuingCountry,
            cardExpiry = cardExpiry,
            downloadTimestamp = Instant.now().toString(),
            reportPeriodDays = daysSpan,
            trips = trips,
            totalDistanceKm = totalDistance,
            totalTrips = trips.size
        )
    }

    private fun computeDaysSpan(trips: List<TripRecord>, allData: Boolean): Int {
        if (trips.isEmpty()) {
            return if (allData) 0 else 14
        }
        val dates = trips.map { LocalDate.parse(it.date) }
        val oldest = dates.minOrNull() ?: return if (allData) 0 else 14
        val newest = dates.maxOrNull() ?: return if (allData) 0 else 14
        return kotlin.math.max(1, (newest.toEpochDay() - oldest.toEpochDay()).toInt() + 1)
    }

    private fun parseRecords(raw: ByteArray): Map<Int, ByteArray> {
        val data = mutableMapOf<Int, ByteArray>()
        var pos = 0
        while (pos + 5 <= raw.size) {
            val fid = ((raw[pos].toInt() and 0xff) shl 8) or (raw[pos + 1].toInt() and 0xff)
            val recType = raw[pos + 2].toInt() and 0xff
            val length = ((raw[pos + 3].toInt() and 0xff) shl 8) or (raw[pos + 4].toInt() and 0xff)
            pos += 5
            if (pos + length > raw.size) {
                break
            }
            val recordData = raw.copyOfRange(pos, pos + length)
            pos += length
            if (recType == 0) {
                data[fid] = recordData
            }
        }
        return data
    }

    private fun parseVehicles(data: ByteArray?): List<VehicleRecord> {
        if (data == null || data.size < 2) return emptyList()
        val vehicles = mutableListOf<VehicleRecord>()
        var pos = 2
        while (pos + 31 <= data.size) {
            val rec = data.copyOfRange(pos, pos + 31)
            val odometerBegin = parseOdometer(rec.copyOfRange(0, 3))
            val odometerEnd = parseOdometer(rec.copyOfRange(3, 6))
            val firstUse = parseTimestamp(rec.copyOfRange(6, 10))
            val lastUse = parseTimestamp(rec.copyOfRange(10, 14))
            val registration = decodeRegistration(rec.copyOfRange(15, 29))

            vehicles.add(
                VehicleRecord(
                    registration = registration,
                    firstUse = firstUse,
                    lastUse = lastUse,
                    odometerBegin = odometerBegin,
                    odometerEnd = odometerEnd
                )
            )

            pos += 31
        }
        return vehicles
    }

    private fun parseOdometer(data: ByteArray): Int {
        if (data.size < 3) return 0
        return ((data[0].toInt() and 0xff) shl 16) or
            ((data[1].toInt() and 0xff) shl 8) or
            (data[2].toInt() and 0xff)
    }

    private fun decodeRegistration(data: ByteArray): String {
        if (data.isEmpty()) return ""
        val codepage = data[0].toInt() and 0xff
        val text = data.copyOfRange(1, data.size)
        return decodeTextWithCodepage(codepage, text)
    }

    private fun decodeText(data: ByteArray): String {
        if (data.isEmpty()) return ""
        val codepage = data[0].toInt() and 0xff
        val text = data.copyOfRange(1, data.size)
        return decodeTextWithCodepage(codepage, text)
    }

    private fun decodeTextWithCodepage(codepage: Int, data: ByteArray): String {
        val trimmed = trimNullAndSpace(data)
        return try {
            if (codepage == 0) trimmed.toString(Charset.forName("US-ASCII")).trim()
            else trimmed.toString(Charset.forName("ISO-8859-$codepage")).trim()
        } catch (_: Exception) {
            trimmed.toString(Charset.forName("US-ASCII")).trim()
        }
    }

    private fun trimNullAndSpace(data: ByteArray): ByteArray {
        var start = 0
        var end = data.size
        while (start < end && data[start] == 0.toByte()) {
            start += 1
        }
        while (end > start && (data[end - 1] == 0.toByte() || data[end - 1] == 0x20.toByte())) {
            end -= 1
        }
        return if (start == 0 && end == data.size) data else data.copyOfRange(start, end)
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

    data class VehicleRecord(
        val registration: String,
        val firstUse: ZonedDateTime?,
        val lastUse: ZonedDateTime?,
        val odometerBegin: Int,
        val odometerEnd: Int
    ) {
        val distance: Int
            get() = odometerEnd - odometerBegin
    }
}
