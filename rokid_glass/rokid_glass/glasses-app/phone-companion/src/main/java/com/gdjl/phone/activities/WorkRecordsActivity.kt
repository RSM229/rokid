package com.gdjl.phone.activities

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.gdjl.phone.data.WorkRecordManager
import com.gdjl.phone.ui.theme.RenewCXRLSampleTheme

class WorkRecordsActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            RenewCXRLSampleTheme {
                WorkRecordsScreen()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WorkRecordsScreen() {
    val records = remember { WorkRecordManager.getAllRecords() }
    val pendingCount = records.count { it.workType == "待定" }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text("📋 工作记录", fontWeight = FontWeight.Bold)
                        Text("待定: $pendingCount 条", fontSize = 12.sp)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer
                )
            )
        }
    ) { paddingValues ->
        if (records.isEmpty()) {
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(paddingValues),
                contentAlignment = Alignment.Center
            ) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("📷", fontSize = 64.sp)
                    Text("暂无工作记录", fontSize = 18.sp, fontWeight = FontWeight.Bold)
                    Text("眼镜端上传的工作记录将显示在这里", fontSize = 14.sp)
                }
            }
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(paddingValues)
                    .padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                items(records) { record ->
                    WorkRecordCard(record)
                }
            }
        }
    }
}

@Composable
fun WorkRecordCard(record: WorkRecordManager.WorkRecord) {
    val workTypeColors = mapOf(
        "待定" to Color(0xFFFF9800),
        "巡视" to Color(0xFF4CAF50),
        "验收" to Color(0xFF2196F3),
        "旁站" to Color(0xFFFF5722),
        "见证" to Color(0xFF9C27B0),
        "质量" to Color(0xFFE91E63),
        "其他工作" to Color(0xFF607D8B)
    )

    val color = workTypeColors[record.workType] ?: Color.Gray

    Card(
        modifier = Modifier.fillMaxWidth(),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (record.workType == "待定")
                MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.3f)
            else MaterialTheme.colorScheme.surface
        )
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(12.dp)
        ) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically
            ) {
                Text(
                    text = record.title,
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold
                )
                Surface(
                    color = color,
                    shape = MaterialTheme.shapes.small
                ) {
                    Text(
                        text = record.workType,
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
                        fontSize = 12.sp,
                        color = Color.White
                    )
                }
            }

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text("📷 ${record.photoCount}张", fontSize = 13.sp)
                Text("🎙️ ${record.audioCount}段", fontSize = 13.sp)
            }

            Text(
                text = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm", java.util.Locale.getDefault())
                    .format(java.util.Date(record.timestamp)),
                fontSize = 12.sp,
                color = MaterialTheme.colorScheme.onSurfaceVariant
            )
        }
    }
}
