<template>
  <el-container class="app-shell">
    <el-header v-if="showNav" class="app-header">
      <div class="logo">AI 电商平台</div>
      <el-menu
        :default-active="route.path"
        mode="horizontal"
        router
        :ellipsis="false"
        class="nav-menu"
      >
        <el-menu-item index="/">商品</el-menu-item>
        <el-menu-item index="/cart">购物车</el-menu-item>
        <el-menu-item index="/orders">订单</el-menu-item>
        <el-menu-item index="/chat">AI 客服</el-menu-item>
        <el-menu-item v-if="user?.role === 'admin'" index="/analytics">经营分析</el-menu-item>
      </el-menu>
      <div class="user-box">
        <el-tag v-if="user?.role" :type="user.role === 'admin' ? 'danger' : 'info'" size="small">
          {{ user.role === 'admin' ? '管理员' : '顾客' }}
        </el-tag>
        <span class="email">{{ user?.email }}</span>
        <el-button size="small" @click="logout">退出</el-button>
      </div>
    </el-header>
    <el-main>
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { logout as apiLogout, probeAuth, readUser } from './api/client'
import { ElMessage } from 'element-plus'

const route = useRoute()
const router = useRouter()
const showNav = computed(() => route.name !== 'login')
const user = computed(() => readUser())

onMounted(async () => {
  // 会话在 HttpOnly cookie 中：无本地 user 时探测 /auth/me 校验并缓存；失败即登出
  if (user.value) return
  const u = await probeAuth()
  if (!u) router.push('/login')
})

async function logout() {
  await apiLogout()
  ElMessage.success('已退出登录')
  router.push('/login')
}
</script>

<style scoped>
.app-shell {
  height: 100%;
}
.app-header {
  display: flex;
  align-items: center;
  gap: 24px;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
  padding: 0 24px;
}
.logo {
  font-weight: 700;
  font-size: 17px;
  color: #303133;
  white-space: nowrap;
}
.nav-menu {
  flex: 1;
  border-bottom: none;
}
.user-box {
  display: flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
}
.email {
  font-size: 13px;
  color: #606266;
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
