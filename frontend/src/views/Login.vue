<template>
  <div class="login-wrap">
    <el-card class="login-card">
      <h2 class="title">AI 电商平台</h2>
      <p class="subtitle">生产级电商业务 + AI 客服 / 经营分析智能体</p>

      <el-form :model="form" @submit.prevent="submit" size="large">
        <el-form-item>
          <el-input v-model="form.email" placeholder="邮箱" autocomplete="username" clearable />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            show-password
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            native-type="submit"
            :loading="loading"
            class="submit-btn"
          >
            {{ isRegister ? '注册并登录' : '登录' }}
          </el-button>
        </el-form-item>
      </el-form>

      <div class="switch-line">
        {{ isRegister ? '已有账号？' : '还没有账号？' }}
        <el-link type="primary" @click="isRegister = !isRegister">
          {{ isRegister ? '去登录' : '去注册' }}
        </el-link>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { login, register, me } from '../api/auth'
import { saveUser } from '../api/client'

const router = useRouter()
const isRegister = ref(false)
const loading = ref(false)
const form = reactive({ email: '', password: '' })

async function submit() {
  if (!form.email || !form.password) {
    ElMessage.warning('请输入邮箱和密码')
    return
  }
  loading.value = true
  try {
    // 登录/注册成功后会话 cookie 由后端 Set-Cookie 自动种下，前端不落 token
    if (isRegister.value) await register(form.email, form.password)
    else await login(form.email, form.password)
    const user = await me()
    saveUser(user)
    ElMessage.success(isRegister.value ? '注册成功' : '登录成功')
    router.push('/')
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #eef4ff 0%, #f6f9ff 100%);
}
.login-card {
  width: 380px;
  padding: 8px 12px 20px;
}
.title {
  text-align: center;
  margin: 8px 0 4px;
  font-size: 22px;
}
.subtitle {
  text-align: center;
  color: #909399;
  font-size: 13px;
  margin: 0 0 20px;
}
.submit-btn {
  width: 100%;
}
.switch-line {
  text-align: center;
  font-size: 13px;
  color: #606266;
}
</style>
