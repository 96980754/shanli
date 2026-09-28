<template>
  <a-modal
    v-model:open="open"
    :title="t('feedback.tuneAnswer')"
    width="760px"
    :ok-text="t('feedback.saveQaPair')"
    :cancel-text="t('common.cancel')"
    :confirm-loading="saving"
    :ok-button-props="{ disabled: loading || !context }"
    @ok="save"
    @cancel="reset"
  >
    <div v-if="loading" class="loading-wrap"><a-spin /></div>
    <div v-else-if="context">
      <div class="answer-hint">
        <Info :size="16" class="answer-hint-icon" />
        <div class="answer-hint-body">
          <p class="answer-hint-title">{{ t('feedback.saveTip') }}</p>
        </div>
      </div>

      <div v-if="isJsonQuestion" class="json-hint">
        <TriangleAlert :size="16" class="json-hint-icon" />
        <div class="answer-hint-body">
          <p class="answer-hint-desc">{{ t('feedback.jsonQuestionTip') }}</p>
        </div>
      </div>

      <div class="question-card">
        <div class="question-label">{{ t('feedback.userQuestionLabel') }}</div>
        <div class="question-text" :class="{ 'question-json': isJsonQuestion }">
          {{ displayQuestion }}
        </div>
      </div>

      <div class="question-card">
        <div class="question-label">{{ t('feedback.originalAnswerLabel') }}</div>
        <div class="question-text original-answer">{{ context.current_answer || '-' }}</div>
      </div>

      <div class="answer-field">
        <div class="field-label">
          {{ t('feedback.answerFieldLabel') }}<span class="field-required">*</span>
        </div>
        <a-textarea
          v-model:value="answer"
          :rows="8"
          :maxlength="20000"
          show-count
          :placeholder="t('feedback.answerPlaceholder')"
        />
      </div>

      <div v-if="context.qa_pair" class="existing-tip">
        <History :size="13" />
        <span>{{ t('feedback.existingQaTip', { count: context.qa_pair.hit_count || 0 }) }}</span>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
import { History, Info, TriangleAlert } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import { dashboardApi } from '@/apis/dashboard_api'

const { t } = useI18n()

const emit = defineEmits(['saved'])
const open = ref(false)
const loading = ref(false)
const saving = ref(false)
const feedbackId = ref(null)
const context = ref(null)
const answer = ref('')

// 结构化 JSON 指令（评测/联调产生）不适合作为面向终端用户的人工问答问题键，
// 识别后美化展示并提示；是否仍保存由运营决定。
const isJsonQuestion = computed(() => {
  const raw = context.value?.question
  if (!raw) return false
  const trimmed = raw.trim()
  if (!(trimmed.startsWith('{') || trimmed.startsWith('['))) return false
  try {
    JSON.parse(trimmed)
    return true
  } catch {
    return false
  }
})
const displayQuestion = computed(() => {
  const raw = context.value?.question || ''
  if (!isJsonQuestion.value) return raw
  try {
    return JSON.stringify(JSON.parse(raw.trim()), null, 2)
  } catch {
    return raw
  }
})

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
    message.error(error?.message || t('feedback.loadTuningFailed'))
    open.value = false
  } finally {
    loading.value = false
  }
}

async function save() {
  const normalized = answer.value.trim()
  if (!normalized) {
    message.warning(t('feedback.enterAnswerWarning'))
    return
  }

  saving.value = true
  try {
    const response = await dashboardApi.saveFeedbackQaPair(feedbackId.value, { answer: normalized })
    message.success(t('feedback.saveQaSuccess'))
    open.value = false
    emit('saved', response.item)
    reset()
  } catch (error) {
    message.error(error?.message || t('feedback.saveQaFailed'))
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

/* ---------- 调优答案弹窗（与「人工补答」保持同一版式） ---------- */
.answer-hint,
.json-hint {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
  padding: 12px 14px;
  border-radius: 8px;
}
.answer-hint {
  border: 1px solid var(--color-info-100);
  background: var(--color-info-50);
}
.json-hint {
  border: 1px solid var(--color-warning-100);
  background: var(--color-warning-50);
}
.answer-hint-icon {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--color-info-700);
}
.json-hint-icon {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--color-warning-900);
}
.answer-hint-body {
  min-width: 0;
}
.answer-hint-title {
  margin: 0;
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.6;
}
.answer-hint-desc {
  margin: 0;
  color: var(--gray-700);
  font-size: 12px;
  line-height: 1.6;
}

.question-card {
  margin-bottom: 16px;
  padding: 12px 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);
}
.question-label {
  margin-bottom: 6px;
  color: var(--gray-600);
  font-size: 12px;
}
.question-text {
  max-height: 140px;
  overflow-y: auto;
  color: var(--gray-900);
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.original-answer {
  font-size: 13px;
}
.question-json {
  font-family: 'SF Mono', 'Monaco', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
}

.answer-field {
  margin-bottom: 16px;
}
.field-label {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 6px;
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 600;
}
.field-required {
  color: var(--color-error-500);
}

.existing-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--gray-600);
  font-size: 12px;
}
</style>
