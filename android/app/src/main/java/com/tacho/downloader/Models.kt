package com.tacho.downloader

import java.time.Instant

enum class Step {
    CARD,
    DOWNLOAD,
    EXTRACT,
    UPLOAD
}

enum class StepStatus {
    PENDING,
    ACTIVE,
    COMPLETE,
    ERROR
}

data class TripRecord(
    val date: String,
    val dayOfWeek: String,
    val vehicleRegistration: String,
    val cardInTime: String,
    val cardOutTime: String,
    val startMileage: Int,
    val endMileage: Int,
    val distanceKm: Int,
    val drivingHours: Double
)

data class DriverReport(
    val driverName: String,
    val cardNumber: String,
    val country: String,
    val cardExpiry: String,
    val downloadTimestamp: String,
    val reportPeriodDays: Int,
    val trips: List<TripRecord>,
    val totalDistanceKm: Int,
    val totalTrips: Int
)

data class ProgressInfo(
    val currentFile: String,
    val done: Int,
    val total: Int,
    val driverName: String?
)

data class DriverCardInfo(
    val name: String,
    val cardNumber: String,
    val expiry: String,
    val country: String
)

data class ResultInfo(
    val fileName: String,
    val fileSizeBytes: Int,
    val totalTrips: Int,
    val totalDistanceKm: Int,
    val reportDays: Int
)

data class AppState(
    val mainStatus: String = "AWAITING CARD INPUT",
    val steps: Map<Step, StepStatus> = defaultStepMap(),
    val progress: ProgressInfo? = null,
    val driverCard: DriverCardInfo? = null,
    val result: ResultInfo? = null,
    val webhookStatus: String? = null,
    val error: String? = null,
    val readerConnected: Boolean = false,
    val sendAllData: Boolean = false,
    val deviceName: String? = null,
    val lastUpdated: Instant = Instant.now()
) {
    companion object {
        fun defaultStepMap(): Map<Step, StepStatus> = mapOf(
            Step.CARD to StepStatus.PENDING,
            Step.DOWNLOAD to StepStatus.PENDING,
            Step.EXTRACT to StepStatus.PENDING,
            Step.UPLOAD to StepStatus.PENDING
        )
    }
}
