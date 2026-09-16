<template>
  <div class="qa-pair-page">
    <div class="filters">
      <a-select
        v-model:value="filters.agent_slug"
        :options="agentOptions"
        class="filter-select"
        allow-clear
        @change="applyFilters"
      />
      <a-select
        v-model:value="filters.source_type"
        :options="sourceOptions"
        class="filter-select"
        @change="applyFilters"
      />
      <a-select
        v-model:value="filters.enabled"
        :options="enabledOptions"
        class="filter-select"
        @change="applyFilters"
      />
      <a-input-search
        v-model:value="filters.keyword"
        :placeholder="t('qaPairs.searchPlaceholder')"
        allow-clear
        class="query-input"
        @search="applyFilters"
      />
      <a-button class="refresh-btn" :loading="loading" @click="loadQaPairs">{{ $t('common.refresh') }}</a-button>
    </div>

    <div v-if="selectedIds.length" class="batch-bar">
      <span>{{ t('qaPairs.batchSelected', { count: selectedIds.length }) }}</span>
      <a-space>
        <a-button size="small" @click="batchSetEnabled(true)">{{ $t('qaPairs.batchEnable') }}</a-button>
        <a-button size="small" @click="batchSetEnabled(false)">{{ $t('qaPairs.batchDisable') }}</a-button>
        <a-popconfirm
          :title="t('qaPairs.deleteConfirmTitle')"
          :ok-text="t('qaPairs.deleteConfirmOk')"
          @confirm="batchDelete"
        >
          <a-button size="small" danger>{{ $t('qaPairs.batchDelete') }}</a-button>
        </a-popconfirm>
      </a-space>
    </div>

    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      :pagination="pagination"
      :locale="{ emptyText: t('qaPairs.noData') }"
      :row-selection="{ selectedRowKeys: selectedIds, onChange: setSelectedIds }"
      row-key="id"
      :scroll="{ x: 1200 }"
      @change="handleTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'question'">
          <a-tooltip :title="record.question" placement="topLeft">
            <div class="qa-text">{{ record.question }}</div>
          </a-tooltip>
        </template>

        <template v-else-if="column.key === 'answer'">
          <a-tooltip :title="record.answer" placement="topLeft">
            <div class="qa-text">{{ record.answer }}</div>
          </a-tooltip>
        </template>

        <template v-else-if="column.key === 'agent'">
          <a-tag>{{ record.agent_slug }}</a-tag>
        </template>

        <template v-else-if="column.key === 'source_type'">
          <a-tag :color="record.source_type === 'udesk' ? 'geekblue' : 'cyan'">
            {{ sourceLabel(record.source_type) }}
          </a-tag>
          <div v-if="record.source_conversation_id" class="source-ref">
            {{ record.source_conversation_id }}
          </div>
        </template>

        <template v-else-if="column.key === 'enabled'">
          <a-switch
            :checked="record.enabled"
            size="small"
            :loading="record._toggling"
            @change="(checked) => toggleEnabled(record, checked)"
          />
        </template>

        <template v-else-if="column.key === 'hits'">
          <span>{{ record.hit_count || 0 }}</span>
          <div v-if="record.last_hit_at" class="source-ref">{{ formatFullDateTime(record.last_hit_at) }}</div>
        </template>

        <template v-else-if="column.key === 'updated_at'">
          {{ formatFullDateTime(record.updated_at) }}
        </template>

        <template v-else-if="column.key === 'actions'">
          <a-button type="link" size="small" @click="openEdit(record)">{{ $t('qaPairs.editAction') }}</a-button>
          <a-popconfirm
            :title="t('qaPairs.deleteConfirmTitle')"
            :ok-text="t('qaPairs.deleteConfirmOk')"
            @confirm="deleteOne(record)"
          >
            <a-button type="link" size="small" danger>{{ $t('qaPairs.deleteAction') }}</a-button>
          </a-popconfirm>
        </template>
      </template>
    </a-table>

    <a-modal
      v-model:open="editVisible"
      :title="t('qaPairs.editTitle')"
      :ok-text="t('qaPairs.editOk')"
      :confirm-loading="editSaving"
      @ok="confirmEdit"
    >
      <p class="edit-hint">{{ t('qaPairs.editHint') }}</p>
      <a-form layout="vertical">
        <a-form-item :label="t('qaPairs.questionColumn')">
          <a-textarea v-model:value="editForm.question" :rows="2" :maxlength="2000" />
        </a-form-item>
        <a-form-item :label="t('qaPairs.answerColumn')">
          <a-textarea v-model:value="editForm.answer" :rows="6" :maxlength="20000" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import { dashboardApi } from '@/apis/dashboard_api'
import { agentApi } from '@/apis/agent_api'
import { formatFullDateTime } from '@/utils/time'

const { t } = useI18n()

const sourceOptions = computed(() => [
  { label: t('qaPairs.sourceAll'), value: '' },
  { label: t('qaPairs.sourceFeedback'), value: 'feedback' },
  { label: t('qaPairs.sourceUdesk'), value: 'udesk' }
])
const enabledOptions = computed(() => [
  { label: t('qaPairs.enabledAll'), value: '' },
  { label: t('qaPairs.enabledOn'), value: 'true' },
  { label: t('qaPairs.enabledOff'), value: 'false' }
])
const columns = computed(() => [
  { title: t('qaPairs.questionColumn'), key: 'question', width: 260 },
  { title: t('qaPairs.answerColumn'), key: 'answer', width: 300 },
  { title: t('qaPairs.agentColumn'), key: 'agent', width: 120 },
  { title: t('qaPairs.sourceColumn'), key: 'source_type', width: 130 },
  { title: t('qaPairs.enabledColumn'), key: 'enabled', width: 90 },
  { title: t('qaPairs.hitsColumn'), key: 'hits', width: 130 },
  { title: t('qaPairs.timeColumn'), key: 'updated_at', width: 170 },
  { title: t('qaPairs.actionsColumn'), key: 'actions', width: 130, fixed: 'right' }
])

const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filters = reactive({ agent_slug: '', source_type: '', enabled: '', keyword: '' })
const agentOptions = ref([])
const selectedIds = ref([])

const pagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  total: total.value,
  showSizeChanger: true,
  showQuickJumper: true,
  pageSizeOptions: ['10', '20', '50', '100'],
  showTotal: (value) => t('qaPairs.totalCount', { total: value })
}))

const sourceLabel = (source) =>
  sourceOptions.value.find((item) => item.value === source)?.label || source

async function loadQaPairs() {
  loading.value = true
  try {
    const response = await dashboardApi.getQaPairs({
      agent_slug: filters.agent_slug,
      source_type: filters.source_type,
      enabled: filters.enabled,
      keyword: filters.keyword.trim(),
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value
    })
    items.value = response.items || []
    total.value = response.total || 0
    selectedIds.value = selectedIds.value.filter((id) => items.value.some((item) => item.id === id))
  } catch (error) {
    console.error('加载问答对列表失败', error)
    message.error(error?.message || t('qaPairs.loadFailed'))
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  loadQaPairs()
}

function handleTableChange(next) {
  page.value = next.current
  pageSize.value = next.pageSize
  loadQaPairs()
}

function setSelectedIds(keys) {
  selectedIds.value = keys
}

async function loadAgents() {
  try {
    const response = await agentApi.getAgents()
    agentOptions.value = [
      { label: t('qaPairs.agentAll'), value: '' },
      ...(response.agents || []).map((agent) => ({
        label: agent.name || agent.agent_id || agent.slug || agent.id,
        value: agent.agent_id || agent.slug || agent.id
      }))
    ]
  } catch (error) {
    console.error('加载智能体列表失败', error)
    agentOptions.value = [{ label: t('qaPairs.agentAll'), value: '' }]
  }
}

async function toggleEnabled(record, checked) {
  record._toggling = true
  try {
    await dashboardApi.updateQaPairsEnabled([record.id], checked)
    record.enabled = checked
    message.success(checked ? t('qaPairs.enableSuccess') : t('qaPairs.disableSuccess'))
  } catch (error) {
    console.error('更新启用状态失败', error)
    message.error(error?.message || t('qaPairs.toggleFailed'))
  } finally {
    record._toggling = false
  }
}

async function batchSetEnabled(enabled) {
  try {
    const response = await dashboardApi.updateQaPairsEnabled(selectedIds.value, enabled)
    message.success(t('qaPairs.batchSuccess', { count: response.updated || 0 }))
    await loadQaPairs()
  } catch (error) {
    console.error('批量更新状态失败', error)
    message.error(error?.message || t('qaPairs.toggleFailed'))
  }
}

async function batchDelete() {
  try {
    const response = await dashboardApi.deleteQaPairs(selectedIds.value)
    message.success(t('qaPairs.deleteSuccess', { count: response.deleted || 0 }))
    await loadQaPairs()
  } catch (error) {
    console.error('批量删除失败', error)
    message.error(error?.message || t('qaPairs.deleteFailed'))
  }
}

async function deleteOne(record) {
  try {
    await dashboardApi.deleteQaPairs([record.id])
    message.success(t('qaPairs.deleteSuccess', { count: 1 }))
    await loadQaPairs()
  } catch (error) {
    console.error('删除问答对失败', error)
    message.error(error?.message || t('qaPairs.deleteFailed'))
  }
}

// ------------------------------------------------------------- 编辑内容
const editVisible = ref(false)
const editSaving = ref(false)
const editTarget = ref(null)
const editForm = reactive({ question: '', answer: '' })

function openEdit(record) {
  editTarget.value = record
  editForm.question = record.question
  editForm.answer = record.answer
  editVisible.value = true
}

async function confirmEdit() {
  if (!editForm.question.trim() || !editForm.answer.trim()) {
    message.warning(t('qaPairs.editEmpty'))
    return
  }
  editSaving.value = true
  try {
    await dashboardApi.updateQaPair(editTarget.value.id, {
      question: editForm.question.trim(),
      answer: editForm.answer.trim()
    })
    message.success(t('qaPairs.editSuccess'))
    editVisible.value = false
    await loadQaPairs()
  } catch (error) {
    console.error('修改问答对失败', error)
    message.error(error?.message || t('qaPairs.editFailed'))
  } finally {
    editSaving.value = false
  }
}

onMounted(() => {
  loadQaPairs()
  loadAgents()
})
</script>

<style scoped lang="less">
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}
.filter-select { width: 140px; }
.query-input { width: 280px; }
.refresh-btn { margin-left: auto; }

.batch-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  margin-bottom: 12px;
  border: 1px solid var(--color-info-100);
  border-radius: 8px;
  background: var(--color-info-50);
  color: var(--gray-800);
}

.qa-text {
  color: var(--gray-900);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.source-ref {
  margin-top: 2px;
  color: var(--gray-500);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.edit-hint {
  margin: 0 0 12px;
  color: var(--gray-500);
  font-size: 12px;
}
</style>
