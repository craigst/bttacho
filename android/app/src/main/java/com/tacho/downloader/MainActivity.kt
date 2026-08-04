package com.tacho.downloader

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.windowsizeclass.ExperimentalMaterial3WindowSizeClassApi
import androidx.compose.material3.windowsizeclass.WindowWidthSizeClass
import androidx.compose.material3.windowsizeclass.calculateWindowSizeClass
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.window.layout.FoldingFeature
import androidx.window.layout.WindowInfoTracker
import androidx.window.layout.WindowLayoutInfo
import kotlinx.coroutines.flow.collectLatest

private const val ACTION_USB_PERMISSION = "com.tacho.downloader.USB_PERMISSION"

class MainActivity : ComponentActivity() {
    private val viewModel: TachoViewModel by viewModels()
    private lateinit var usbManager: UsbManager
    private lateinit var usbReceiver: BroadcastReceiver

    @OptIn(ExperimentalMaterial3WindowSizeClassApi::class)
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        usbManager = getSystemService(Context.USB_SERVICE) as UsbManager

        val permissionIntent = PendingIntent.getBroadcast(
            this,
            0,
            Intent(ACTION_USB_PERMISSION),
            PendingIntent.FLAG_IMMUTABLE
        )

        usbReceiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                when (intent.action) {
                    ACTION_USB_PERMISSION -> {
                        val device = extractUsbDevice(intent)
                        val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
                        if (device != null && granted) {
                            viewModel.setDeviceConnected(device)
                            viewModel.startDownload(context, usbManager, device)
                        }
                    }
                    UsbManager.ACTION_USB_DEVICE_ATTACHED -> {
                        val device = extractUsbDevice(intent)
                        if (device != null && isTargetReader(device)) {
                            requestPermission(device, permissionIntent)
                        }
                    }
                    UsbManager.ACTION_USB_DEVICE_DETACHED -> {
                        val device = extractUsbDevice(intent)
                        if (device != null && isTargetReader(device)) {
                            viewModel.reset()
                        }
                    }
                }
            }
        }

        val filter = IntentFilter().apply {
            addAction(ACTION_USB_PERMISSION)
            addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
            addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
        }
        ContextCompat.registerReceiver(this, usbReceiver, filter, ContextCompat.RECEIVER_NOT_EXPORTED)

        handleIntent(intent, permissionIntent)

        setContent {
            val state by viewModel.state.collectAsState()
            val windowSizeClass = calculateWindowSizeClass(this)
            val foldInfo = rememberFoldInfo(this)

            Surface(color = Color(0xFF0A0F1A)) {
                TachoScreen(
                    state = state,
                    windowWidth = windowSizeClass.widthSizeClass,
                    foldingFeature = foldInfo,
                    onToggleAllData = viewModel::toggleAllData
                )
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent, PendingIntent.getBroadcast(this, 0, Intent(ACTION_USB_PERMISSION), PendingIntent.FLAG_IMMUTABLE))
    }

    override fun onDestroy() {
        super.onDestroy()
        unregisterReceiver(usbReceiver)
    }

    private fun handleIntent(intent: Intent, permissionIntent: PendingIntent) {
        if (intent.action == UsbManager.ACTION_USB_DEVICE_ATTACHED) {
            val device = extractUsbDevice(intent)
            if (device != null && isTargetReader(device)) {
                requestPermission(device, permissionIntent)
                return
            }
        }

        val existing = findReader()
        if (existing != null) {
            viewModel.setDeviceConnected(existing)
            if (usbManager.hasPermission(existing)) {
                viewModel.startDownload(this, usbManager, existing)
            } else {
                requestPermission(existing, permissionIntent)
            }
        }
    }

    private fun requestPermission(device: UsbDevice, permissionIntent: PendingIntent) {
        if (usbManager.hasPermission(device)) {
            viewModel.startDownload(this, usbManager, device)
        } else {
            usbManager.requestPermission(device, permissionIntent)
        }
    }

    private fun findReader(): UsbDevice? {
        return usbManager.deviceList.values.firstOrNull { isTargetReader(it) }
    }

    private fun extractUsbDevice(intent: Intent): UsbDevice? {
        return if (Build.VERSION.SDK_INT >= 33) {
            intent.getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
        } else {
            @Suppress("DEPRECATION")
            intent.getParcelableExtra(UsbManager.EXTRA_DEVICE)
        }
    }

    private fun isTargetReader(device: UsbDevice): Boolean {
        for (i in 0 until device.interfaceCount) {
            val iface = device.getInterface(i)
            if (iface.interfaceClass == USB_CCID_CLASS) {
                return true
            }
        }
        return false
    }
}

@OptIn(ExperimentalMaterial3WindowSizeClassApi::class)
@Composable
private fun TachoScreen(
    state: AppState,
    windowWidth: WindowWidthSizeClass,
    foldingFeature: FoldingFeature?,
    onToggleAllData: (Boolean) -> Unit
) {
    val scrollState = rememberScrollState()
    val padding = when (windowWidth) {
        WindowWidthSizeClass.Compact -> 12.dp
        WindowWidthSizeClass.Medium -> 20.dp
        WindowWidthSizeClass.Expanded -> 28.dp
        else -> 16.dp
    }

    val hingeWidth = rememberHingeWidth(foldingFeature)

    BoxWithConstraints(
        modifier = Modifier
            .fillMaxSize()
            .background(
                Brush.linearGradient(
                    listOf(Color(0xFF0A0F1A), Color(0xFF121A2E))
                )
            )
            .systemBarsPadding()
            .padding(padding)
    ) {
        val isExpanded = windowWidth == WindowWidthSizeClass.Expanded
        val isNarrow = maxWidth < 360.dp

        if (isExpanded) {
            Row(
                modifier = Modifier.fillMaxSize(),
                horizontalArrangement = Arrangement.spacedBy(20.dp)
            ) {
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .verticalScroll(scrollState),
                    verticalArrangement = Arrangement.spacedBy(16.dp)
                ) {
                    HeaderSection(isNarrow)
                    StatusCore(state.mainStatus, isNarrow)
                    SciFiProgressBar(state.progress, isNarrow)
                    ToggleSection(state.sendAllData, onToggleAllData)
                    StepsSection(state.steps)
                }

                if (hingeWidth > 0.dp) {
                    Spacer(modifier = Modifier.width(hingeWidth))
                }

                Column(
                    modifier = Modifier
                        .weight(1f)
                        .verticalScroll(scrollState),
                    verticalArrangement = Arrangement.spacedBy(16.dp)
                ) {
                    DriverCardSection(state.driverCard)
                    ResultSection(state.result, state.webhookStatus)
                    ErrorSection(state.error)
                }
            }
        } else {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .verticalScroll(scrollState),
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                HeaderSection(isNarrow)
                StatusCore(state.mainStatus, isNarrow)
                SciFiProgressBar(state.progress, isNarrow)
                ToggleSection(state.sendAllData, onToggleAllData)
                StepsSection(state.steps)
                DriverCardSection(state.driverCard)
                ResultSection(state.result, state.webhookStatus)
                ErrorSection(state.error)
            }
        }
    }
}

@Composable
private fun HeaderSection(compact: Boolean) {
    Column(
        modifier = Modifier.fillMaxWidth(),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(
            text = "TACHOGRAPH CARD DOWNLOADER",
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Bold,
            fontSize = if (compact) 16.sp else 18.sp,
            color = Color(0xFF7BE9FF),
            textAlign = TextAlign.Center
        )
        Spacer(modifier = Modifier.height(8.dp))
        Box(
            modifier = Modifier
                .height(2.dp)
                .fillMaxWidth(0.75f)
                .background(
                    Brush.horizontalGradient(
                        listOf(Color.Transparent, Color(0xFF00D4FF), Color.Transparent)
                    )
                )
        )
    }
}

@Composable
private fun StatusCore(status: String, compact: Boolean) {
    val infinite = rememberInfiniteTransition(label = "pulse")
    val pulse by infinite.animateFloat(
        initialValue = 0.6f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(1200, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Reverse
        ),
        label = "pulseAlpha"
    )

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RectangleShape)
            .border(1.dp, Color(0xFF1F2A3A))
            .padding(16.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Canvas(modifier = Modifier.size(if (compact) 80.dp else 90.dp)) {
            val radius = size.minDimension / 2f
            drawCircle(Color(0xFF0E1A28))
            drawCircle(Color(0xFF00D4FF).copy(alpha = 0.25f * pulse), radius = radius)
            drawCircle(Color(0xFF00D4FF).copy(alpha = 0.6f * pulse), radius = radius * 0.4f)
            drawCircle(Color(0xFF0C111B), radius = radius * 0.18f)
        }
        Spacer(modifier = Modifier.height(12.dp))
        Text(
            text = status,
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Bold,
            fontSize = if (compact) 12.sp else 14.sp,
            color = Color(0xFF00A8CC),
            textAlign = TextAlign.Center
        )
    }
}

@Composable
private fun SciFiProgressBar(progress: ProgressInfo?, compact: Boolean) {
    val infinite = rememberInfiniteTransition(label = "scan")
    val scan by infinite.animateFloat(
        initialValue = 0f,
        targetValue = 1f,
        animationSpec = infiniteRepeatable(
            animation = tween(1500, easing = FastOutSlowInEasing),
            repeatMode = RepeatMode.Restart
        ),
        label = "scanPos"
    )

    val fraction = if (progress == null || progress.total == 0) 0f else progress.done / progress.total.toFloat()

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, Color(0xFF1F2A3A))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp)
    ) {
        Text(
            text = "DATA ACQUISITION",
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Bold,
            fontSize = 12.sp,
            color = Color(0xFF7BE9FF)
        )

        Canvas(modifier = Modifier
            .fillMaxWidth()
            .height(if (compact) 20.dp else 24.dp)
        ) {
            val corner = 6.dp.toPx()
            drawRoundRect(
                color = Color(0xFF0E1522),
                cornerRadius = androidx.compose.ui.geometry.CornerRadius(corner, corner)
            )

            if (fraction > 0f) {
                val barWidth = size.width * fraction
                val gradient = Brush.horizontalGradient(
                    listOf(Color(0xFF008FB3), Color(0xFF00D4FF), Color(0xFF66F0FF)),
                    startX = 0f,
                    endX = barWidth
                )
                drawRoundRect(
                    brush = gradient,
                    size = androidx.compose.ui.geometry.Size(barWidth, size.height),
                    cornerRadius = androidx.compose.ui.geometry.CornerRadius(corner, corner)
                )

                val scanX = barWidth * scan
                drawRect(
                    color = Color.White.copy(alpha = 0.35f),
                    topLeft = androidx.compose.ui.geometry.Offset(scanX - 12f, 0f),
                    size = androidx.compose.ui.geometry.Size(24f, size.height)
                )
            }

            val segmentColor = Color(0xFF133249)
            val segments = 12
            val segmentWidth = size.width / segments
            for (i in 1 until segments) {
                val x = i * segmentWidth
                drawLine(segmentColor, androidx.compose.ui.geometry.Offset(x, 0f), androidx.compose.ui.geometry.Offset(x, size.height))
            }
        }

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween
        ) {
            Text(
                text = progress?.currentFile ?: "WAITING",
                fontSize = 11.sp,
                color = Color(0xFF6B8AA5),
                fontFamily = FontFamily.Monospace
            )
            Text(
                text = if (progress == null) "0%" else "${(fraction * 100).toInt()}%",
                fontSize = 11.sp,
                color = Color(0xFFD8F5FF),
                fontFamily = FontFamily.Monospace
            )
        }
    }
}

@Composable
private fun ToggleSection(enabled: Boolean, onToggle: (Boolean) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, Color(0xFF1F2A3A))
            .padding(horizontal = 16.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(
                text = "FULL DATA EXTRACTION",
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                color = Color(0xFF7090B0)
            )
            Text(
                text = "Send all card history instead of 14 days",
                fontSize = 11.sp,
                color = Color(0xFF506070)
            )
        }
        Switch(
            checked = enabled,
            onCheckedChange = onToggle,
            colors = SwitchDefaults.colors(
                checkedThumbColor = Color(0xFF00D4FF),
                checkedTrackColor = Color(0xFF0E3C55),
                uncheckedThumbColor = Color(0xFF4A5568),
                uncheckedTrackColor = Color(0xFF1C2433)
            )
        )
    }
}

@Composable
private fun StepsSection(steps: Map<Step, StepStatus>) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, Color(0xFF1F2A3A))
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        StepRow(1, "CARD DETECTION", steps[Step.CARD] ?: StepStatus.PENDING)
        StepRow(2, "DATA ACQUISITION", steps[Step.DOWNLOAD] ?: StepStatus.PENDING)
        StepRow(3, "REPORT EXTRACTION", steps[Step.EXTRACT] ?: StepStatus.PENDING)
        StepRow(4, "N8N TRANSMISSION", steps[Step.UPLOAD] ?: StepStatus.PENDING)
    }
}

@Composable
private fun StepRow(number: Int, label: String, status: StepStatus) {
    val color = when (status) {
        StepStatus.PENDING -> Color(0xFF46566B)
        StepStatus.ACTIVE -> Color(0xFF00D4FF)
        StepStatus.COMPLETE -> Color(0xFF00FF88)
        StepStatus.ERROR -> Color(0xFFFF5A5A)
    }
    Row(
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Box(
            modifier = Modifier
                .size(28.dp)
                .border(1.dp, color, shape = MaterialTheme.shapes.small),
            contentAlignment = Alignment.Center
        ) {
            Text(
                text = number.toString(),
                fontSize = 12.sp,
                fontFamily = FontFamily.Monospace,
                color = color
            )
        }
        Text(
            text = label,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
            color = color,
            fontFamily = FontFamily.Monospace
        )
    }
}

@Composable
private fun DriverCardSection(card: DriverCardInfo?) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, Color(0xFF1F2A3A))
            .padding(16.dp)
    ) {
        Text(
            text = "DRIVER CARD",
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Bold,
            fontSize = 12.sp,
            color = Color(0xFF7BE9FF)
        )
        Spacer(modifier = Modifier.height(12.dp))
        if (card == null) {
            Text(
                text = "Insert card to display driver info",
                fontSize = 12.sp,
                color = Color(0xFF6B8AA5)
            )
        } else {
            Text(
                text = card.name,
                fontSize = 18.sp,
                fontWeight = FontWeight.Bold,
                color = Color(0xFF00FF88)
            )
            Spacer(modifier = Modifier.height(6.dp))
            InfoRow("Card", card.cardNumber)
            InfoRow("Expiry", card.expiry)
            InfoRow("Country", card.country)
        }
    }
}

@Composable
private fun ResultSection(result: ResultInfo?, webhookStatus: String?) {
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, Color(0xFF1F2A3A))
            .padding(16.dp)
    ) {
        Text(
            text = "RESULT SUMMARY",
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Bold,
            fontSize = 12.sp,
            color = Color(0xFF7BE9FF)
        )
        Spacer(modifier = Modifier.height(12.dp))
        if (result == null) {
            Text(
                text = "No extraction yet",
                fontSize = 12.sp,
                color = Color(0xFF6B8AA5)
            )
        } else {
            InfoRow("File", result.fileName)
            InfoRow("Size", "${result.fileSizeBytes} bytes")
            InfoRow("Trips", result.totalTrips.toString())
            InfoRow("Distance", "${result.totalDistanceKm} km")
            InfoRow("Days", result.reportDays.toString())
            if (!webhookStatus.isNullOrBlank()) {
                Spacer(modifier = Modifier.height(6.dp))
                Text(
                    text = webhookStatus,
                    fontSize = 12.sp,
                    color = Color(0xFF00D4FF)
                )
            }
        }
    }
}

@Composable
private fun ErrorSection(error: String?) {
    if (error.isNullOrBlank()) return
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .border(1.dp, Color(0xFFFF5A5A))
            .padding(16.dp)
    ) {
        Text(
            text = "ERROR",
            fontFamily = FontFamily.Monospace,
            fontWeight = FontWeight.Bold,
            fontSize = 12.sp,
            color = Color(0xFFFF5A5A)
        )
        Spacer(modifier = Modifier.height(8.dp))
        Text(
            text = error,
            fontSize = 12.sp,
            color = Color(0xFFFFB3B3)
        )
    }
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween
    ) {
        Text(
            text = label.uppercase(),
            fontSize = 11.sp,
            color = Color(0xFF6B8AA5),
            fontFamily = FontFamily.Monospace
        )
        Text(
            text = value,
            fontSize = 11.sp,
            color = Color(0xFFD8F5FF),
            fontFamily = FontFamily.Monospace
        )
    }
}

@Composable
private fun rememberFoldInfo(activity: Activity): FoldingFeature? {
    val tracker = remember { WindowInfoTracker.getOrCreate(activity) }
    var layoutInfo by remember { mutableStateOf<WindowLayoutInfo?>(null) }

    LaunchedEffect(tracker) {
        tracker.windowLayoutInfo(activity).collectLatest { info ->
            layoutInfo = info
        }
    }

    return layoutInfo?.displayFeatures?.filterIsInstance<FoldingFeature>()?.firstOrNull()
}

@Composable
private fun rememberHingeWidth(foldingFeature: FoldingFeature?): Dp {
    if (foldingFeature == null) return 0.dp
    val density = LocalDensity.current
    return with(density) { foldingFeature.bounds.width().toDp() }
}
