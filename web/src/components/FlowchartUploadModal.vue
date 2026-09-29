<template>
  <a-modal
    :open="open"
    :title="t('flowchart.uploadTitle')"
    :footer="null"
    :mask-closable="!busy"
    @cancel="close"
  >
    <p class="flowchart-upload-help">{{ t('flowchart.uploadHelp') }}</p>
    <input
      id="flowchart-pdf-file"
      type="file"
      accept="application/pdf,.pdf"
      :disabled="busy"
      :aria-label="t('flowchart.selectPdf')"
      @change="selectFile"
    />
    <a-alert
      v-if="error"
      class="flowchart-upload-message"
      type="error"
      show-icon
      :message="error"
    />
    <a-alert
      v-if="busy"
      class="flowchart-upload-message"
      type="info"
      show-icon
      :message="phase === 'parsing' ? t('flowchart.parsing') : t('flowchart.uploading')"
    />
    <div class="flowchart-upload-actions">
      <a-button :disabled="busy" @click="close">{{ t('common.cancel') }}</a-button>
      <a-button
        type="primary"
        :loading="busy"
        :disabled="!selectedFile || !canUpload"
        @click="submit"
      >
        {{ t('flowchart.startAnalysis') }}
      </a-button>
    </div>
  </a-modal>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fileApi, flowchartApi } from '@/apis/knowledge_api'
import { isPdfFlowchartFile } from '@/utils/flowchartReview'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: String, default: '' },
  canUpload: { type: Boolean, default: false }
})
const emit = defineEmits(['update:open', 'created'])
const { t } = useI18n()
const selectedFile = ref(null)
const busy = ref(false)
const phase = ref('uploading')
const error = ref('')

watch(
  () => props.open,
  (open) => {
    if (!open) {
      selectedFile.value = null
      error.value = ''
      phase.value = 'uploading'
    }
  }
)

const selectFile = (event) => {
  const file = event.target.files?.[0]
  selectedFile.value = isPdfFlowchartFile(file) ? file : null
  error.value = file && !selectedFile.value ? t('flowchart.pdfOnly') : ''
  event.target.value = ''
}

const close = () => {
  if (!busy.value) emit('update:open', false)
}

const submit = async () => {
  if (busy.value || !selectedFile.value || !props.canUpload) return
  busy.value = true
  error.value = ''
  phase.value = 'uploading'
  try {
    const uploaded = await fileApi.uploadFile(selectedFile.value, props.kbId)
    if (!uploaded?.file_path) throw new Error(t('flowchart.uploadFailed'))
    phase.value = 'parsing'
    const result = await flowchartApi.createFlowchart(props.kbId, uploaded.file_path)
    if (!result?.file_id) throw new Error(t('flowchart.createFailed'))
    emit('created', result.file_id)
    emit('update:open', false)
  } catch (cause) {
    error.value = cause.message || t('flowchart.createFailed')
  } finally {
    busy.value = false
  }
}
</script>

<style scoped lang="less">
.flowchart-upload-help {
  color: var(--color-text-secondary);
}
.flowchart-upload-message {
  margin-top: 16px;
}
.flowchart-upload-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 24px;
}
</style>
