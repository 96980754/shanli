<template>
  <a-modal
    :open="open"
    :title="t('flowchart.reviewTitle')"
    width="min(1600px, 96vw)"
    :footer="null"
    :mask-closable="false"
    :body-style="{ padding: '16px', height: 'min(82vh, 1000px)' }"
    @cancel="requestClose"
  >
    <div class="flowchart-review">
      <div v-if="loading" class="flowchart-center">
        <a-spin :tip="t('flowchart.loadingPreview')" />
      </div>
      <a-alert v-else-if="loadError" type="error" show-icon :message="loadError" />
      <template v-else-if="preview">
        <div class="flowchart-topline">
          <div class="flowchart-status">
            <a-tag :color="statusColor">{{
              getFileStatusView(reparsing ? 'flowchart_parsing' : preview.status).label
            }}</a-tag>
            <span>{{ t('flowchart.revision', { revision: preview.revision }) }}</span>
            <span v-if="dirty" class="flowchart-dirty">{{ t('flowchart.unsaved') }}</span>
            <span v-else-if="savedNotice">{{ t('flowchart.saved') }}</span>
          </div>
          <div class="flowchart-actions">
            <a-button v-if="conflict" @click="loadPreview">{{
              t('flowchart.reloadLatest')
            }}</a-button>
            <a-button
              v-if="editable"
              :disabled="busy"
              :loading="reparsing"
              @click="requestReparse"
              >{{ t('flowchart.reparse') }}</a-button
            >
            <a-button
              v-if="editable && savedMarkdown"
              type="primary"
              :disabled="!dirty || busy || conflict"
              :loading="saving"
              @click="saveDraft"
              >{{ t('flowchart.saveDraft') }}</a-button
            >
            <a-button
              v-if="
                editable && savedMarkdown && preview.status === 'flowchart_waiting_confirmation'
              "
              type="primary"
              :disabled="dirty || busy || conflict"
              :loading="confirming"
              @click="requestConfirm"
              >{{ t('flowchart.confirmIndex') }}</a-button
            >
            <a-button
              v-if="canManage && preview.confirmed_at && preview.status === 'error_indexing'"
              type="primary"
              :loading="confirming"
              @click="retryIndex"
              >{{ t('flowchart.retryIndex') }}</a-button
            >
          </div>
        </div>
        <p class="flowchart-disclaimer">{{ t('flowchart.disclaimer') }}</p>
        <a-alert
          v-if="reparsing"
          class="flowchart-notice"
          type="info"
          show-icon
          :message="t('flowchart.reparsing')"
        />
        <a-alert
          v-if="['flowchart_confirming', 'indexing'].includes(preview.status)"
          class="flowchart-notice"
          type="info"
          show-icon
          :message="t('flowchart.indexing')"
        />
        <a-alert
          v-if="preview.status === 'error_indexing'"
          class="flowchart-notice"
          type="error"
          show-icon
          :message="t('flowchart.indexFailed')"
          :description="preview.error_message || undefined"
        />
        <a-alert
          v-if="preview.status === 'error_flowchart_parsing'"
          class="flowchart-notice"
          type="warning"
          show-icon
          :message="savedMarkdown ? t('flowchart.failedWithDraft') : t('flowchart.failedNoDraft')"
          :description="preview.error_message || undefined"
        />
        <a-alert
          v-for="key in warningKeys"
          :key="key"
          class="flowchart-notice"
          type="warning"
          show-icon
          :message="t(key)"
        />
        <a-collapse v-if="dpiPages.length" ghost class="flowchart-render-details">
          <a-collapse-panel key="render" :header="t('flowchart.renderDetails')">
            <span v-for="page in dpiPages" :key="page.page_number" class="flowchart-dpi-page">
              {{
                t('flowchart.dpiDetail', {
                  page: page.page_number,
                  requested: page.requested_dpi,
                  effective: page.effective_dpi
                })
              }}
            </span>
          </a-collapse-panel>
        </a-collapse>
        <div class="flowchart-review-grid">
          <section
            class="flowchart-pane flowchart-pdf-pane"
            :aria-label="t('flowchart.originalPdf')"
          >
            <header>{{ t('flowchart.originalPdf') }}</header>
            <div v-if="sourceLoading" class="flowchart-center"><a-spin /></div>
            <a-alert v-else-if="sourceError" type="error" show-icon :message="sourceError" />
            <AgentFilePreview
              v-else-if="sourceUrl"
              :file="sourceFile"
              :file-path="preview.filename || ''"
              :show-header="false"
              :show-download="false"
              :full-height="true"
              :borderless="true"
            />
          </section>
          <section
            class="flowchart-pane flowchart-markdown-pane"
            :aria-label="t('flowchart.semanticMarkdown')"
          >
            <header class="flowchart-editor-header">
              <span>{{ t('flowchart.semanticMarkdown') }}</span>
              <a-radio-group v-if="savedMarkdown" v-model:value="editorMode" size="small">
                <a-radio-button value="edit">{{ t('common.edit') }}</a-radio-button>
                <a-radio-button value="preview">{{ t('upload.preview') }}</a-radio-button>
              </a-radio-group>
            </header>
            <div class="flowchart-section-contract">
              <strong>{{ t('flowchart.requiredSections') }}</strong>
              <span>{{ sectionNames }}</span>
            </div>
            <div v-if="busy && !savedMarkdown" class="flowchart-center">
              <a-spin :tip="t('flowchart.parsing')" />
            </div>
            <div v-else-if="!savedMarkdown" class="flowchart-center">
              {{ t('flowchart.noDraft') }}
            </div>
            <textarea
              v-else-if="editorMode === 'edit'"
              v-model="currentMarkdown"
              class="flowchart-editor"
              :readonly="!editable || busy || conflict"
              spellcheck="false"
              :aria-label="t('flowchart.semanticMarkdown')"
            />
            <div v-else class="flowchart-markdown-preview">
              <MarkdownPreview :content="currentMarkdown" />
            </div>
          </section>
        </div>
      </template>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { message, Modal } from 'ant-design-vue'
import { flowchartApi } from '@/apis/knowledge_api'
import { getWorkspaceKnowledgeFileContent } from '@/apis/workspace_api'
import { normalizePreviewResponse } from '@/utils/file_preview'
import { getFileStatusView } from '@/utils/knowledge_file_policy'
import {
  canEditFlowchartDraft,
  getFlowchartWarningKeys,
  isFlowchartDraftDirty
} from '@/utils/flowchartReview'
import AgentFilePreview from '@/components/AgentFilePreview.vue'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: String, default: '' },
  fileId: { type: String, default: '' },
  canManage: { type: Boolean, default: false }
})
const emit = defineEmits(['update:open', 'changed'])
const { t } = useI18n()
const preview = ref(null)
const loading = ref(false)
const loadError = ref('')
const savedMarkdown = ref('')
const currentMarkdown = ref('')
const savedNotice = ref(false)
const conflict = ref(false)
const editorMode = ref('edit')
const saving = ref(false)
const reparsing = ref(false)
const confirming = ref(false)
const sourceLoading = ref(false)
const sourceError = ref('')
const sourceUrl = ref('')
let requestSequence = 0
let sourceRequestSequence = 0
let pollTimer = null

const sectionNames =
  '# 流程名称 · ## 流程用途 · ## 参与角色 · ## 主流程 · ## 条件分支 · ## 退回与异常路径 · ## 关键上下游关系 · ## 开始与结束 · ## 补充说明'
const dirty = computed(() => isFlowchartDraftDirty(savedMarkdown.value, currentMarkdown.value))
const editable = computed(() => canEditFlowchartDraft(preview.value, props.canManage))
const busy = computed(
  () =>
    saving.value ||
    reparsing.value ||
    confirming.value ||
    preview.value?.status === 'flowchart_parsing'
)
const warningKeys = computed(() => getFlowchartWarningKeys(preview.value?.flowchart_metadata))
const dpiPages = computed(() =>
  (preview.value?.flowchart_metadata?.pages || []).filter(
    (page) => page.requested_dpi && page.effective_dpi
  )
)
const statusColor = computed(() => {
  if (['error_flowchart_parsing', 'error_indexing'].includes(preview.value?.status)) return 'orange'
  if (preview.value?.confirmed_at) return 'green'
  return 'blue'
})
const sourceFile = computed(() => ({
  previewType: 'pdf',
  previewUrl: sourceUrl.value,
  supported: true
}))

const clearPoll = () => {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
}
const revokeSource = () => {
  if (sourceUrl.value) URL.revokeObjectURL(sourceUrl.value)
  sourceUrl.value = ''
}
const applyPreview = (payload) => {
  preview.value = payload
  savedMarkdown.value = payload.semantic_markdown || ''
  currentMarkdown.value = savedMarkdown.value
  conflict.value = false
  loadError.value = ''
}
const loadPreview = async () => {
  if (!props.kbId || !props.fileId) return
  const sequence = ++requestSequence
  clearPoll()
  loading.value = !preview.value
  try {
    const payload = await flowchartApi.getFlowchartPreview(props.kbId, props.fileId)
    if (sequence !== requestSequence) return
    applyPreview(payload)
    if (['flowchart_parsing', 'flowchart_confirming', 'indexing'].includes(payload.status)) {
      pollTimer = setTimeout(loadPreview, 3000)
    }
    return payload
  } catch (cause) {
    if (sequence === requestSequence) loadError.value = cause.message || t('flowchart.loadFailed')
    return null
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}
const loadSource = async () => {
  const sequence = ++sourceRequestSequence
  sourceLoading.value = true
  sourceError.value = ''
  try {
    const response = await getWorkspaceKnowledgeFileContent(props.kbId, props.fileId)
    const result = await normalizePreviewResponse(response)
    if (sequence !== sourceRequestSequence) {
      if (result.previewUrl) URL.revokeObjectURL(result.previewUrl)
      return
    }
    revokeSource()
    sourceUrl.value = result.previewUrl || ''
    if (!sourceUrl.value) sourceError.value = result.message || t('flowchart.pdfPreviewFailed')
  } catch (cause) {
    if (sequence === sourceRequestSequence)
      sourceError.value = cause.message || t('flowchart.pdfPreviewFailed')
  } finally {
    if (sequence === sourceRequestSequence) sourceLoading.value = false
  }
}

watch(
  () => [props.open, props.kbId, props.fileId],
  ([open]) => {
    requestSequence++
    sourceRequestSequence++
    clearPoll()
    revokeSource()
    preview.value = null
    savedMarkdown.value = ''
    currentMarkdown.value = ''
    savedNotice.value = false
    conflict.value = false
    editorMode.value = 'edit'
    if (open && props.fileId) {
      loadPreview()
      loadSource()
    }
  },
  { immediate: true }
)

const saveDraft = async () => {
  if (!editable.value || !dirty.value || busy.value || conflict.value) return
  saving.value = true
  try {
    const result = await flowchartApi.updateFlowchartDraft(
      props.kbId,
      props.fileId,
      preview.value.revision,
      currentMarkdown.value
    )
    applyPreview(result)
    savedNotice.value = true
    message.success(t('flowchart.saved'))
    emit('changed')
  } catch (cause) {
    if (cause.response?.status === 409) {
      conflict.value = true
      message.error(t('flowchart.revisionConflict'))
    } else {
      message.error(cause.message || t('flowchart.saveFailed'))
    }
  } finally {
    saving.value = false
  }
}

const confirmAction = (content) =>
  new Promise((resolve) => {
    Modal.confirm({
      title: t('flowchart.confirmAction'),
      content,
      okText: t('common.ok'),
      cancelText: t('common.cancel'),
      onOk: () => resolve(true),
      onCancel: () => resolve(false)
    })
  })

const requestReparse = async () => {
  if (!editable.value || busy.value || conflict.value) return
  const allowed = await confirmAction(
    dirty.value ? t('flowchart.reparseDirtyConfirm') : t('flowchart.reparseConfirm')
  )
  if (!allowed) return
  reparsing.value = true
  savedNotice.value = false
  try {
    const result = await flowchartApi.reparseFlowchart(
      props.kbId,
      props.fileId,
      preview.value.revision
    )
    if (result.status === 'flowchart_parsing') {
      preview.value = { ...preview.value, status: result.status }
    }
    const latest = await loadPreview()
    if (!latest) return
    if (preview.value?.status === 'error_flowchart_parsing')
      message.warning(t('flowchart.reparseFailed'))
    else message.success(t('flowchart.reparseCompleted'))
    emit('changed')
  } catch (cause) {
    if (cause.response?.status === 409) {
      conflict.value = true
      message.error(t('flowchart.revisionConflict'))
    } else {
      message.error(cause.message || t('flowchart.reparseFailed'))
    }
  } finally {
    reparsing.value = false
  }
}

const requestConfirm = async () => {
  if (!editable.value || !savedMarkdown.value || dirty.value || busy.value || conflict.value) return
  if (preview.value.status !== 'flowchart_waiting_confirmation') return
  if (!(await confirmAction(t('flowchart.confirmWarning')))) return
  confirming.value = true
  const previousStatus = preview.value.status
  preview.value = { ...preview.value, status: 'flowchart_confirming' }
  pollTimer = setTimeout(loadPreview, 3000)
  try {
    await flowchartApi.confirmFlowchart(props.kbId, props.fileId, preview.value.revision)
    await loadPreview()
    emit('changed')
  } catch (cause) {
    if (cause.response?.status === 409) {
      clearPoll()
      preview.value = { ...preview.value, status: previousStatus }
      conflict.value = true
      message.error(t('flowchart.revisionConflict'))
    } else {
      message.error(cause.message || t('flowchart.indexFailed'))
      await loadPreview()
    }
  } finally {
    confirming.value = false
  }
}

const retryIndex = async () => {
  if (!props.canManage || confirming.value || preview.value?.status !== 'error_indexing') return
  confirming.value = true
  preview.value = { ...preview.value, status: 'flowchart_confirming' }
  pollTimer = setTimeout(loadPreview, 3000)
  try {
    await flowchartApi.retryFlowchartIndex(props.kbId, props.fileId)
    await loadPreview()
    emit('changed')
  } catch (cause) {
    message.error(cause.message || t('flowchart.indexFailed'))
    await loadPreview()
  } finally {
    confirming.value = false
  }
}

const confirmDiscard = async () => {
  if (saving.value || reparsing.value || confirming.value) return false
  if (!dirty.value) return true
  return confirmAction(t('flowchart.discardConfirm'))
}
const requestClose = async () => {
  if (saving.value || reparsing.value || confirming.value) return
  if (await confirmDiscard()) emit('update:open', false)
}
const beforeUnload = (event) => {
  if (!props.open || !dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}
window.addEventListener('beforeunload', beforeUnload)
onBeforeUnmount(() => {
  requestSequence++
  sourceRequestSequence++
  clearPoll()
  revokeSource()
  window.removeEventListener('beforeunload', beforeUnload)
})
defineExpose({ confirmDiscard })
</script>

<style scoped lang="less">
.flowchart-review {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}
.flowchart-center {
  display: grid;
  place-items: center;
  flex: 1;
  min-height: 120px;
  color: var(--color-text-secondary);
}
.flowchart-topline,
.flowchart-status,
.flowchart-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.flowchart-topline {
  justify-content: space-between;
}
.flowchart-dirty {
  color: var(--color-warning-700);
}
.flowchart-disclaimer {
  color: var(--color-text-secondary);
  margin: 8px 0;
}
.flowchart-notice {
  margin-bottom: 8px;
}
.flowchart-render-details {
  flex: none;
}
.flowchart-dpi-page {
  display: block;
}
.flowchart-review-grid {
  display: grid;
  grid-template-columns: minmax(0, 45fr) minmax(0, 55fr);
  gap: 12px;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.flowchart-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  overflow: hidden;
}
.flowchart-pane > header {
  padding: 8px 12px;
  border-bottom: 1px solid var(--gray-100);
  font-weight: 600;
}
.flowchart-pdf-pane > :not(header) {
  flex: 1;
  min-height: 0;
}
.flowchart-editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.flowchart-section-contract {
  display: flex;
  flex-direction: column;
  padding: 8px 12px;
  font-size: 12px;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--gray-100);
}
.flowchart-editor {
  width: 100%;
  flex: 1;
  min-height: 0;
  padding: 12px;
  border: 0;
  resize: none;
  outline: none;
  overflow: auto;
  font: 13px/1.6 monospace;
  color: var(--color-text);
  background: var(--color-bg-container);
}
.flowchart-markdown-preview {
  flex: 1;
  min-height: 0;
  padding: 12px;
  overflow: auto;
}
@media (max-width: 800px) {
  .flowchart-review-grid {
    grid-template-columns: 1fr;
    grid-template-rows: minmax(180px, 40%) minmax(220px, 60%);
  }
}
</style>
