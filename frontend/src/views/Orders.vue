<template>
  <div class="page">
    <h2 class="page-title">我的订单</h2>

    <el-table :data="orders" v-loading="loading" border stripe>
      <el-table-column prop="order_no" label="订单号" width="200" />
      <el-table-column label="状态" width="120" align="center">
        <template #default="{ row }">
          <el-tag :type="statusMeta(row.status).type" size="small">
            {{ statusMeta(row.status).text }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="金额" width="120" align="right">
        <template #default="{ row }">¥{{ Number(row.total).toFixed(2) }}</template>
      </el-table-column>
      <el-table-column prop="created_at" label="下单时间" width="190" />
      <el-table-column label="商品" min-width="200">
        <template #default="{ row }">
          <div v-for="item in row.items" :key="item.product_id" class="item-line">
            {{ item.product_name }} × {{ item.quantity }}
          </div>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" align="center">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'created'"
            size="small"
            type="success"
            @click="pay(row)"
          >
            模拟支付
          </el-button>
          <el-button
            v-if="row.status === 'created'"
            size="small"
            type="danger"
            text
            @click="cancel(row)"
          >
            取消
          </el-button>
          <el-button
            v-if="['paid', 'shipped', 'completed'].includes(row.status)"
            size="small"
            text
            type="primary"
            @click="openTracking(row)"
          >
            查看物流
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="还没有订单">
          <el-button type="primary" @click="$router.push('/')">去逛商品</el-button>
        </el-empty>
      </template>
    </el-table>

    <el-drawer
      v-model="trackingVisible"
      :title="`物流轨迹 ${trackingOrderNo}`"
      size="420px"
      @closed="closeTracking"
    >
      <div v-loading="trackingLoading" class="tracking-body">
        <template v-if="tracking">
          <div class="tracking-meta">
            <div>承运商：{{ tracking.carrier || '待发货' }}</div>
            <div>运单号：{{ tracking.tracking_no || '发货后生成' }}</div>
          </div>
          <el-timeline v-if="reversedNodes.length">
            <el-timeline-item
              v-for="node in reversedNodes"
              :key="node.name"
              :type="node.reached ? 'success' : 'info'"
              :hollow="!node.reached"
              :timestamp="formatTime(node.time)"
              placement="top"
            >
              <span :class="{ 'node-pending': !node.reached }">{{ node.name }}</span>
            </el-timeline-item>
          </el-timeline>
        </template>
        <el-empty v-else-if="!trackingLoading" description="该订单暂无物流信息" />
        <p class="tracking-tip">
          模拟物流：支付后约 2 分钟揽收、10 分钟内送达；打开本页每 30 秒自动刷新。
        </p>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listOrders, payOrder, cancelOrder, getTracking } from '../api/orders'

const orders = ref([])
const loading = ref(false)

const trackingVisible = ref(false)
const trackingLoading = ref(false)
const trackingOrderNo = ref('')
const tracking = ref(null)

function statusMeta(status) {
  const map = {
    created: { text: '已创建', type: 'info' },
    paid: { text: '已支付', type: 'success' },
    shipped: { text: '已发货', type: 'primary' },
    completed: { text: '已完成', type: 'success' },
    cancelled: { text: '已取消', type: 'danger' },
  }
  return map[status] || { text: status, type: 'info' }
}

// 最新的节点排最上，符合真实物流 App 的阅读顺序
const reversedNodes = computed(() => [...(tracking.value?.nodes || [])].reverse())

function formatTime(t) {
  const d = new Date(t)
  return Number.isNaN(d.getTime()) ? t : d.toLocaleString('zh-CN', { hour12: false })
}

let timer = null

async function loadTracking() {
  trackingLoading.value = true
  try {
    tracking.value = await getTracking(trackingOrderNo.value)
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    trackingLoading.value = false
  }
}

function openTracking(row) {
  trackingOrderNo.value = row.order_no
  tracking.value = null
  trackingVisible.value = true
  loadTracking()
  // 演示时物流会随时间前进，定时刷新即可看到轨迹自动推进
  timer = setInterval(loadTracking, 30000)
}

function closeTracking() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
  tracking.value = null
  load() // 查询物流会推进订单状态，关抽屉时同步列表
}

onUnmounted(() => {
  if (timer) clearInterval(timer)
})

async function load() {
  loading.value = true
  try {
    const data = await listOrders()
    orders.value = data.items
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

async function pay(row) {
  try {
    await payOrder(row.order_no)
    ElMessage.success('支付成功')
    load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function cancel(row) {
  try {
    await ElMessageBox.confirm('确认取消该订单？', '取消订单', { type: 'warning' })
  } catch {
    return // 用户取消对话框
  }
  try {
    await cancelOrder(row.order_no)
    ElMessage.success('已取消')
    load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

onMounted(load)
</script>

<style scoped>
.item-line {
  font-size: 13px;
  color: #606266;
}
.tracking-body {
  min-height: 200px;
}
.tracking-meta {
  margin-bottom: 16px;
  font-size: 13px;
  color: #606266;
  line-height: 1.8;
}
.node-pending {
  color: #909399;
}
.tracking-tip {
  margin-top: 16px;
  font-size: 12px;
  color: #909399;
}
</style>
