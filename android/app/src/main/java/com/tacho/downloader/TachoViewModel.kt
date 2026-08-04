package com.tacho.downloader

import android.content.Context
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.io.File
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

class TachoViewModel : ViewModel() {
    private val _state = MutableStateFlow(AppState())
    val state: StateFlow<AppState> = _state
    private var downloadJob: Job? = null

    fun toggleAllData(enabled: Boolean) {
        _state.update { it.copy(sendAllData = enabled, lastUpdated = Instant.now()) }
    }

    fun setDeviceConnected(device: UsbDevice?) {
        _state.update {
            it.copy(
                readerConnected = device != null,
                deviceName = device?.productName ?: device?.deviceName,
                lastUpdated = Instant.now()
            )
        }
    }

    fun reset() {
        downloadJob?.cancel()
        downloadJob = null
        _state.value = AppState()
    }

    fun startDownload(context: Context, usbManager: UsbManager, device: UsbDevice) {
        if (downloadJob?.isActive == true) return
        downloadJob = viewModelScope.launch(Dispatchers.IO) {
            val deviceId = device.deviceId
            val deviceName = device.productName ?: device.deviceName
            try {
                resetForNextCard(deviceName)

                UsbCcidTransport(usbManager, device).use { transport ->
                    while (isDeviceConnected(usbManager, deviceId)) {
                        val hasCard = waitForCardPresent(transport, usbManager, deviceId)
                        if (!hasCard) break
                        try {
                            val allData = _state.value.sendAllData
                            val finalProgress = processCard(context, transport, allData)

                            _state.update {
                                it.copy(
                                    progress = finalProgress,
                                    mainStatus = "EXTRACTION COMPLETE - REMOVE CARD",
                                    lastUpdated = Instant.now()
                                )
                            }

                            waitForCardRemoval(transport, usbManager, deviceId)
                            resetForNextCard(deviceName)
                        } catch (e: Exception) {
                            updateStep(Step.DOWNLOAD, StepStatus.ERROR, "FAILED")
                            _state.update {
                                it.copy(
                                    error = e.message ?: "Unknown error",
                                    mainStatus = "ERROR - REMOVE CARD",
                                    lastUpdated = Instant.now()
                                )
                            }
                            waitForCardRemoval(transport, usbManager, deviceId)
                            resetForNextCard(deviceName)
                        }
                    }
                }
            } catch (e: Exception) {
                updateStep(Step.DOWNLOAD, StepStatus.ERROR, "FAILED")
                _state.update {
                    it.copy(
                        error = e.message ?: "Unknown error",
                        mainStatus = "ERROR - CHECK CONNECTION",
                        lastUpdated = Instant.now()
                    )
                }
            }
        }
    }

    private fun updateStep(step: Step, status: StepStatus, mainStatus: String) {
        _state.update { current ->
            val steps = current.steps.toMutableMap()
            steps[step] = status
            current.copy(
                steps = steps,
                mainStatus = mainStatus,
                lastUpdated = Instant.now()
            )
        }
    }

    private fun driverCard(reader: TachoReader): DriverCardInfo? {
        val name = reader.driverName ?: return null
        return DriverCardInfo(
            name = name,
            cardNumber = reader.cardNumber ?: "Unknown",
            expiry = reader.cardExpiry?.toLocalDate()?.toString() ?: "Unknown",
            country = reader.issuingCountry ?: "??"
        )
    }

    private fun saveDddFile(context: Context, reader: TachoReader, bytes: ByteArray): File {
        val outputDir = File(context.getExternalFilesDir(null), "downloads")
        outputDir.mkdirs()
        outputDir.listFiles { file -> file.extension.lowercase() == "ddd" }?.forEach { file ->
            file.delete()
        }

        val name = (reader.driverName ?: "driver")
            .lowercase()
            .map { if (it.isLetterOrDigit()) it else '_' }
            .joinToString("")
            .trim('_')
            .ifEmpty { "driver" }

        val timestamp = DateTimeFormatter.ofPattern("yyyy-MM-dd_HHmmss")
            .withZone(ZoneOffset.UTC)
            .format(Instant.now())

        val file = File(outputDir, "${name}_${timestamp}.ddd")
        file.writeBytes(bytes)
        return file
    }

    private fun resetForNextCard(deviceName: String?) {
        val steps = AppState.defaultStepMap().toMutableMap()
        steps[Step.CARD] = StepStatus.ACTIVE
        _state.update {
            it.copy(
                mainStatus = "WAITING FOR CARD",
                steps = steps,
                progress = null,
                driverCard = null,
                result = null,
                webhookStatus = null,
                error = null,
                readerConnected = true,
                deviceName = deviceName,
                lastUpdated = Instant.now()
            )
        }
    }

    private suspend fun waitForCardPresent(
        transport: UsbCcidTransport,
        usbManager: UsbManager,
        deviceId: Int
    ): Boolean {
        while (isDeviceConnected(usbManager, deviceId)) {
            val status = try {
                transport.readSlotStatus()
            } catch (_: Exception) {
                delay(300)
                continue
            }
            if (transport.isCardPresent(status)) {
                return true
            }
            delay(300)
        }
        return false
    }

    private suspend fun waitForCardRemoval(
        transport: UsbCcidTransport,
        usbManager: UsbManager,
        deviceId: Int
    ) {
        var consecutiveErrors = 0
        var inactiveProbeCounter = 0
        while (isDeviceConnected(usbManager, deviceId)) {
            val status = try {
                transport.readSlotStatus().also { consecutiveErrors = 0 }
            } catch (_: Exception) {
                consecutiveErrors += 1
                if (consecutiveErrors >= 3) {
                    return
                }
                delay(300)
                continue
            }
            if (transport.isCardAbsent(status)) {
                return
            }
            if (transport.isCardInactive(status)) {
                inactiveProbeCounter += 1
                if (inactiveProbeCounter % 5 == 0) {
                    val stillPresent = try {
                        transport.powerOn()
                        true
                    } catch (_: Exception) {
                        false
                    } finally {
                        try {
                            transport.powerOff()
                        } catch (_: Exception) {
                        }
                    }
                    if (!stillPresent) {
                        return
                    }
                }
            }
            delay(400)
        }
    }

    private fun isDeviceConnected(usbManager: UsbManager, deviceId: Int): Boolean {
        return usbManager.deviceList.values.any { it.deviceId == deviceId }
    }

    private fun processCard(
        context: Context,
        transport: UsbCcidTransport,
        allData: Boolean
    ): ProgressInfo {
        updateStep(Step.CARD, StepStatus.COMPLETE, "CARD LINKED")
        updateStep(Step.DOWNLOAD, StepStatus.ACTIVE, "ACQUIRING DATA STREAM...")

        transport.powerOn()

        val reader = TachoReader(transport)
        var lastProgress: ProgressInfo? = null
        val dddBytes = reader.download { name, done, total, driver ->
            val progress = ProgressInfo(name, done, total, driver)
            lastProgress = progress
            _state.update {
                it.copy(
                    progress = progress,
                    driverCard = driverCard(reader),
                    mainStatus = "ACQUIRING DATA STREAM...",
                    lastUpdated = Instant.now()
                )
            }
        }

        updateStep(Step.DOWNLOAD, StepStatus.COMPLETE, "DATA ACQUIRED")
        updateStep(Step.EXTRACT, StepStatus.ACTIVE, "EXTRACTING REPORT...")

        val outputFile = saveDddFile(context, reader, dddBytes)
        val report = TachoParser(dddBytes).getReport(allData)

        updateStep(Step.EXTRACT, StepStatus.COMPLETE, "REPORT READY")
        updateStep(Step.UPLOAD, StepStatus.ACTIVE, "TRANSMITTING TO N8N...")

        val webhook = WebhookClient.send(report)
        updateStep(
            Step.UPLOAD,
            if (webhook.ok) StepStatus.COMPLETE else StepStatus.ERROR,
            webhook.message
        )

        try {
            transport.powerOff()
        } catch (_: Exception) {
        }

        val finalProgress = lastProgress?.let {
            it.copy(currentFile = "COMPLETE", done = it.total)
        } ?: ProgressInfo("COMPLETE", 1, 1, reader.driverName)

        _state.update {
            it.copy(
                driverCard = driverCard(reader),
                result = ResultInfo(
                    fileName = outputFile.name,
                    fileSizeBytes = dddBytes.size,
                    totalTrips = report.totalTrips,
                    totalDistanceKm = report.totalDistanceKm,
                    reportDays = report.reportPeriodDays
                ),
                webhookStatus = webhook.message,
                progress = finalProgress,
                lastUpdated = Instant.now()
            )
        }

        return finalProgress
    }
}
