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
          <span v-if="row.status === 'paid'" class="hint">等待完成</span>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="还没有订单">
          <el-button type="primary" @click="$router.push('/')">去逛商品</el-button>
        </el-empty>
      </template>
    </el-table>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listOrders, payOrder, cancelOrder } from '../api/orders'

const orders = ref([])
const loading = ref(false)

function statusMeta(status) {
  const map = {
    created: { text: '已创建', type: 'info' },
    paid: { text: '已支付', type: 'success' },
    completed: { text: '已完成', type: 'success' },
    cancelled: { text: '已取消', type: 'danger' },
  }
  return map[status] || { text: status, type: 'info' }
}

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
.hint {
  font-size: 13px;
  color: #909399;
}
</style>
