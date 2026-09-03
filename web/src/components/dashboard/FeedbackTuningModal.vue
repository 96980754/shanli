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
    <a-form v-else-if="context" layout="vertical">
      <a-alert
        type="info"
        show-icon
        :message="t('feedback.saveTip')"
        class="tip"
      />

      <a-alert
        v-if="isJsonQuestion"
        type="warning"
        show-icon
        :message="t('feedback.jsonQuestionTip')"
        class="tip"
      />

      <a-form-item :label="t('feedback.userQuestionLabel')">
        <div class="readonly-block" :class="{ 'question-json': isJsonQuestion }">
          {{ displayQuestion }}
        </div>
      </a-form-item>

      <a-form-item :label="t('feedback.originalAnswerLabel')">
        <div class="readonly-block original-answer">{{ context.current_answer || '-' }}</div>
      </a-form-item>

      <a-form-item :label="t('feedback.confirmAnswerLabel')" required>
        <a-textarea
          v-model:value="answer"
          :rows="8"
          :maxlength="20000"
          show-count
          :placeholder="t('feedback.answerPlaceholder')"
        />
      </a-form-item>

      <div v-if="context.qa_pair" class="existing-tip">
        {{ $t('feedback.existingQaTip', { count: context.qa_pair.hit_count || 0 }) }}
      </div>
    </a-form>
  </a-modal>
</template>

<script setup>
import { computed, ref } from 'vue'
import { message } from 'ant-design-vue'
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
.tip {
  margin-bottom: 20px;
}
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

.question-json {
  font-family: 'SF Mono', 'Monaco', 'Consolas', monospace;
  font-size: 12px;
  line-height: 1.5;
}
.original-answer {
  max-height: 180px;
  overflow-y: auto;
}
.existing-tip {
  color: var(--gray-600);
  font-size: 12px;
}
</style>
