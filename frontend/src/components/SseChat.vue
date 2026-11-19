<template>
  <div class="chat">
    <div ref="scroller" class="messages">
      <div v-if="!messages.length" class="empty">
        <el-empty :description="emptyText">
          <div class="examples">
            <el-tag
              v-for="(ex, i) in examples"
              :key="i"
              class="example"
              effect="plain"
              @click="send(ex)"
            >
              {{ ex }}
            </el-tag>
          </div>
        </el-empty>
      </div>

      <div v-for="(m, i) in messages" :key="i" class="msg-wrap" :class="m.role">
        <div class="msg" :class="m.role">
          <div class="bubble">
            <!-- 用户消息 -->
            <template v-if="m.role === 'user'">{{ m.content }}</template>
            <!-- assistant：步骤轨迹 + 打字机正文 + 引用 -->
            <template v-else>
              <el-timeline v-if="m.steps.length" class="steps">
                <el-timeline-item
                  v-for="(s, j) in m.steps"
                  :key="j"
                  :timestamp="nodeLabel(s.node)"
                  :type="nodeType(s.node)"
                >
                  {{ s.detail }}
                </el-timeline-item>
              </el-timeline>

              <div v-if="m.running && !m.content" class="thinking">
                <el-icon class="is-loading"><Loading /></el-icon> 智能体思考中…
              </div>
              <div v-else class="md-body" v-html="renderMd(m.rendered)" />

              <div v-if="m.citations.length" class="citations">
                <el-collapse accordion>
                  <el-collapse-item title="参考来源" name="refs">
                    <div v-for="(c, k) in m.citations" :key="k" class="cite">
                      <span class="cite-index">[{{ c.index }}]</span>
                      <div class="cite-body">
                        <div class="cite-title">{{ c.title || c.source }}</div>
                        <div class="cite-text">{{ c.text }}</div>
                      </div>
                    </div>
                  </el-collapse-item>
                </el-collapse>
              </div>

              <div v-if="m.traceId" class="trace">
                trace_id：<code>{{ m.traceId }}</code>
              </div>
            </template>
          </div>
        </div>
      </div>
    </div>

    <div class="input">
      <el-input
        v-model="question"
        :placeholder="placeholder"
        :disabled="running"
        clearable
        @keyup.enter="send(question)"
      >
        <template #append>
          <el-button :loading="running" @click="send(question)">发送</el-button>
        </template>
      </el-input>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { streamSse } from '../api/sse'

const props = defineProps({
  path: { type: String, required: true }, // '/chat' | '/analytics'
  placeholder: { type: String, default: '输入问题，回车发送' },
  emptyText: { type: String, default: '开始对话吧' },
  examples: { type: Array, default: () => [] },
})

const messages = reactive([])
const question = ref('')
const running = ref(false)
const scroller = ref(null)

function nodeLabel(node) {
  return {
    route: '意图识别',
    rewrite: '查询改写',
    retrieve: '知识检索',
    reason: 'LLM 推理',
    execute_tool: '工具调用',
    answer: '生成回答',
  }[node] || node
}

function nodeType(node) {
  return {
    route: 'primary',
    rewrite: 'primary',
    retrieve: 'success',
    reason: 'warning',
    execute_tool: 'danger',
    answer: 'success',
  }[node] || 'primary'
}

function renderMd(text) {
  if (!text) return ''
  // marked 产物为不可信 HTML（LLM/知识库文本可被污染），用 DOMPurify 白名单清洗：
  // 默认策略会剥掉所有 on* 事件属性、javascript: 协议与危险标签（iframe/script/object 等）。
  return DOMPurify.sanitize(marked.parse(text, { gfm: true, breaks: true }))
}

// 打字机：拿到完整 content 后逐步渲染，模拟流式观感
function startTyping(msg) {
  let i = 0
  msg.rendered = ''
  if (msg._typingTimer) clearInterval(msg._typingTimer)
  msg._typingTimer = setInterval(() => {
    i += 2
    msg.rendered = msg.content.slice(0, i)
    if (i >= msg.content.length) {
      clearInterval(msg._typingTimer)
      msg._typingTimer = null
    }
  }, 16)
}

function send(text) {
  const q = (text ?? '').trim()
  if (!q || running.value) return

  messages.push({ role: 'user', content: q })
  const msg = reactive({
    role: 'assistant',
    content: '',
    rendered: '',
    steps: [],
    citations: [],
    traceId: '',
    running: true,
  })
  messages.push(msg)
  running.value = true

  // history = 当前问题之前的完整对话轮次（去掉刚加的 user + assistant 占位）
  const history = messages
    .slice(0, -2)
    .filter((m) => m.content)
    .map((m) => ({ role: m.role, content: m.content }))

  msg.cancel = streamSse(props.path, { question: q, history }, {
    onStep(step) {
      msg.steps.push({ node: step.node, detail: step.detail })
    },
    onAnswer(ans) {
      msg.content = ans.content || ''
      msg.citations = ans.citations || []
      startTyping(msg)
    },
    onDone(done) {
      msg.traceId = done.trace_id
      msg.running = false
      running.value = false
    },
    onError(message) {
      msg.content = `⚠ ${message || '服务处理失败'}`
      msg.running = false
      running.value = false
    },
  })
}

// 组件卸载时终止未完成的请求并清理打字机定时器，避免僵尸请求/泄漏
onUnmounted(() => {
  for (const m of messages) {
    if (m._typingTimer) clearInterval(m._typingTimer)
    if (m.cancel) m.cancel()
  }
})

watch(
  () => messages.map((m) => m.rendered).join(''),
  async () => {
    await nextTick()
    if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
  },
)
</script>

<style scoped>
.chat {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 220px);
  min-height: 420px;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px;
}
.empty {
  margin-top: 60px;
}
.examples {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
  margin-top: 12px;
}
.example {
  cursor: pointer;
}
.msg-wrap {
  display: flex;
  margin-bottom: 14px;
}
.msg-wrap.user {
  justify-content: flex-end;
}
.msg {
  max-width: 82%;
}
.bubble {
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.7;
  background: #f0f2f5;
  color: #303133;
}
.user .bubble {
  background: #409eff;
  color: #fff;
}
.steps {
  margin-bottom: 10px;
}
.thinking {
  color: #909399;
  display: flex;
  align-items: center;
  gap: 6px;
}
.citations {
  margin-top: 10px;
  border-top: 1px dashed #e4e7ed;
  padding-top: 8px;
}
.cite {
  display: flex;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px solid #f0f2f5;
}
.cite-index {
  color: #409eff;
  font-weight: 600;
}
.cite-title {
  font-weight: 600;
  font-size: 13px;
}
.cite-text {
  font-size: 12px;
  color: #909399;
  margin-top: 2px;
}
.trace {
  margin-top: 8px;
  font-size: 12px;
  color: #c0c4cc;
}
.trace code {
  font-size: 12px;
}
.input {
  border-top: 1px solid #e4e7ed;
  padding: 12px;
}
</style>
