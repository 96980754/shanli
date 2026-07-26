<template>
  <a-modal
    v-model:open="visible"
    title="QA 知识对"
    width="920px"
    :footer="null"
    :destroy-on-close="true"
    @after-open-change="handleOpenChange"
  >
    <div v-if="loading" class="qa-loading">
      <a-spin tip="正在加载 QA 知识对..." />
    </div>
    <a-alert v-else-if="errorMessage" type="error" show-icon :message="errorMessage" />
    <div v-else class="qa-shell">
      <div class="qa-toolbar">
        <span>正文版本 {{ payload?.cleaning_version ?? '-' }}</span>
        <span v-if="payload?.readonly">当前权限为只读</span>
        <div v-if="!payload?.readonly" class="qa-toolbar-actions">
          <a-button :loading="actionLoading" @click="generate">自动生成草稿</a-button>
          <a-button @click="startManual">手工新增</a-button>
          <a-button
            type="primary"
            :disabled="confirmableItems.length === 0"
            :loading="actionLoading"
            @click="confirmAll"
          >
            批量确认
          </a-button>
        </div>
      </div>

      <a-alert
        v-if="draft"
        type="info"
        show-icon
        message="选择原文证据后保存；答案中的数字、型号、版本和链接必须能在证据中找到。"
      />
      <a-alert v-if="generationMessage" type="info" show-icon :message="generationMessage" />
      <section v-if="draft" class="qa-editor">
        <a-input v-model:value="draft.question" :maxlength="300" placeholder="问题" />
        <a-textarea
          v-model:value="draft.answer"
          :maxlength="2000"
          :auto-size="{ minRows: 3, maxRows: 8 }"
          placeholder="答案"
        />
        <a-select
          v-model:value="draft.source_chunk_ids"
          mode="tags"
          :open="false"
          placeholder="输入来源 chunk ID 后按回车"
        />
        <div
          v-for="(evidence, index) in draft.evidence"
          :key="`${evidence.chunk_id}-${index}`"
          class="evidence-editor"
        >
          <a-input v-model:value="evidence.chunk_id" placeholder="chunk ID" />
          <a-textarea
            v-model:value="evidence.text"
            :auto-size="{ minRows: 2, maxRows: 5 }"
            placeholder="从该 chunk 原文中逐字摘录证据"
          />
          <a-button danger type="text" @click="draft.evidence.splice(index, 1)">删除证据</a-button>
        </div>
        <div class="qa-editor-actions">
          <a-button @click="draft.evidence.push({ chunk_id: '', text: '' })">增加证据</a-button>
          <a-button @click="draft = null">取消</a-button>
          <a-button type="primary" :loading="actionLoading" @click="saveDraft">保存草稿</a-button>
        </div>
      </section>

      <a-empty v-if="!draft && items.length === 0" description="暂无 QA 知识对" />
      <section v-for="item in items" :key="item.qa_id" class="qa-card">
        <header>
          <div class="qa-tags">
            <a-tag :color="item.source === 'manual' ? 'purple' : 'blue'">
              {{ item.source === 'manual' ? '人工' : '自动' }}
            </a-tag>
            <a-tag :color="statusColor(item)">{{ statusLabel(item) }}</a-tag>
            <a-tag v-if="item.possibly_outdated" color="orange">正文已变化，需复核</a-tag>
          </div>
          <div v-if="!payload?.readonly" class="qa-card-actions">
            <a-button size="small" @click="edit(item)">编辑</a-button>
            <a-button
              v-if="item.status !== 'confirmed' || item.sync_status !== 'synced'"
              size="small"
              type="primary"
              :loading="actionLoading"
              @click="confirmOne(item)"
            >
              确认并同步
            </a-button>
            <a-button
              v-if="item.status === 'draft'"
              size="small"
              danger
              :loading="actionLoading"
              @click="rejectOne(item)"
            >
              拒绝
            </a-button>
          </div>
        </header>
        <strong class="question">{{ item.question }}</strong>
        <p class="answer">{{ item.answer }}</p>
        <a-alert
          v-if="item.sync_status === 'failed'"
          type="error"
          show-icon
          :message="item.sync_error || 'QA 同步失败，可重新确认后重试'"
        />
        <div class="evidence-list">
          <div v-for="evidence in item.evidence" :key="`${item.qa_id}-${evidence.chunk_id}`">
            <code>{{ evidence.chunk_id }}</code>
            <span>{{ evidence.text }}</span>
          </div>
        </div>
      </section>

      <div class="qa-footer">
        <a-button @click="visible = false">关闭</a-button>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { documentApi } from '@/apis/knowledge_api'

const props = defineProps({
  open: { type: Boolean, default: false },
  kbId: { type: [String, Number], default: '' },
  fileId: { type: [String, Number], default: '' }
})
const emit = defineEmits(['update:open', 'changed'])
const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})

const loading = ref(false)
const actionLoading = ref(false)
const errorMessage = ref('')
const payload = ref(null)
const items = computed(() => payload.value?.items || [])
const confirmableItems = computed(() =>
  items.value.filter((item) => item.status === 'draft' || item.sync_status === 'failed')
)
const draft = ref(null)
const generationMessage = ref('')

const statusLabel = (item) => {
  if (item.sync_status === 'failed') return '同步失败'
  return item.status === 'confirmed' ? '已确认' : '草稿'
}
const statusColor = (item) => {
  if (item.sync_status === 'failed') return 'red'
  return item.status === 'confirmed' ? 'green' : 'gold'
}

const load = async () => {
  if (!props.kbId || !props.fileId) return
  loading.value = true
  errorMessage.value = ''
  try {
    payload.value = await documentApi.getDocumentQAs(props.kbId, props.fileId)
  } catch (error) {
    errorMessage.value = error.message || '加载 QA 知识对失败'
  } finally {
    loading.value = false
  }
}

const handleOpenChange = (open) => {
  if (open) load()
}

const startManual = () => {
  draft.value = {
    qa_id: null,
    version: null,
    question: '',
    answer: '',
    source_chunk_ids: [],
    evidence: [{ chunk_id: '', text: '' }]
  }
}

const edit = (item) => {
  draft.value = {
    qa_id: item.qa_id,
    version: item.version,
    question: item.question,
    answer: item.answer,
    source_chunk_ids: [...item.source_chunk_ids],
    evidence: item.evidence.map((evidence) => ({ ...evidence }))
  }
}

const runAction = async (action, successMessage) => {
  actionLoading.value = true
  try {
    await action()
    await load()
    message.success(successMessage)
    emit('changed')
  } catch (error) {
    message.error(error.message || '操作失败，请刷新后重试')
  } finally {
    actionLoading.value = false
  }
}

const saveDraft = async () => {
  const evidence = draft.value.evidence.filter((item) => item.chunk_id && item.text)
  const body = {
    question: draft.value.question,
    answer: draft.value.answer,
    source_chunk_ids: draft.value.source_chunk_ids,
    evidence
  }
  await runAction(async () => {
    if (draft.value.qa_id) {
      await documentApi.updateDocumentQA(props.kbId, props.fileId, draft.value.qa_id, {
        ...body,
        version: draft.value.version
      })
    } else {
      await documentApi.createDocumentQA(props.kbId, props.fileId, body)
    }
    draft.value = null
  }, 'QA 草稿已保存')
}

const generate = async () => {
  actionLoading.value = true
  try {
    const queued = await documentApi.generateDocumentQAs(props.kbId, props.fileId)
    generationMessage.value = queued.created ? '正在生成 QA 草稿...' : '已有相同正文版本的生成任务'
    for (let attempt = 0; attempt < 30 && visible.value; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 1000))
      const task = await documentApi.getDocumentQAGenerationTask(
        props.kbId,
        props.fileId,
        queued.task_id
      )
      generationMessage.value = task.message || generationMessage.value
      if (task.status === 'success') {
        await load()
        generationMessage.value = ''
        message.success('QA 草稿生成完成')
        emit('changed')
        return
      }
      if (['failed', 'cancelled'].includes(task.status)) {
        generationMessage.value = task.error || 'QA 草稿生成失败'
        return
      }
    }
    generationMessage.value = 'QA 任务仍在后台执行，可稍后重新打开查看'
  } catch (error) {
    message.error(error.message || '生成失败，请稍后重试')
  } finally {
    actionLoading.value = false
  }
}

const confirmOne = (item) =>
  runAction(
    () => documentApi.confirmDocumentQA(props.kbId, props.fileId, item.qa_id, item.version),
    'QA 已确认并同步'
  )

const confirmAll = () =>
  runAction(
    () =>
      documentApi.batchConfirmDocumentQAs(
        props.kbId,
        props.fileId,
        confirmableItems.value.map((item) => ({ qa_id: item.qa_id, version: item.version }))
      ),
    'QA 批量确认完成'
  )

const rejectOne = (item) => {
  Modal.confirm({
    title: '拒绝该 QA 草稿？',
    content: '拒绝后，自动生成不会静默恢复相同问题。',
    okText: '确认拒绝',
    cancelText: '取消',
    onOk: () =>
      runAction(
        () => documentApi.rejectDocumentQA(props.kbId, props.fileId, item.qa_id, item.version),
        'QA 草稿已拒绝'
      )
  })
}
</script>

<style scoped lang="less">
.qa-loading {
  display: grid;
  min-height: 360px;
  place-items: center;
}

.qa-shell,
.qa-editor,
.qa-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.qa-toolbar,
.qa-card header,
.qa-editor-actions,
.qa-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.qa-toolbar {
  color: var(--color-text-secondary);
}

.qa-toolbar-actions,
.qa-card-actions,
.qa-tags {
  display: flex;
  gap: 8px;
}

.qa-editor,
.qa-card {
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.evidence-editor,
.evidence-list > div {
  display: grid;
  grid-template-columns: minmax(140px, 220px) 1fr auto;
  gap: 8px;
  align-items: start;
}

.question,
.answer {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.answer {
  margin: 0;
}

.evidence-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: var(--color-text-secondary);
}

.evidence-list code {
  overflow-wrap: anywhere;
}

.qa-footer {
  justify-content: flex-end;
}
</style>
