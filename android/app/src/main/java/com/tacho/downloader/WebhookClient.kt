package com.tacho.downloader

import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object WebhookClient {
    data class Result(val ok: Boolean, val message: String)

    fun send(report: DriverReport): Result {
        val payload = buildJson(report)
        val jsonBytes = payload.toString().toByteArray(Charsets.UTF_8)

        val postResult = tryPost(jsonBytes)
        if (postResult.ok) {
            return postResult
        }

        if (postResult.message.startsWith("HTTP 404")) {
            return tryGet(jsonBytes)
        }

        return postResult
    }

    private fun tryPost(jsonBytes: ByteArray): Result {
        return try {
            val url = URL(WEBHOOK_URL)
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "POST"
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
                setRequestProperty("User-Agent", "TachoDownloader/1.0")
                connectTimeout = 15000
                readTimeout = 15000
            }
            conn.outputStream.use { it.write(jsonBytes) }
            val code = conn.responseCode
            val ok = code in 200..299
            Result(ok, if (ok) "POST OK ($code)" else "HTTP $code")
        } catch (e: Exception) {
            Result(false, e.message ?: "POST failed")
        }
    }

    private fun tryGet(jsonBytes: ByteArray): Result {
        return try {
            val encoded = Base64.encodeToString(jsonBytes, Base64.URL_SAFE or Base64.NO_WRAP)
            val url = URL("$WEBHOOK_URL?data=$encoded")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("User-Agent", "TachoDownloader/1.0")
                connectTimeout = 15000
                readTimeout = 15000
            }
            val code = conn.responseCode
            val ok = code in 200..299
            Result(ok, if (ok) "GET OK ($code)" else "HTTP $code")
        } catch (e: Exception) {
            Result(false, e.message ?: "GET failed")
        }
    }

    private fun buildJson(report: DriverReport): JSONObject {
        val root = JSONObject()
        root.put("driver_name", report.driverName)
        root.put("card_number", report.cardNumber)
        root.put("country", report.country)
        root.put("card_expiry", report.cardExpiry)
        root.put("download_timestamp", report.downloadTimestamp)
        root.put("report_period_days", report.reportPeriodDays)
        root.put("total_distance_km", report.totalDistanceKm)
        root.put("total_trips", report.totalTrips)

        val trips = JSONArray()
        report.trips.forEach { trip ->
            val obj = JSONObject()
            obj.put("date", trip.date)
            obj.put("day_of_week", trip.dayOfWeek)
            obj.put("vehicle_registration", trip.vehicleRegistration)
            obj.put("card_in_time", trip.cardInTime)
            obj.put("card_out_time", trip.cardOutTime)
            obj.put("start_mileage", trip.startMileage)
            obj.put("end_mileage", trip.endMileage)
            obj.put("distance_km", trip.distanceKm)
            obj.put("driving_hours", trip.drivingHours)
            trips.put(obj)
        }

        root.put("trips", trips)
        return root
    }
}
