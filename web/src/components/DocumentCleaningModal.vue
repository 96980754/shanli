<template>
  <a-modal
    v-model:open="visible"
    title="文档清洗预览"
    width="1180px"
    :footer="null"
    :destroy-on-close="true"
  >
    <div v-if="loading" class="loading-state">
      <a-spin tip="正在加载清洗草稿..." />
    </div>
    <a-alert
      v-else-if="errorMessage"
      type="error"
      show-icon
      :message="errorMessage"
      class="cleaning-alert"
    />
    <div v-else-if="draft" class="cleaning-shell">
      <div class="cleaning-summary">
        <a-tag :color="statusColor">{{ statusLabel }}</a-tag>
        <span>草稿版本 {{ draft.cleaning_version }}</span>
        <span v-if="draft.readonly">当前权限为只读</span>
        <span v-if="draft.has_online_chunks">保存草稿不会影响当前在线索引</span>
      </div>

      <a-alert
        v-if="draft.error_message"
        type="warning"
        show-icon
        :message="draft.error_message"
        class="cleaning-alert"
      />

      <div class="comparison-grid">
        <section class="comparison-pane">
          <header>解析原文</header>
          <div class="preview-scroll">
            <MarkdownPreview :content="draft.original_markdown || ''" />
          </div>
        </section>
        <section class="comparison-pane">
          <header class="draft-header">
            <span>清洗结果</span>
            <a-radio-group v-model:value="rightMode" size="small">
              <a-radio-button value="edit">编辑</a-radio-button>
              <a-radio-button value="preview">预览</a-radio-button>
            </a-radio-group>
          </header>
          <textarea
            v-if="rightMode === 'edit'"
            v-model="editableContent"
            class="markdown-editor"
            :readonly="draft.readonly"
            maxlength="2000000"
            spellcheck="false"
          />
          <div v-else class="preview-scroll">
            <MarkdownPreview :content="editableContent" />
          </div>
        </section>
      </div>

      <a-collapse v-if="changes.length || warnings.length" ghost>
        <a-collapse-panel key="changes" header="清洗变更与警告">
          <a-alert
            v-for="warning in warnings"
            :key="warning"
            type="warning"
            show-icon
            :message="warning"
            class="change-alert"
          />
          <ul class="change-list">
            <li v-for="(change, index) in changes" :key="`${change.change_type}-${index}`">
              <strong>{{ change.change_type }}</strong>
              <span>{{ change.reason }}</span>
            </li>
          </ul>
        </a-collapse-panel>
      </a-collapse>

      <div class="modal-actions">
        <a-button @click="visible = false">关闭</a-button>
        <template v-if="!draft.readonly">
          <a-button :loading="actionLoading" @click="cancelDraft">取消草稿</a-button>
          <a-button :loading="actionLoading" @click="regenerateDraft">重新生成</a-button>
          <a-button v-if="draft.status === 'waiting_confirmation'" @click="openQA"
            >QA 知识对</a-button
          >
          <a-button :loading="actionLoading" @click="saveDraft">保存草稿</a-button>
          <a-button type="primary" :loading="actionLoading" @click="confirmDraft">
            确认并入库
          </a-button>
        </template>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { documentApi } from '@/apis/knowledge_api'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: [String, Number], default: '' },
  fileId: { type: [String, Number], default: '' }
})

const emit = defineEmits(['update:open', 'confirmed', 'changed', 'open-qa'])
const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})

const loading = ref(false)
const actionLoading = ref(false)
const errorMessage = ref('')
const draft = ref(null)
const editableContent = ref('')
const rightMode = ref('edit')

const changes = computed(() => draft.value?.cleaning_metadata?.changes || [])
const warnings = computed(() => draft.value?.cleaning_metadata?.warnings || [])
const statusLabel = computed(() => {
  const labels = {
    parsed: '待清洗',
    cleaning: '清洗中',
    waiting_confirmation: '待确认',
    confirmed: '已确认',
    indexing: '入库中',
    indexed: '已入库',
    error_cleaning: '清洗失败',
    error_indexing: '入库失败'
  }
  return labels[draft.value?.status] || draft.value?.status || ''
})
const statusColor = computed(() => {
  if (String(draft.value?.status || '').startsWith('error_')) return 'red'
  if (draft.value?.status === 'indexed') return 'green'
  return 'blue'
})

const applyPayload = (payload) => {
  draft.value = payload
  editableContent.value = payload?.cleaned_markdown || ''
  errorMessage.value = ''
}

const loadDraft = async () => {
  if (!props.kbId || !props.fileId) return
  loading.value = true
  errorMessage.value = ''
  try {
    applyPayload(await documentApi.getCleaningPreview(props.kbId, props.fileId))
  } catch (error) {
    errorMessage.value = error.message || '加载清洗草稿失败'
  } finally {
    loading.value = false
  }
}

const handleOpenChange = (open) => {
  if (open) loadDraft()
}

watch(() => props.open, handleOpenChange)

const runAction = async (action, successText) => {
  actionLoading.value = true
  try {
    const payload = await action()
    if (payload?.original_markdown !== undefined) {
      applyPayload(payload)
    }
    message.success(successText)
    emit('changed', payload)
    return payload
  } catch (error) {
    message.error(error.message || '操作失败，请刷新后重试')
    return null
  } finally {
    actionLoading.value = false
  }
}

const saveDraft = () =>
  runAction(
    () =>
      documentApi.saveCleaningDraft(
        props.kbId,
        props.fileId,
        editableContent.value,
        draft.value.cleaning_version
      ),
    '草稿已保存'
  )

const regenerateDraft = () =>
  runAction(
    () =>
      documentApi.regenerateCleaningDraft(props.kbId, props.fileId, draft.value.cleaning_version),
    '清洗草稿已重新生成'
  )

const cancelDraft = () => {
  Modal.confirm({
    title: '取消当前清洗草稿？',
    content: '已入库文档会继续使用上一次确认的内容。',
    okText: '确认取消',
    cancelText: '返回',
    onOk: () =>
      runAction(
        () =>
          documentApi.cancelCleaningDraft(props.kbId, props.fileId, draft.value.cleaning_version),
        '清洗草稿已取消'
      )
  })
}

const confirmDraft = () => {
  Modal.confirm({
    title: '确认清洗结果并入库？',
    content: '系统会切片并写入向量库；已入库文档会在新索引验证成功后切换版本。',
    okText: '确认并入库',
    cancelText: '返回编辑',
    onOk: async () => {
      const payload = await runAction(
        () =>
          documentApi.confirmCleaningDraft(props.kbId, props.fileId, draft.value.cleaning_version),
        '文档已确认并完成入库'
      )
      if (payload) {
        emit('confirmed', payload)
        visible.value = false
      }
    }
  })
}

const openQA = () => {
  emit('open-qa', props.fileId)
}
</script>

<style scoped lang="less">
.loading-state {
  display: grid;
  min-height: 420px;
  place-items: center;
}

.cleaning-shell {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.cleaning-summary {
  display: flex;
  align-items: center;
  gap: 12px;
  color: var(--gray-600);
  font-size: 13px;
}

.cleaning-alert,
.change-alert {
  margin-bottom: 8px;
}

.comparison-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  min-height: 520px;
}

.comparison-pane {
  display: flex;
  min-width: 0;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
}

.comparison-pane > header {
  display: flex;
  min-height: 44px;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--gray-200);
  background: var(--gray-50);
  font-weight: 600;
}

.draft-header {
  justify-content: space-between;
}

.preview-scroll {
  flex: 1;
  overflow: auto;
  padding: 14px;
}

.markdown-editor {
  flex: 1;
  resize: none;
  border: 0;
  outline: none;
  padding: 14px;
  background: var(--gray-0);
  color: var(--gray-900);
  font:
    13px/1.65 ui-monospace,
    SFMono-Regular,
    Menlo,
    Consolas,
    monospace;
}

.markdown-editor:read-only {
  background: var(--gray-50);
}

.change-list {
  display: grid;
  margin: 0;
  gap: 6px;
}

.change-list li {
  display: flex;
  gap: 8px;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

@media (max-width: 900px) {
  .comparison-grid {
    grid-template-columns: 1fr;
  }
}
</style>
