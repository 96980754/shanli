<template>
  <div class="gap-page">
    <div class="page-header">
      <div>
        <h1>知识缺口</h1>
        <p>查看知识库无法覆盖的用户问题，并跟踪补充处理状态。</p>
      </div>
      <a-button :loading="loading" @click="loadGaps">刷新</a-button>
    </div>

    <div class="filters">
      <a-input-search
        v-model:value="filters.query"
        placeholder="搜索问题"
        allow-clear
        class="query-input"
        @search="applyFilters"
      />
      <a-select v-model:value="filters.status" :options="statusOptions" class="filter-select" @change="applyFilters" />
      <a-select v-model:value="filters.reason" :options="reasonOptions" class="filter-select" @change="applyFilters" />
    </div>

    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      :pagination="pagination"
      row-key="id"
      @change="handleTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'question'">
          <a-button type="link" class="question-link" @click="openDetail(record.id)">
            {{ record.question }}
          </a-button>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="statusColor(record.status)">{{ statusLabel(record.status) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'reason'">
          {{ reasonLabel(record.reason) }}
        </template>
        <template v-else-if="column.key === 'kb_scope'">
          <span>{{ record.kb_scope?.join(', ') || '未指定' }}</span>
        </template>
        <template v-else-if="column.key === 'last_seen_at'">
          {{ formatFullDateTime(record.last_seen_at) }}
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-button type="link" @click="openUpdate(record)">处理</a-button>
        </template>
      </template>
    </a-table>

    <a-drawer v-model:open="detailOpen" title="知识缺口详情" width="520">
      <a-descriptions v-if="detail" :column="1" bordered size="small">
        <a-descriptions-item label="问题">{{ detail.question }}</a-descriptions-item>
        <a-descriptions-item label="状态">{{ statusLabel(detail.status) }}</a-descriptions-item>
        <a-descriptions-item label="拒答原因">{{ reasonLabel(detail.reason) }}</a-descriptions-item>
        <a-descriptions-item label="出现次数">{{ detail.occurrence_count }}</a-descriptions-item>
        <a-descriptions-item label="Agent">{{ detail.agent_slug }}</a-descriptions-item>
        <a-descriptions-item label="知识库范围">{{ detail.kb_scope?.join(', ') || '未指定' }}</a-descriptions-item>
        <a-descriptions-item label="最近用户">{{ detail.uid || '-' }}</a-descriptions-item>
        <a-descriptions-item label="最近会话">{{ detail.conversation_thread_id || '-' }}</a-descriptions-item>
        <a-descriptions-item label="首次出现">{{ formatFullDateTime(detail.first_seen_at) }}</a-descriptions-item>
        <a-descriptions-item label="最近出现">{{ formatFullDateTime(detail.last_seen_at) }}</a-descriptions-item>
        <a-descriptions-item label="处理备注">{{ detail.resolution_note || '-' }}</a-descriptions-item>
      </a-descriptions>
      <a-button v-if="detail" type="primary" block class="drawer-action" @click="openUpdate(detail)">更新处理状态</a-button>
    </a-drawer>

    <a-modal v-model:open="updateOpen" title="处理知识缺口" @ok="submitUpdate" :confirm-loading="updating">
      <a-form layout="vertical">
        <a-form-item label="处理状态">
          <a-select v-model:value="updateForm.status" :options="editableStatusOptions" />
        </a-form-item>
        <a-form-item label="处理备注">
          <a-textarea v-model:value="updateForm.resolution_note" :rows="5" :maxlength="2000" show-count />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { dashboardApi } from '@/apis/dashboard_api'
import { formatFullDateTime } from '@/utils/time'

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '新发现', value: 'new' },
  { label: '处理中', value: 'processing' },
  { label: '已解决', value: 'resolved' },
  { label: '已忽略', value: 'ignored' }
]
const editableStatusOptions = statusOptions.slice(1)
const reasonOptions = [
  { label: '全部原因', value: '' },
  { label: '无可用知识库', value: 'no_enabled_knowledge_base' },
  { label: '没有检索结果', value: 'no_results' },
  { label: '检索正文为空', value: 'empty_content' },
  { label: '候选证据不足', value: 'insufficient_evidence' }
]
const columns = [
  { title: '问题', key: 'question', width: 360 },
  { title: '次数', dataIndex: 'occurrence_count', width: 80 },
  { title: '原因', key: 'reason', width: 140 },
  { title: '知识库范围', key: 'kb_scope', width: 180 },
  { title: '状态', key: 'status', width: 100 },
  { title: '最近出现', key: 'last_seen_at', width: 170 },
  { title: '操作', key: 'actions', width: 80, fixed: 'right' }
]

const loading = ref(false)
const updating = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const detailOpen = ref(false)
const updateOpen = ref(false)
const detail = ref(null)
const updatingId = ref(null)
const filters = reactive({ query: '', status: '', reason: '' })
const updateForm = reactive({ status: 'processing', resolution_note: '' })
const pagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  total: total.value,
  showSizeChanger: true,
  showQuickJumper: true,
  pageSizeOptions: ['10', '20', '50', '100'],
  showTotal: (value) => `共 ${value} 条`
}))

const statusLabel = (status) => statusOptions.find((item) => item.value === status)?.label || status
const reasonLabel = (reason) => reasonOptions.find((item) => item.value === reason)?.label || reason
const statusColor = (status) => ({ new: 'blue', processing: 'orange', resolved: 'green', ignored: 'default' })[status]

async function loadGaps() {
  loading.value = true
  try {
    const response = await dashboardApi.getKnowledgeGaps({
      status: filters.status,
      reason: filters.reason,
      query: filters.query.trim(),
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value
    })
    items.value = response.items || []
    total.value = response.total || 0
  } catch (error) {
    console.error('加载知识缺口失败', error)
    message.error(error?.message || '加载知识缺口失败')
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  loadGaps()
}

function handleTableChange(next) {
  page.value = next.current
  pageSize.value = next.pageSize
  loadGaps()
}

async function openDetail(gapId) {
  try {
    detail.value = (await dashboardApi.getKnowledgeGap(gapId)).item
    detailOpen.value = true
  } catch (error) {
    message.error(error?.message || '加载详情失败')
  }
}

function openUpdate(record) {
  updatingId.value = record.id
  updateForm.status = record.status
  updateForm.resolution_note = record.resolution_note || ''
  updateOpen.value = true
}

async function submitUpdate() {
  updating.value = true
  try {
    const response = await dashboardApi.updateKnowledgeGap(updatingId.value, updateForm)
    message.success('处理状态已更新')
    updateOpen.value = false
    if (detail.value?.id === response.item.id) detail.value = response.item
    await loadGaps()
  } catch (error) {
    message.error(error?.message || '更新知识缺口失败')
  } finally {
    updating.value = false
  }
}

onMounted(loadGaps)
</script>

<style scoped lang="less">
.gap-page {
  min-height: 100vh;
  padding: var(--page-padding);
  background: var(--gray-25);
}
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 20px;
  h1 { margin: 0 0 6px; font-size: 24px; color: var(--gray-1000); }
  p { margin: 0; color: var(--gray-600); }
}
.filters {
  display: flex;
  gap: 12px;
  padding: 16px;
  margin-bottom: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}
.query-input { width: 320px; }
.filter-select { width: 180px; }
.question-link { height: auto; padding: 0; text-align: left; white-space: normal; }
.drawer-action { margin-top: 20px; }
</style>
