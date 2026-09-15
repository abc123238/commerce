<template>
  <div class="page">
    <h2 class="page-title">AI 客服</h2>
    <p class="desc">
      商品知识库 RAG 检索 + 工具调用打穿真实业务库（查订单 / 查库存 / 算优惠）。
      回答带 <code>[n]</code> 引用，可在「参考来源」中查看知识块。
    </p>
    <SseChat
      path="/chat"
      placeholder="问点什么，比如：我的订单到哪里了？"
      empty-text="我是 AI 客服，可以帮你查订单、看商品、算优惠"
      :examples="examples"
    />
  </div>
</template>

<script setup>
import SseChat from '../components/SseChat.vue'

const examples = [
  '我的订单到哪里了？',
  '有没有降噪耳机？多少钱？',
  '买了 7 天内可以退换吗？',
]
</script>

<style scoped>
.desc {
  color: #909399;
  font-size: 13px;
  margin: -8px 0 14px;
}
.desc code {
  background: #f0f2f5;
  padding: 1px 5px;
  border-radius: 4px;
}
</style>
