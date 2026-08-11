<template>
  <a-modal
    v-model:open="open"
    title="调优答案"
    width="760px"
    ok-text="保存问答对"
    cancel-text="取消"
    :confirm-loading="saving"
    :ok-button-props="{ disabled: loading || !context }"
    @ok="save"
    @cancel="reset"
  >
    <div v-if="loading" class="loading-wrap"><a-spin /></div>
    <a-form v-else-if="context" layout="vertical">
      <a-alert
        type="info"
        show-icon
        message="保存后，同一智能体再次收到相同问题时将优先直接返回这条人工确认答案。"
        class="tip"
      />

      <a-form-item label="用户问题">
        <div class="readonly-block">{{ context.question }}</div>
      </a-form-item>

      <a-form-item label="原回答">
        <div class="readonly-block original-answer">{{ context.current_answer || '-' }}</div>
      </a-form-item>

      <a-form-item label="人工确认答案" required>
        <a-textarea
          v-model:value="answer"
          :rows="8"
          :maxlength="20000"
          show-count
          placeholder="编辑并确认正确答案"
        />
      </a-form-item>

      <div v-if="context.qa_pair" class="existing-tip">
        已存在人工问答对，本次保存会更新原答案；历史命中 {{ context.qa_pair.hit_count || 0 }} 次。
      </div>
    </a-form>
  </a-modal>
</template>

<script setup>
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { dashboardApi } from '@/apis/dashboard_api'

const emit = defineEmits(['saved'])
const open = ref(false)
const loading = ref(false)
const saving = ref(false)
const feedbackId = ref(null)
const context = ref(null)
const answer = ref('')

async function show(id) {
  feedbackId.value = id
  context.value = null
  answer.value = ''
  open.value = true
  loading.value = true
  try {
    const response = await dashboardApi.getFeedbackTuningContext(id)
    context.value = response.item
    answer.value = response.item?.qa_pair?.answer || response.item?.current_answer || ''
  } catch (error) {
    message.error(error?.message || '加载调优信息失败')
    open.value = false
  } finally {
    loading.value = false
  }
}

async function save() {
  const normalized = answer.value.trim()
  if (!normalized) {
    message.warning('请输入人工确认答案')
    return
  }

  saving.value = true
  try {
    const response = await dashboardApi.saveFeedbackQaPair(feedbackId.value, { answer: normalized })
    message.success('问答对已保存，后续相同问题将优先直接命中')
    open.value = false
    emit('saved', response.item)
    reset()
  } catch (error) {
    message.error(error?.message || '保存问答对失败')
  } finally {
    saving.value = false
  }
}

function reset() {
  feedbackId.value = null
  context.value = null
  answer.value = ''
}

defineExpose({ show })
</script>

<style scoped lang="less">
.loading-wrap {
  display: flex;
  justify-content: center;
  padding: 64px 0;
}
.tip { margin-bottom: 20px; }
.readonly-block {
  padding: 10px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-25);
  color: var(--gray-800);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.original-answer { max-height: 180px; overflow-y: auto; }
.existing-tip { color: var(--gray-600); font-size: 12px; }
</style>
