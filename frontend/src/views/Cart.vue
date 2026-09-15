<template>
  <div class="page">
    <h2 class="page-title">购物车</h2>

    <el-table :data="items" v-loading="loading" border stripe>
      <el-table-column prop="name" label="商品" min-width="200" />
      <el-table-column label="单价" width="110" align="right">
        <template #default="{ row }">¥{{ Number(row.unit_price).toFixed(2) }}</template>
      </el-table-column>
      <el-table-column label="数量" width="160" align="center">
        <template #default="{ row }">
          <el-input-number
            :model-value="row.quantity"
            :min="1"
            :max="99"
            :disabled="pending.has(row.product_id)"
            size="small"
            @change="(v) => update(row, v)"
          />
        </template>
      </el-table-column>
      <el-table-column label="小计" width="120" align="right">
        <template #default="{ row }">¥{{ Number(row.subtotal).toFixed(2) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" align="center">
        <template #default="{ row }">
          <el-button size="small" type="danger" text @click="remove(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="购物车还是空的，去挑点商品吧">
          <el-button type="primary" @click="$router.push('/')">去逛商品</el-button>
        </el-empty>
      </template>
    </el-table>

    <div v-if="items.length" class="checkout">
      <span class="total-label">
        合计：<span class="total-amount">¥{{ Number(total).toFixed(2) }}</span>
      </span>
      <el-button type="primary" size="large" :loading="ordering" @click="checkout">
        去结算下单
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getCart, updateCartItem, removeCartItem } from '../api/cart'
import { createOrder } from '../api/orders'

const router = useRouter()
const items = ref([])
const total = ref('0')
const loading = ref(false)
const ordering = ref(false)
const pending = ref(new Set()) // 请求进行中的商品行，避免快速加减导致乱序覆盖

async function load() {
  loading.value = true
  try {
    const data = await getCart()
    items.value = data.items
    total.value = data.total
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

async function update(row, quantity) {
  if (pending.value.has(row.product_id)) return
  pending.value.add(row.product_id)
  try {
    await updateCartItem(row.product_id, quantity)
    await load()
  } catch (err) {
    ElMessage.error(err.message)
    load()
  } finally {
    pending.value.delete(row.product_id)
  }
}

async function remove(row) {
  try {
    await removeCartItem(row.product_id)
    ElMessage.success('已删除')
    load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

async function checkout() {
  try {
    await ElMessageBox.confirm('确认从购物车生成订单并支付？', '下单确认', { type: 'info' })
  } catch {
    return // 用户取消对话框
  }
  ordering.value = true
  try {
    const order = await createOrder()
    ElMessage.success(`订单创建成功：${order.order_no}`)
    router.push('/orders')
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    ordering.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.checkout {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 16px;
  margin-top: 16px;
  padding: 12px 16px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}
.total-label {
  font-size: 15px;
}
.total-amount {
  color: #f56c6c;
  font-size: 20px;
  font-weight: 700;
}
</style>
