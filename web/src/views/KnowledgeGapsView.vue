<template>
  <div class="gap-page">
    <div class="filters">
      <a-input-search
        v-model:value="filters.query"
        :placeholder="t('gaps.searchPlaceholder')"
        allow-clear
        class="query-input"
        @search="applyFilters"
      />
      <a-select v-model:value="filters.status" :options="statusOptions" class="filter-select" @change="applyFilters" />
      <a-select v-model:value="filters.reason" :options="reasonOptions" class="filter-select" @change="applyFilters" />
      <a-select v-model:value="filters.domain" :options="domainOptions" class="filter-select" @change="applyFilters" />
      <a-button class="refresh-btn" :loading="loading" @click="loadGaps">{{ $t('common.refresh') }}</a-button>
    </div>

    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      :pagination="pagination"
      :locale="{ emptyText: $t('common.noData') }"
      row-key="id"
      :scroll="{ x: 1140 }"
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
        <template v-else-if="column.key === 'domain'">
          {{ domainLabel(record.domain) }}
        </template>
        <template v-else-if="column.key === 'kb_scope'">
          <span>{{ record.kb_scope?.join(', ') || $t('gaps.kbScopeUnspecified') }}</span>
        </template>
        <template v-else-if="column.key === 'last_seen_at'">
          {{ formatFullDateTime(record.last_seen_at) }}
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-button type="link" @click="openAnswer(record)">{{ $t('gaps.answerAction') }}</a-button>
        </template>
      </template>
    </a-table>

    <a-drawer v-model:open="detailOpen" :title="t('gaps.detailTitle')" width="min(520px, 100vw)">
      <a-descriptions v-if="detail" :column="1" bordered size="small">
        <a-descriptions-item :label="t('eval.questionColumn')">{{ detail.question }}</a-descriptions-item>
        <a-descriptions-item :label="t('common.status')">{{ statusLabel(detail.status) }}</a-descriptions-item>
        <a-descriptions-item :label="t('gaps.reasonLabel')">{{ reasonLabel(detail.reason) }}</a-descriptions-item>
        <a-descriptions-item :label="t('gaps.businessDomainLabel')">{{ domainLabel(detail.domain) }}</a-descriptions-item>
        <a-descriptions-item :label="t('gaps.occurrenceCountLabel')">{{ detail.occurrence_count }}</a-descriptions-item>
        <a-descriptions-item :label="$t('gaps.agentLabel')">{{ detail.agent_name || detail.agent_slug }}</a-descriptions-item>
        <a-descriptions-item :label="t('gaps.kbScopeLabel')">{{ detail.kb_scope?.join(', ') || $t('gaps.kbScopeUnspecified') }}</a-descriptions-item>
        <a-descriptions-item :label="t('gaps.recentUserLabel')">{{ detail.uid || '-' }}</a-descriptions-item>
        <a-descriptions-item :label="t('gaps.recentConversationLabel')">{{ detail.conversation_thread_id || '-' }}</a-descriptions-item>
        <a-descriptions-item :label="t('gaps.firstSeenLabel')">{{ formatFullDateTime(detail.first_seen_at) }}</a-descriptions-item>
        <a-descriptions-item :label="t('gaps.lastSeenLabel')">{{ formatFullDateTime(detail.last_seen_at) }}</a-descriptions-item>
        <a-descriptions-item :label="t('gaps.resolutionNoteLabel')">{{ detail.resolution_note || '-' }}</a-descriptions-item>
      </a-descriptions>
      <a-space v-if="detail" direction="vertical" class="drawer-actions">
        <a-button type="primary" block @click="openAnswer(detail)">{{ $t('gaps.answerAndSaveLabel') }}</a-button>
      </a-space>
    </a-drawer>

    <a-modal
      v-model:open="webSearchOpen"
      :title="t('gaps.manualAnswerTitle')"
      width="760px"
      :ok-text="t('gaps.confirmAndSaveLabel')"
      :cancel-text="t('common.cancel')"
      :confirm-loading="savingQa"
      :ok-button-props="{ disabled: webSearching || !webAnswer.trim() }"
      @ok="saveWebQa"
    >
      <div class="answer-hint">
        <Info :size="16" class="answer-hint-icon" />
        <div class="answer-hint-body">
          <p class="answer-hint-title">{{ t('gaps.answerHintTitle') }}</p>
          <p class="answer-hint-desc">{{ t('gaps.answerHintDesc') }}</p>
        </div>
      </div>

      <div class="question-card">
        <div class="question-label">{{ t('gaps.uncoveredQuestionLabel') }}</div>
        <div class="question-text">{{ webSearchGap?.question || '-' }}</div>
      </div>

      <div class="draft-row">
        <span class="agent-chip">
          <Bot :size="15" />
          {{ t('gaps.agentInfo', { name: webSearchGap?.agent_name || webSearchGap?.agent_slug || '-' }) }}
        </span>
        <a-button :loading="webSearching" @click="runWebSearch">
          <template #icon><Globe :size="14" /></template>
          {{ t('gaps.webSearchGenerateLabel') }}
        </a-button>
      </div>

      <div v-if="webSearching" class="draft-loading">
        <LoaderCircle :size="18" class="spin" />
        <span>{{ t('gaps.webSearchLoadingText') }}</span>
      </div>

      <template v-else>
        <div class="answer-field">
          <div class="field-label">
            {{ t('gaps.answerFieldLabel') }}<span class="field-required">*</span>
          </div>
          <a-textarea
            v-model:value="webAnswer"
            :rows="8"
            :maxlength="20000"
            show-count
            :placeholder="t('gaps.answerPlaceholder')"
          />
        </div>

        <div class="source-section">
          <div class="source-header">
            <span class="source-title">{{ t('gaps.sourceTitle') }}</span>
            <span v-if="webSources.length" class="source-count">{{ webSources.length }}</span>
          </div>
          <a-empty v-if="webSources.length === 0" :image="simpleImage" :description="t('gaps.noSources')" />
          <div v-else class="source-list">
            <a
              v-for="(source, index) in webSources"
              :key="`${source.url}-${index}`"
              class="source-item"
              :href="source.url"
              target="_blank"
              rel="noopener noreferrer"
            >
              <span class="source-index">{{ index + 1 }}</span>
              <span class="source-body">
                <span class="source-name">{{ source.title || source.url }}</span>
                <span v-if="source.content" class="source-snippet" :title="source.content">
                  {{ source.content }}
                </span>
              </span>
            </a>
          </div>
        </div>
      </template>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { Empty, message } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import { Bot, Globe, Info, LoaderCircle } from 'lucide-vue-next'
import { dashboardApi } from '@/apis/dashboard_api'
import { formatFullDateTime } from '@/utils/time'
import { useConfigStore } from '@/stores/config'

const { t } = useI18n()
const configStore = useConfigStore()

const statusOptions = computed(() => [
  { label: t('gaps.statusAll'), value: '' },
  { label: t('gaps.statusNew'), value: 'new' },
  { label: t('gaps.statusProcessing'), value: 'processing' },
  { label: t('gaps.statusResolved'), value: 'resolved' },
  { label: t('gaps.statusIgnored'), value: 'ignored' }
])
const reasonOptions = computed(() => [
  { label: t('gaps.reasonAll'), value: '' },
  { label: t('gaps.reasonNoEnabledKb'), value: 'no_enabled_knowledge_base' },
  { label: t('gaps.reasonNoResults'), value: 'no_results' },
  { label: t('gaps.reasonEmptyContent'), value: 'empty_content' },
  { label: t('gaps.reasonInsufficientEvidence'), value: 'insufficient_evidence' },
  { label: t('gaps.reasonNoEvidenceOutput'), value: 'no_evidence_output' }
])
const domainOptions = computed(() => [
  { label: t('gaps.businessDomainAll'), value: '' },
  ...(configStore.config.business_lines || []).map((line) => ({ label: line.name, value: line.code })),
  { label: t('gaps.businessDomainUnknown'), value: 'unknown' }
])
const columns = computed(() => [
  { title: t('eval.questionColumn'), key: 'question', width: 360 },
  { title: t('gaps.occurrenceCountColumn'), dataIndex: 'occurrence_count', width: 80 },
  { title: t('common.reason'), key: 'reason', width: 140 },
  { title: t('gaps.businessDomainLabel'), key: 'domain', width: 140 },
  { title: t('common.status'), key: 'status', width: 100 },
  { title: t('gaps.lastSeenLabel'), key: 'last_seen_at', width: 170 },
  { title: t('gaps.actionsColumn'), key: 'actions', width: 150, fixed: 'right' }
])

const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const detailOpen = ref(false)
const detail = ref(null)
const filters = reactive({ query: '', status: '', reason: '', domain: '' })

const webSearchOpen = ref(false)
const webSearching = ref(false)
const savingQa = ref(false)
const webSearchGap = ref(null)
const webAnswer = ref('')
const webSources = ref([])
const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE

const pagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  total: total.value,
  showSizeChanger: true,
  showQuickJumper: true,
  pageSizeOptions: ['10', '20', '50', '100'],
  showTotal: (value) => t('gaps.totalCount', { total: value })
}))

const statusLabel = (status) => statusOptions.value.find((item) => item.value === status)?.label || status
const reasonLabel = (reason) => reasonOptions.value.find((item) => item.value === reason)?.label || reason
const domainLabel = (domain) => domainOptions.value.find((item) => item.value === domain)?.label || domain
const statusColor = (status) => ({ new: 'blue', processing: 'orange', resolved: 'green', ignored: 'default' })[status]

async function loadGaps() {
  loading.value = true
  try {
    const response = await dashboardApi.getKnowledgeGaps({
      status: filters.status,
      reason: filters.reason,
      domain: filters.domain,
      query: filters.query.trim(),
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value
    })
    items.value = response.items || []
    total.value = response.total || 0
  } catch (error) {
    console.error('加载知识缺口失败', error)
    message.error(error?.message || t('gaps.loadGapsFailed'))
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
    message.error(error?.message || t('gaps.loadDetailFailed'))
  }
}

function openAnswer(record) {
  webSearchGap.value = { ...record }
  // 已补答过的缺口：回显已存答案（后端详情/列表带 answer 字段），便于查看或重新编辑
  webAnswer.value = record.answer || ''
  webSources.value = []
  webSearchOpen.value = true
}

async function runWebSearch() {
  if (!webSearchGap.value?.id) return
  webSearching.value = true
  try {
    const response = await dashboardApi.searchKnowledgeGapAnswer(webSearchGap.value.id)
    webAnswer.value = response.draft_answer || ''
    webSources.value = Array.isArray(response.sources) ? response.sources : []
    if (!webAnswer.value) {
      message.warning(t('gaps.webSearchNoDraft'))
    }
  } catch (error) {
    console.error('联网搜索生成草稿失败', error)
    message.error(error?.message || t('gaps.webSearchDraftFailed'))
  } finally {
    webSearching.value = false
  }
}

async function saveWebQa() {
  const answer = webAnswer.value.trim()
  if (!webSearchGap.value?.id || !answer) {
    message.warning(t('gaps.fillAnswerFirst'))
    return
  }

  savingQa.value = true
  try {
    const response = await dashboardApi.saveKnowledgeGapQaPair(webSearchGap.value.id, {
      answer,
      sources: webSources.value
    })
    message.success(t('gaps.saveQaSuccess'))
    webSearchOpen.value = false
    if (detail.value?.id === response.gap?.id) detail.value = response.gap
    await loadGaps()
  } catch (error) {
    console.error('保存问答对失败', error)
    message.error(error?.message || t('gaps.saveQaFailed'))
  } finally {
    savingQa.value = false
  }
}

onMounted(loadGaps)
</script>

<style scoped lang="less">
.filters {
  display: flex;
  gap: 12px;
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  flex-wrap: wrap; // 320px 搜索框 + 3 个 180px 下拉约需 980px，窄屏必须换行
}
.query-input { width: 320px; }
.filter-select { width: 180px; }
.refresh-btn { margin-left: auto; }
.question-link { height: auto; padding: 0; text-align: left; white-space: normal; }
.drawer-actions { width: 100%; margin-top: 20px; }

/* ---------- 人工补答弹窗 ---------- */
.answer-hint {
  display: flex;
  gap: 10px;
  margin-bottom: 20px;
  padding: 12px 14px;
  border: 1px solid var(--color-info-100);
  border-radius: 8px;
  background: var(--color-info-50);
}
.answer-hint-icon { flex: 0 0 auto; margin-top: 2px; color: var(--color-info-700); }
.answer-hint-body { min-width: 0; }
.answer-hint-title {
  margin: 0;
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.6;
}
.answer-hint-desc {
  margin: 4px 0 0;
  color: var(--gray-600);
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
.question-label { margin-bottom: 6px; color: var(--gray-600); font-size: 12px; }
.question-text {
  max-height: 120px;
  overflow-y: auto;
  color: var(--gray-900);
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}
.draft-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  padding: 10px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}
.agent-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  overflow: hidden;
  color: var(--gray-600);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.draft-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 220px;
  color: var(--gray-600);
  font-size: 13px;
}
.answer-field { margin-bottom: 16px; }
.field-label {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 6px;
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 600;
}
.field-required { color: var(--color-error-500); }
.source-section {
  padding-top: 14px;
  border-top: 1px solid var(--gray-150);
}
.source-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}
.source-title { color: var(--gray-900); font-size: 13px; font-weight: 600; }
.source-count {
  padding: 0 8px;
  border-radius: 999px;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 12px;
  line-height: 18px;
}
.source-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 240px;
  overflow-y: auto;
}
.source-item {
  display: flex;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-25);
  color: inherit;
  text-decoration: none;

  &:hover {
    border-color: var(--main-50);
    background: var(--gray-0);
  }

  &:focus-visible {
    outline: 2px solid var(--main-color);
    outline-offset: 1px;
  }
}
.source-index {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--gray-100);
  color: var(--gray-600);
  font-size: 11px;
  font-weight: 600;
}
.source-body {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
.source-name {
  color: var(--main-700);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.5;
  word-break: break-word;
}
.source-snippet {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  color: var(--gray-600);
  font-size: 12px;
  line-height: 1.5;
}
.spin { animation: spin 1s linear infinite; }

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
