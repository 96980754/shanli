<template>
  <div class="qa-records-page">
    <div class="page-header">
      <button class="back-btn" type="button" @click="backToDashboard">
        <ArrowLeft :size="16" />
        <span>{{ $t('common.back') }}</span>
      </button>
      <h1>{{ $t('qaRecords.pageTitle') }}</h1>
      <p>{{ $t('qaRecords.pageSubtitle') }}</p>
    </div>

    <div class="filters">
      <a-range-picker
        v-model:value="customRange"
        value-format="YYYY-MM-DD"
        :disabled-date="disableFutureDate"
        :placeholder="[$t('qaRecords.rangeStartPlaceholder'), $t('qaRecords.rangeEndPlaceholder')]"
        @change="applyFilters"
      />
      <a-select v-model:value="filters.domain" :options="domainOptions" class="filter-select" @change="applyFilters" />
      <a-input-search
        v-model:value="filters.keyword"
        :placeholder="t('qaRecords.searchPlaceholder')"
        allow-clear
        class="query-input"
        @search="applyFilters"
      />
      <a-button class="refresh-btn" :loading="loading" @click="loadRecords">{{ $t('common.refresh') }}</a-button>
      <a-button type="primary" :loading="exporting" @click="exportCsv">
        <template #icon><Download class="btn-icon" /></template>
        {{ $t('qaRecords.export') }}
      </a-button>
    </div>

    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      :pagination="pagination"
      :custom-row="customRow"
      row-key="id"
      :scroll="{ x: 960 }"
      @change="handleTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'question'">
          <span class="clamp-text" :title="record.question">{{ record.question || '-' }}</span>
        </template>
        <template v-else-if="column.key === 'answer'">
          <span class="clamp-text" :title="record.answer">{{ record.answer || '-' }}</span>
        </template>
        <template v-else-if="column.key === 'domain'">
          <a-tag :color="record.domain === 'unknown' ? 'default' : 'blue'">{{ domainLabel(record.domain) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'answer_type'">
          <a-tag :color="answerTypeColor(record.answer_type)">{{ answerTypeLabel(record.answer_type) }}</a-tag>
        </template>
        <template v-else-if="column.key === 'created_at'">
          {{ formatFullDateTime(record.created_at) }}
        </template>
        <template v-else-if="column.key === 'username'">
          {{ record.username || record.uid || '-' }}
        </template>
      </template>
    </a-table>

    <a-drawer v-model:open="detailOpen" :title="t('qaRecords.detailTitle')" width="min(560px, 100vw)">
      <template v-if="detail">
        <a-descriptions :column="1" bordered size="small">
          <a-descriptions-item :label="t('qaRecords.timeColumn')">
            {{ formatFullDateTime(detail.created_at) }}
          </a-descriptions-item>
          <a-descriptions-item :label="t('qaRecords.userColumn')">
            {{ detail.username || detail.uid || '-' }}
          </a-descriptions-item>
          <a-descriptions-item :label="t('qaRecords.domainColumn')">
            <a-tag :color="detail.domain === 'unknown' ? 'default' : 'blue'">{{ domainLabel(detail.domain) }}</a-tag>
          </a-descriptions-item>
          <a-descriptions-item :label="t('qaRecords.answerTypeColumn')">
            <a-tag :color="answerTypeColor(detail.answer_type)">{{ answerTypeLabel(detail.answer_type) }}</a-tag>
          </a-descriptions-item>
          <a-descriptions-item :label="t('qaRecords.agentColumn')">{{ detail.agent_id || '-' }}</a-descriptions-item>
        </a-descriptions>

        <div class="detail-block">
          <div class="detail-label">{{ t('qaRecords.questionColumn') }}</div>
          <div class="detail-text">{{ detail.question || '-' }}</div>
        </div>
        <div class="detail-block">
          <div class="detail-label">{{ t('qaRecords.answerColumn') }}</div>
          <div class="detail-text">{{ detail.answer || '-' }}</div>
        </div>
      </template>
    </a-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Download } from 'lucide-vue-next'
import dayjs, { formatFullDateTime } from '@/utils/time'
import { dashboardApi } from '@/apis/dashboard_api'
import { useConfigStore } from '@/stores/config'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const configStore = useConfigStore()

// 本页是数据总览下钻的明细页，返回总览（与 DataBaseInfoView 的返回一致，用 push 而非 back，
// 直接输 URL 进来时也有确定的落点）
const backToDashboard = () => {
  router.push({ path: '/dashboard' })
}

const domainOptions = computed(() => [
  { label: t('qaRecords.domainAll'), value: '' },
  ...(configStore.config.business_lines || []).map((line) => ({ label: line.name, value: line.code })),
  { label: t('qaRecords.domainUnknown'), value: 'unknown' }
])

const ANSWER_TYPE_META = {
  answered: { labelKey: 'qaRecords.typeAnswered', color: 'green' },
  knowledge_refusal: { labelKey: 'qaRecords.typeKnowledgeRefusal', color: 'orange' },
  scope_refusal: { labelKey: 'qaRecords.typeScopeRefusal', color: 'default' },
  policy_refusal: { labelKey: 'qaRecords.typePolicyRefusal', color: 'red' },
  system_error: { labelKey: 'qaRecords.typeSystemError', color: 'red' }
}

const columns = computed(() => [
  { title: t('qaRecords.timeColumn'), key: 'created_at', width: 170 },
  { title: t('qaRecords.userColumn'), key: 'username', width: 110 },
  { title: t('qaRecords.questionColumn'), key: 'question' },
  { title: t('qaRecords.answerColumn'), key: 'answer' },
  { title: t('qaRecords.domainColumn'), key: 'domain', width: 110 },
  { title: t('qaRecords.answerTypeColumn'), key: 'answer_type', width: 100 },
  { title: t('qaRecords.agentColumn'), dataIndex: 'agent_id', width: 120 }
])

const loading = ref(false)
const exporting = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const customRange = ref(null)
const filters = reactive({ domain: '', keyword: '' })
const detailOpen = ref(false)
const detail = ref(null)

// 点行看完整问答：表格里问题与回答都截断，长回答需在抽屉里通读
const customRow = (record) => ({
  onClick: () => {
    detail.value = record
    detailOpen.value = true
  },
  style: { cursor: 'pointer' }
})

const pagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  total: total.value,
  showSizeChanger: true,
  showQuickJumper: true,
  pageSizeOptions: ['10', '20', '50', '100'],
  showTotal: (value) => t('qaRecords.totalCount', { total: value })
}))

const domainLabel = (domain) =>
  domainOptions.value.find((item) => item.value === domain)?.label ||
  (domain === 'unknown' ? t('qaRecords.domainUnknown') : domain)

const answerTypeLabel = (type) =>
  ANSWER_TYPE_META[type] ? t(ANSWER_TYPE_META[type].labelKey) : type
const answerTypeColor = (type) => ANSWER_TYPE_META[type]?.color || 'default'

const disableFutureDate = (current) => current && current > dayjs().endOf('day')

// 当前生效的时段（null = 全部），与总览一致按北京日界传 YYYY-MM-DD
const activeRange = computed(() => {
  if (customRange.value?.[0] && customRange.value?.[1]) {
    return { start_date: customRange.value[0], end_date: customRange.value[1] }
  }
  return null
})

function queryParams() {
  return {
    ...(activeRange.value || {}),
    domain: filters.domain,
    keyword: filters.keyword.trim()
  }
}

async function loadRecords() {
  loading.value = true
  try {
    const response = await dashboardApi.getQaRecords({
      ...queryParams(),
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value
    })
    items.value = response.items || []
    total.value = response.total || 0
  } catch (error) {
    console.error('加载问答明细失败', error)
    message.error(error?.message || t('qaRecords.loadFailed'))
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  loadRecords()
}

function handleTableChange(next) {
  page.value = next.current
  pageSize.value = next.pageSize
  loadRecords()
}

async function exportCsv() {
  if (exporting.value) return
  exporting.value = true
  try {
    const response = await dashboardApi.exportQaRecords(queryParams())
    const blob = await response.blob()
    const contentDisposition =
      response.headers.get('Content-Disposition') || response.headers.get('content-disposition')
    const match = contentDisposition && contentDisposition.match(/filename\*=UTF-8''([^;]+)/)
    const filename = match ? decodeURIComponent(match[1]) : `qa_records_${Date.now()}.csv`

    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)

    message.success(t('qaRecords.exportSuccess'))
  } catch (error) {
    console.error('导出问答明细失败', error)
    message.error(error?.message || t('qaRecords.exportFailed'))
  } finally {
    exporting.value = false
  }
}

onMounted(() => {
  // 总览「问答次数」卡片跳转时携带当前时段，初始筛选与其保持一致
  const { start_date, end_date } = route.query
  if (start_date && end_date) {
    customRange.value = [String(start_date), String(end_date)]
  }
  loadRecords()
})
</script>

<style scoped lang="less">
.qa-records-page {
  min-height: 100%;
  padding: var(--page-padding);
  background: var(--gray-25);
}
.page-header {
  margin-bottom: 12px;
  h1 { margin: 0 0 6px; font-size: 24px; color: var(--gray-1000); }
  p { margin: 0; color: var(--gray-600); }
}
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 8px -8px;
  padding: 4px 8px;
  border: none;
  border-radius: 6px;
  background: none;
  color: var(--gray-500);
  font-size: 14px;
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
  &:hover { color: var(--gray-700); background: var(--gray-50); }
}
.filters {
  display: flex;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  flex-wrap: wrap;
}
.query-input { width: 280px; }
.filter-select { width: 180px; }
.refresh-btn { margin-left: auto; }
.btn-icon { width: 14px; height: 14px; }
.clamp-text {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-all;
  white-space: normal;
}
.detail-block {
  margin-top: 16px;
  .detail-label {
    font-size: 13px;
    font-weight: 600;
    color: var(--gray-800);
    margin-bottom: 6px;
  }
  .detail-text {
    padding: 10px 12px;
    border: 1px solid var(--gray-150);
    border-radius: 6px;
    background: var(--gray-25);
    color: var(--gray-900);
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 360px;
    overflow-y: auto;
  }
}
</style>
