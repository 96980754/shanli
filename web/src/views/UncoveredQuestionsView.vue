<template>
  <div class="uncovered-page">
    <header class="page-header">
      <div>
        <h1>未覆盖问题</h1>
        <p>集中查看知识库无法回答的问题，并跟踪补充知识的处理进度。</p>
      </div>
      <a-button :loading="loading" @click="loadQuestions">
        <template #icon><ReloadOutlined /></template>
        刷新
      </a-button>
    </header>

    <section class="summary-grid" aria-label="未覆盖问题统计">
      <div class="summary-card">
        <span>筛选结果</span>
        <strong>{{ total }}</strong>
      </div>
      <div class="summary-card">
        <span>本页出现次数</span>
        <strong>{{ pageOccurrenceCount }}</strong>
      </div>
      <div class="summary-card">
        <span>本页最高相关度</span>
        <strong>{{ highestScoreText }}</strong>
      </div>
    </section>

    <a-card class="filter-card" :bordered="false">
      <a-form layout="inline" class="filter-form" @submit.prevent="handleSearch">
        <a-form-item label="状态">
          <a-select
            v-model:value="filters.status"
            allow-clear
            placeholder="全部状态"
            style="width: 150px"
            :options="statusOptions"
          />
        </a-form-item>
        <a-form-item label="原因">
          <a-select
            v-model:value="filters.reason"
            allow-clear
            placeholder="全部原因"
            style="width: 180px"
            :options="reasonOptions"
          />
        </a-form-item>
        <a-form-item label="智能体">
          <a-input
            v-model:value="filters.agentId"
            allow-clear
            placeholder="输入智能体 ID"
            style="width: 190px"
            @press-enter="handleSearch"
          />
        </a-form-item>
        <a-form-item label="关键词" class="keyword-filter">
          <a-input-search
            v-model:value="filters.keyword"
            allow-clear
            placeholder="问题、UID 或会话 ID"
            style="width: 260px"
            @search="handleSearch"
          />
        </a-form-item>
        <a-form-item>
          <a-space>
            <a-button type="primary" :loading="loading" @click="handleSearch">查询</a-button>
            <a-button @click="resetFilters">重置</a-button>
          </a-space>
        </a-form-item>
      </a-form>
    </a-card>

    <a-card class="table-card" :bordered="false">
      <a-table
        row-key="id"
        :columns="columns"
        :data-source="questions"
        :loading="loading"
        :pagination="false"
        :scroll="{ x: 1250 }"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'question'">
            <button type="button" class="question-link" @click="openDetail(record.id)">
              {{ record.question }}
            </button>
          </template>

          <template v-else-if="column.key === 'status'">
            <a-tag :color="statusMeta[record.status]?.color || 'default'">
              {{ statusMeta[record.status]?.label || record.status }}
            </a-tag>
          </template>

          <template v-else-if="column.key === 'reason'">
            <a-tooltip :title="record.reason">
              <span>{{ reasonLabel(record.reason) }}</span>
            </a-tooltip>
          </template>

          <template v-else-if="column.key === 'top_score'">
            <span>{{ formatScore(record.top_score) }}</span>
          </template>

          <template v-else-if="column.key === 'last_seen_at'">
            <span>{{ formatDate(record.last_seen_at) }}</span>
          </template>

          <template v-else-if="column.key === 'action'">
            <a-space>
              <a-button type="link" size="small" @click="openDetail(record.id)">详情</a-button>
              <a-button type="link" size="small" @click="openUpdate(record)">处理</a-button>
            </a-space>
          </template>
        </template>

        <template #emptyText>
          <a-empty description="暂无符合条件的未覆盖问题" />
        </template>
      </a-table>

      <div class="pagination-row">
        <span>共 {{ total }} 条</span>
        <a-pagination
          v-model:current="pagination.current"
          v-model:page-size="pagination.pageSize"
          :total="total"
          :page-size-options="['10', '20', '50', '100']"
          show-size-changer
          show-quick-jumper
          @change="handlePageChange"
          @show-size-change="handlePageSizeChange"
        />
      </div>
    </a-card>

    <a-drawer v-model:open="detailOpen" title="未覆盖问题详情" width="560" :destroy-on-close="true">
      <div v-if="detailLoading" class="drawer-loading"><a-spin /></div>
      <template v-else-if="selectedQuestion">
        <div class="detail-question">{{ selectedQuestion.question }}</div>
        <a-descriptions :column="1" bordered size="small">
          <a-descriptions-item label="状态">
            <a-tag :color="statusMeta[selectedQuestion.status]?.color || 'default'">
              {{ statusMeta[selectedQuestion.status]?.label || selectedQuestion.status }}
            </a-tag>
          </a-descriptions-item>
          <a-descriptions-item label="出现次数">
            {{ selectedQuestion.occurrence_count }}
          </a-descriptions-item>
          <a-descriptions-item label="拒答原因">
            {{ reasonLabel(selectedQuestion.reason) }}
          </a-descriptions-item>
          <a-descriptions-item label="最高相关度">
            {{ formatScore(selectedQuestion.top_score) }}
          </a-descriptions-item>
          <a-descriptions-item label="智能体 ID">
            {{ selectedQuestion.agent_id }}
          </a-descriptions-item>
          <a-descriptions-item label="知识库 ID">
            <a-space wrap>
              <a-tag v-for="kbId in selectedQuestion.kb_ids" :key="kbId">{{ kbId }}</a-tag>
              <span v-if="!selectedQuestion.kb_ids?.length">—</span>
            </a-space>
          </a-descriptions-item>
          <a-descriptions-item label="用户 UID">
            {{ selectedQuestion.uid }}
          </a-descriptions-item>
          <a-descriptions-item label="会话 ID">
            <span class="break-all">{{ selectedQuestion.thread_id }}</span>
          </a-descriptions-item>
          <a-descriptions-item label="助手消息 ID">
            {{ selectedQuestion.assistant_message_id ?? '—' }}
          </a-descriptions-item>
          <a-descriptions-item label="首次出现">
            {{ formatDate(selectedQuestion.first_seen_at) }}
          </a-descriptions-item>
          <a-descriptions-item label="最近出现">
            {{ formatDate(selectedQuestion.last_seen_at) }}
          </a-descriptions-item>
          <a-descriptions-item label="解决时间">
            {{ formatDate(selectedQuestion.resolved_at) }}
          </a-descriptions-item>
          <a-descriptions-item label="处理备注">
            <span class="pre-wrap">{{ selectedQuestion.resolution_note || '—' }}</span>
          </a-descriptions-item>
        </a-descriptions>
      </template>
      <template #extra>
        <a-button v-if="selectedQuestion" type="primary" @click="openUpdate(selectedQuestion)">
          更新状态
        </a-button>
      </template>
    </a-drawer>

    <a-modal
      v-model:open="updateOpen"
      title="处理未覆盖问题"
      ok-text="保存"
      cancel-text="取消"
      :confirm-loading="updating"
      @ok="submitUpdate"
    >
      <a-form layout="vertical">
        <a-form-item label="问题">
          <div class="modal-question">{{ updatingQuestion?.question }}</div>
        </a-form-item>
        <a-form-item label="状态" required>
          <a-select v-model:value="updateForm.status" :options="statusOptions" />
        </a-form-item>
        <a-form-item label="处理备注">
          <a-textarea
            v-model:value="updateForm.resolutionNote"
            :rows="5"
            :maxlength="2000"
            show-count
            placeholder="记录知识补充、忽略原因或后续处理计划"
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { dashboardApi } from '@/apis/dashboard_api'
import { formatFullDateTime } from '@/utils/time'

const statusMeta = {
  new: { label: '新发现', color: 'red' },
  processing: { label: '处理中', color: 'orange' },
  resolved: { label: '已解决', color: 'green' },
  ignored: { label: '已忽略', color: 'default' }
}

const statusOptions = Object.entries(statusMeta).map(([value, meta]) => ({
  value,
  label: meta.label
}))

const reasonMeta = {
  no_result: '无检索结果',
  empty_content: '检索内容为空',
  low_relevance: '相关度不足',
  missing_answer_evidence: '缺少回答依据',
  conflicting_evidence: '证据冲突'
}

const reasonOptions = Object.entries(reasonMeta).map(([value, label]) => ({ value, label }))

const columns = [
  { title: '问题', key: 'question', dataIndex: 'question', width: 360 },
  { title: '状态', key: 'status', dataIndex: 'status', width: 100 },
  { title: '原因', key: 'reason', dataIndex: 'reason', width: 130 },
  { title: '出现次数', key: 'occurrence_count', dataIndex: 'occurrence_count', width: 100 },
  { title: '最高相关度', key: 'top_score', dataIndex: 'top_score', width: 120 },
  { title: '智能体', key: 'agent_id', dataIndex: 'agent_id', width: 160, ellipsis: true },
  { title: '最近出现', key: 'last_seen_at', dataIndex: 'last_seen_at', width: 180 },
  { title: '操作', key: 'action', fixed: 'right', width: 120 }
]

const loading = ref(false)
const questions = ref([])
const total = ref(0)
const filters = reactive({
  status: undefined,
  reason: undefined,
  agentId: '',
  keyword: ''
})
const pagination = reactive({ current: 1, pageSize: 20 })

const detailOpen = ref(false)
const detailLoading = ref(false)
const selectedQuestion = ref(null)

const updateOpen = ref(false)
const updating = ref(false)
const updatingQuestion = ref(null)
const updateForm = reactive({ status: 'processing', resolutionNote: '' })

const pageOccurrenceCount = computed(() =>
  questions.value.reduce((sum, item) => sum + Number(item.occurrence_count || 0), 0)
)

const highestScoreText = computed(() => {
  const scores = questions.value
    .map((item) => Number(item.top_score))
    .filter((value) => Number.isFinite(value))
  return scores.length ? Math.max(...scores).toFixed(4) : '—'
})

const reasonLabel = (reason) => reasonMeta[reason] || reason || '—'
const formatScore = (score) => {
  const numericScore = Number(score)
  return Number.isFinite(numericScore) ? numericScore.toFixed(4) : '—'
}
const formatDate = (value) => (value ? formatFullDateTime(value) : '—')

const loadQuestions = async () => {
  loading.value = true
  try {
    const response = await dashboardApi.getUncoveredQuestions({
      status: filters.status || undefined,
      reason: filters.reason || undefined,
      agent_id: filters.agentId.trim() || undefined,
      q: filters.keyword.trim() || undefined,
      limit: pagination.pageSize,
      offset: (pagination.current - 1) * pagination.pageSize
    })
    questions.value = response.items || []
    total.value = Number(response.total || 0)
  } catch (error) {
    console.error('加载未覆盖问题失败:', error)
    message.error(error.message || '加载未覆盖问题失败')
    questions.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

const handleSearch = () => {
  pagination.current = 1
  loadQuestions()
}

const resetFilters = () => {
  filters.status = undefined
  filters.reason = undefined
  filters.agentId = ''
  filters.keyword = ''
  pagination.current = 1
  loadQuestions()
}

const handlePageChange = (page) => {
  pagination.current = page
  loadQuestions()
}

const handlePageSizeChange = (_current, size) => {
  pagination.current = 1
  pagination.pageSize = size
  loadQuestions()
}

const openDetail = async (questionId) => {
  detailOpen.value = true
  detailLoading.value = true
  selectedQuestion.value = null
  try {
    selectedQuestion.value = await dashboardApi.getUncoveredQuestion(questionId)
  } catch (error) {
    console.error('加载未覆盖问题详情失败:', error)
    message.error(error.message || '加载详情失败')
    detailOpen.value = false
  } finally {
    detailLoading.value = false
  }
}

const openUpdate = (record) => {
  updatingQuestion.value = record
  updateForm.status = record.status || 'processing'
  updateForm.resolutionNote = record.resolution_note || ''
  updateOpen.value = true
}

const submitUpdate = async () => {
  if (!updatingQuestion.value?.id) return
  updating.value = true
  try {
    const updated = await dashboardApi.updateUncoveredQuestion(updatingQuestion.value.id, {
      status: updateForm.status,
      resolution_note: updateForm.resolutionNote.trim() || null
    })
    message.success('处理状态已更新')
    updateOpen.value = false
    if (selectedQuestion.value?.id === updated.id) {
      selectedQuestion.value = updated
    }
    await loadQuestions()
  } catch (error) {
    console.error('更新未覆盖问题失败:', error)
    message.error(error.message || '更新失败')
  } finally {
    updating.value = false
  }
}

onMounted(loadQuestions)
</script>

<style scoped lang="less">
.uncovered-page {
  min-height: 100%;
  padding: 24px;
  background: var(--gray-25);
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;

  h1 {
    margin: 0 0 6px;
    color: var(--gray-1000);
    font-size: 24px;
    font-weight: 650;
  }

  p {
    margin: 0;
    color: var(--gray-600);
    font-size: 14px;
  }
}

.summary-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.summary-card {
  display: flex;
  min-height: 88px;
  flex-direction: column;
  justify-content: center;
  padding: 16px 18px;
  border: 1px solid var(--gray-100);
  border-radius: 10px;
  background: var(--gray-0);

  span {
    margin-bottom: 6px;
    color: var(--gray-600);
    font-size: 13px;
  }

  strong {
    color: var(--gray-1000);
    font-size: 24px;
    line-height: 1;
  }
}

.filter-card,
.table-card {
  border: 1px solid var(--gray-100);
  border-radius: 10px;
  background: var(--gray-0);
}

.filter-card {
  margin-bottom: 16px;
}

.filter-form {
  row-gap: 12px;
}

.keyword-filter {
  flex: 1 1 280px;
}

.question-link {
  display: block;
  width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--main-color);
  text-align: left;
  cursor: pointer;
  word-break: break-word;

  &:hover {
    text-decoration: underline;
  }
}

.pagination-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-top: 16px;
  color: var(--gray-600);
}

.drawer-loading {
  display: flex;
  justify-content: center;
  padding: 80px 0;
}

.detail-question,
.modal-question {
  padding: 14px 16px;
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  background: var(--gray-25);
  color: var(--gray-1000);
  line-height: 1.7;
  word-break: break-word;
}

.detail-question {
  margin-bottom: 16px;
  font-size: 16px;
  font-weight: 600;
}

.break-all {
  word-break: break-all;
}

.pre-wrap {
  white-space: pre-wrap;
}

@media (max-width: 960px) {
  .uncovered-page {
    padding: 16px;
  }

  .summary-grid {
    grid-template-columns: 1fr;
  }

  .page-header,
  .pagination-row {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
