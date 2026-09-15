<template>
  <div class="page">
    <h2 class="page-title">商品列表</h2>

    <div class="toolbar">
      <el-input
        v-model="keyword"
        placeholder="搜索商品名称"
        clearable
        style="width: 260px"
        @keyup.enter="search"
        @clear="search"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-button type="primary" @click="search">搜索</el-button>
    </div>

    <el-table :data="products" v-loading="loading" border stripe>
      <el-table-column prop="name" label="商品" min-width="180" />
      <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
      <el-table-column label="价格" width="110" align="right">
        <template #default="{ row }">¥{{ Number(row.price).toFixed(2) }}</template>
      </el-table-column>
      <el-table-column label="库存" width="90" align="center">
        <template #default="{ row }">
          <el-tag v-if="row.stock === 0" type="danger" size="small">缺货</el-tag>
          <span v-else>{{ row.stock }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="status" label="状态" width="90" align="center">
        <template #default="{ row }">
          <el-tag :type="row.status === 'on' ? 'success' : 'info'" size="small">
            {{ row.status === 'on' ? '在售' : '下架' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="150" align="center">
        <template #default="{ row }">
          <el-button
            size="small"
            type="primary"
            :disabled="row.status !== 'on' || row.stock === 0"
            @click="addToCart(row)"
          >
            加入购物车
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination
        layout="prev, pager, next, total"
        :total="total"
        :page-size="size"
        :current-page="page"
        @current-change="onPage"
      />
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { listProducts } from '../api/products'
import { addToCart as apiAddToCart } from '../api/cart'

const products = ref([])
const total = ref(0)
const page = ref(1)
const size = 10
const keyword = ref('')
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    const data = await listProducts({ page: page.value, size, keyword: keyword.value || undefined })
    products.value = data.items
    total.value = data.total
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

function search() {
  // 新关键词从头翻页，避免停留在旧结果集的深层页码导致空页
  page.value = 1
  load()
}

function onPage(p) {
  page.value = p
  load()
}

async function addToCart(row) {
  try {
    await apiAddToCart(row.id, 1)
    ElMessage.success(`已加入购物车：${row.name}`)
  } catch (err) {
    ElMessage.error(err.message)
  }
}

onMounted(load)
</script>

<style scoped>
.toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}
.pager {
  display: flex;
  justify-content: center;
  margin-top: 16px;
}
</style>
