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
import com.gdjl.phone.data.HazardRecordManager
import com.gdjl.phone.ui.theme.RenewCXRLSampleTheme

class HazardRecordsActivity : ComponentActivity() {
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            RenewCXRLSampleTheme {
                HazardRecordsScreen()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HazardRecordsScreen() {
    val records = remember { HazardRecordManager.getAllRecords() }
    val pendingCount = records.count { it.status == "待处理" }
    
    Scaffold(
        topBar = {
            TopAppBar(
                title = { 
                    Column {
                        Text("🔴 隐患记录", fontWeight = FontWeight.Bold)
                        Text("待处理: $pendingCount 条", fontSize = 12.sp)
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.errorContainer
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
                    Text("🔍", fontSize = 64.sp)
                    Text("暂无隐患记录", fontSize = 18.sp, fontWeight = FontWeight.Bold)
                    Text("眼镜端上传的隐患将显示在这里", fontSize = 14.sp)
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
                    HazardRecordCard(record)
                }
            }
        }
    }
}

@Composable
fun HazardRecordCard(record: HazardRecordManager.HazardRecord) {
    val statusColor = when (record.status) {
        "待处理" -> Color(0xFFFF9800)
        "处理中" -> Color(0xFF2196F3)
        "已闭环" -> Color(0xFF4CAF50)
        else -> Color.Gray
    }
    
    val levelColor = when (record.riskLevel) {
        "重大" -> Color(0xFFF44336)
        "高" -> Color(0xFFFF5722)
        "中" -> Color(0xFFFF9800)
        else -> Color(0xFF4CAF50)
    }
    
    Card(
        modifier = Modifier.fillMaxWidth(),
        elevation = CardDefaults.cardElevation(defaultElevation = 2.dp),
        colors = CardDefaults.cardColors(
            containerColor = if (record.status == "待处理") 
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
                    text = "⚠️ ${record.hazardName}",
                    fontSize = 16.sp,
                    fontWeight = FontWeight.Bold,
                    color = levelColor
                )
                Surface(
                    color = statusColor,
                    shape = MaterialTheme.shapes.small
                ) {
                    Text(
                        text = record.status,
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
                Text("风险: ${record.riskLevel}", fontSize = 13.sp)
                if (record.section.isNotEmpty()) {
                    Text("部位: ${record.section}", fontSize = 13.sp)
                }
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
