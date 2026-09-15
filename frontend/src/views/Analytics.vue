<template>
  <div class="page">
    <h2 class="page-title">经营分析智能体</h2>
    <p class="desc">
      多步工具调用：查销售汇总 → 计算环比/同比增长 → 畅销榜 → 结构化报告。
      每一步的工具调用都会在轨迹中展示。
    </p>
    <SseChat
      path="/analytics"
      placeholder="输入一个经营分析问题"
      empty-text="我是经营分析智能体，可以汇总销售额、算增长、找出畅销品"
      :examples="examples"
    />
  </div>
</template>

<script setup>
import SseChat from '../components/SseChat.vue'

const examples = [
  '按品类汇总最近 7 天的销售额，并计算环比增长',
  '哪个品类卖得最好？前 3 名是哪些商品？',
  '本月销售额比上月涨了多少？',
]
</script>

<style scoped>
.desc {
  color: #909399;
  font-size: 13px;
  margin: -8px 0 14px;
}
</style>
