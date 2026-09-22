<template>
  <div class="candidate-page">
    <div class="filters">
      <a-select
        v-model:value="filters.review_status"
        :options="reviewOptions"
        class="filter-select"
        @change="applyFilters"
      />
      <a-select
        v-model:value="filters.dedup_status"
        :options="dedupOptions"
        class="filter-select"
        @change="applyFilters"
      />
      <a-select
        v-model:value="filters.domain"
        :options="domainOptions"
        class="filter-select"
        @change="applyFilters"
      />
      <a-input-search
        v-model:value="filters.keyword"
        :placeholder="t('candidates.searchPlaceholder')"
        allow-clear
        class="query-input"
        @search="applyFilters"
      />
      <a-button class="refresh-btn" :loading="loading" @click="loadCandidates">{{ $t('common.refresh') }}</a-button>
      <a-tooltip :title="pullBlocked ? t('candidates.pullBlocked', { fields: missingFieldLabels }) : ''">
        <span class="pull-btn-wrap">
          <a-button class="pull-btn" :loading="pullBusy" :disabled="pullBlocked" @click="triggerPull">
            {{ $t('candidates.pullNow') }}
          </a-button>
        </span>
      </a-tooltip>
      <a-tooltip :title="summarizeBlocked ? t('candidates.summarizeNoPending') : ''">
        <span class="pull-btn-wrap">
          <a-button
            class="summarize-btn"
            :loading="summarizeBusy"
            :disabled="summarizeBlocked"
            @click="triggerSummarize"
          >
            {{ $t('candidates.summarizeNow') }}
          </a-button>
        </span>
      </a-tooltip>
    </div>

    <a-alert
      v-if="pullBlocked"
      type="warning"
      show-icon
      class="pull-status"
      :message="t('candidates.pullBlocked', { fields: missingFieldLabels })"
      :description="t('candidates.pullBlockedHint')"
    />
    <div v-else class="pull-status-lines">
      <div class="status-row">
        <span class="pull-status-text">{{ pullSummary }}</span>
        <a-tag v-if="pullStatusTag" :color="pullStatusTag.color">{{ pullStatusTag.text }}</a-tag>
        <a-tooltip v-if="pullError" :title="pullError">
          <span class="pull-status-error">{{ pullError }}</span>
        </a-tooltip>
      </div>
      <div v-if="summarizeSummary" class="status-row">
        <span class="pull-status-text">{{ summarizeSummary }}</span>
        <a-tooltip v-if="summarizeError" :title="summarizeError">
          <span class="pull-status-error">{{ summarizeError }}</span>
        </a-tooltip>
      </div>
      <div v-if="summarizeGeneratedLine" class="status-row">{{ summarizeGeneratedLine }}</div>
      <div class="status-row status-counts">{{ countsLine }}</div>
      <div class="status-row status-counts">{{ syncScopeLine }}</div>
    </div>

    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      :pagination="pagination"
      :locale="{ emptyText: t('candidates.noData') }"
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

        <template v-else-if="column.key === 'evidence'">
          <a-tooltip :title="record.evidence_quote" placement="topLeft">
            <div class="qa-text evidence-text">{{ record.evidence_quote }}</div>
          </a-tooltip>
        </template>

        <template v-else-if="column.key === 'domain'">
          <a-tag v-if="record.domain">{{ domainLabel(record.domain) }}</a-tag>
          <span v-else class="muted">-</span>
        </template>

        <template v-else-if="column.key === 'confidence'">
          <span v-if="record.confidence != null">{{ (record.confidence * 100).toFixed(0) }}%</span>
          <span v-else class="muted">-</span>
          <a-tooltip v-if="record.ambiguity_note" :title="record.ambiguity_note" placement="topLeft">
            <div class="ambiguity-note">{{ record.ambiguity_note }}</div>
          </a-tooltip>
        </template>

        <template v-else-if="column.key === 'dedup_status'">
          <a-tag :color="dedupColor(record.dedup_status)">{{ dedupLabel(record.dedup_status) }}</a-tag>
        </template>

        <template v-else-if="column.key === 'review_status'">
          <a-tag :color="reviewColor(record.review_status)">{{ reviewLabel(record.review_status) }}</a-tag>
        </template>

        <template v-else-if="column.key === 'created_at'">
          {{ formatFullDateTime(record.created_at) }}
        </template>

        <template v-else-if="column.key === 'actions'">
          <a-button
            v-if="record.review_status === 'pending'"
            type="link"
            size="small"
            @click="openReview(record)"
          >
            {{ $t('candidates.reviewAction') }}
          </a-button>
        </template>
      </template>
    </a-table>

    <!-- 审核弹窗：候选问答（可微调）+ 客服原话 + 来源会话，采纳/删除都收在这里 -->
    <a-modal
      v-model:open="reviewVisible"
      :title="t('candidates.reviewTitle')"
      width="720px"
      :footer="null"
      :destroy-on-close="true"
    >
      <p class="accept-hint">{{ t('candidates.acceptHint') }}</p>
      <a-form layout="vertical">
        <a-form-item :label="t('qaPairs.questionColumn')">
          <a-textarea v-model:value="acceptForm.question" :rows="2" :maxlength="300" show-count />
        </a-form-item>
        <a-form-item :label="t('qaPairs.answerColumn')">
          <a-textarea v-model:value="acceptForm.answer" :rows="5" :maxlength="2000" show-count />
        </a-form-item>
        <a-form-item :label="t('candidates.evidenceColumn')">
          <div class="evidence-quote">{{ acceptTarget?.evidence_quote }}</div>
        </a-form-item>
      </a-form>

      <!-- 来源会话记录（脱敏消息，核对 evidence_quote 用） -->
      <div class="review-context-title">{{ t('candidates.contextTitle') }}</div>
      <a-spin :spinning="contextLoading">
        <div v-if="contextError" class="context-error">{{ contextError }}</div>
        <template v-else-if="contextConversation">
          <div class="context-meta">
            <div>{{ t('candidates.contextConversation') }}：{{ contextConversation.conversation_id }}</div>
            <div v-if="contextConversation.started_at">
              {{ t('candidates.contextStartedAt') }}：{{ formatFullDateTime(contextConversation.started_at) }}
            </div>
          </div>
          <div class="context-messages">
            <div v-for="msg in contextMessages" :key="msg.id" class="context-msg" :class="`role-${msg.role}`">
              <span class="role-tag">{{ roleLabel(msg.role) }}</span>
              <div class="msg-content">{{ msg.content }}</div>
            </div>
          </div>
        </template>
      </a-spin>

      <div class="review-footer">
        <a-button :disabled="accepting" @click="reviewVisible = false">
          {{ t('common.cancel') }}
        </a-button>
        <a-popconfirm
          :title="t('qaPairs.deleteConfirmTitle')"
          :ok-text="t('qaPairs.deleteConfirmOk')"
          @confirm="confirmDelete"
        >
          <a-button danger :loading="deleting">{{ t('qaPairs.deleteAction') }}</a-button>
        </a-popconfirm>
        <a-tooltip :title="acceptForm.agent_slug ? '' : t('candidates.agentUnavailable')">
          <span>
            <a-button
              type="primary"
              :loading="accepting"
              :disabled="!acceptForm.agent_slug"
              @click="confirmAccept"
            >
              {{ t('candidates.acceptAction') }}
            </a-button>
          </span>
        </a-tooltip>
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import { dashboardApi } from '@/apis/dashboard_api'
import { agentApi } from '@/apis/agent_api'
import { isBuiltinAgent } from '@/stores/agent'
import { useConfigStore } from '@/stores/config'
import { formatFullDateTime } from '@/utils/time'

const { t } = useI18n()
const configStore = useConfigStore()

// 审核态显示名。rejected 只对历史遗留行有意义——现在的「删除」是真删行，
// 不再产生该状态，故它只出现在显示映射里、不进筛选器（选了必然是空列表）
const REVIEW_LABEL_KEYS = {
  pending: 'candidates.reviewPending',
  accepted: 'candidates.reviewAccepted',
  rejected: 'candidates.reviewRejected'
}
const reviewOptions = computed(() => [
  { label: t('candidates.reviewAll'), value: '' },
  { label: t(REVIEW_LABEL_KEYS.pending), value: 'pending' },
  { label: t(REVIEW_LABEL_KEYS.accepted), value: 'accepted' }
])
const dedupOptions = computed(() => [
  { label: t('candidates.dedupAll'), value: '' },
  { label: t('candidates.dedupPending'), value: 'pending' },
  { label: t('candidates.dedupDuplicate'), value: 'duplicate' },
  { label: t('candidates.dedupUnique'), value: 'unique' }
])
const domainOptions = computed(() => [
  { label: t('candidates.domainAll'), value: '' },
  ...(configStore.config.business_lines || []).map((line) => ({ label: line.name, value: line.code }))
])
const columns = computed(() => [
  { title: t('qaPairs.questionColumn'), key: 'question', width: 240 },
  { title: t('qaPairs.answerColumn'), key: 'answer', width: 280 },
  { title: t('candidates.evidenceColumn'), key: 'evidence', width: 240 },
  { title: t('candidates.domainColumn'), key: 'domain', width: 110 },
  { title: t('candidates.confidenceColumn'), key: 'confidence', width: 110 },
  { title: t('candidates.dedupColumn'), key: 'dedup_status', width: 100 },
  { title: t('common.status'), key: 'review_status', width: 100 },
  { title: t('qaPairs.timeColumn'), key: 'created_at', width: 170 },
  { title: t('qaPairs.actionsColumn'), key: 'actions', width: 100, fixed: 'right' }
])

const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filters = reactive({ review_status: 'pending', dedup_status: '', domain: '', keyword: '' })
// 采纳时的归属智能体：固定为内置智能体（智能助手），弹窗里不再让用户选——
// 系统里非子智能体只有它一个，客服知识也只对它生效，一个只有单一选项的下拉框
// 只会让人以为「不选就不能提交」。取不到列表时采纳按钮保持禁用（带 tooltip 说明）。
const defaultAgentSlug = ref('')

const pagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  total: total.value,
  showSizeChanger: true,
  showTotal: (value) => t('qaPairs.totalCount', { total: value })
}))

const domainLabel = (domain) => domainOptions.value.find((item) => item.value === domain)?.label || domain
const dedupLabel = (status) => dedupOptions.value.find((item) => item.value === status)?.label || status
const reviewLabel = (status) => (REVIEW_LABEL_KEYS[status] ? t(REVIEW_LABEL_KEYS[status]) : status)
const dedupColor = (status) =>
  ({ pending: 'default', duplicate: 'orange', conflict: 'red', unique: 'green' })[status] || 'default'
const reviewColor = (status) => ({ pending: 'default', accepted: 'green', rejected: 'red' })[status] || 'default'
const roleLabel = (role) =>
  ({
    customer: t('candidates.roleCustomer'),
    agent: t('candidates.roleAgent'),
    robot: t('candidates.roleRobot'),
    system: t('candidates.roleSystem')
  })[role] || role

async function loadCandidates() {
  loading.value = true
  try {
    const response = await dashboardApi.getQaCandidates({
      review_status: filters.review_status,
      dedup_status: filters.dedup_status,
      domain: filters.domain,
      keyword: filters.keyword.trim(),
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value
    })
    items.value = response.items || []
    total.value = response.total || 0
  } catch (error) {
    console.error('加载候选知识列表失败', error)
    message.error(error?.message || t('candidates.loadFailed'))
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  loadCandidates()
}

function handleTableChange(next) {
  page.value = next.current
  pageSize.value = next.pageSize
  loadCandidates()
}

async function loadAgents() {
  try {
    const response = await agentApi.getAgents()
    const agents = response.agents || []
    const builtin = agents.find((agent) => isBuiltinAgent(agent))
    defaultAgentSlug.value = builtin ? builtin.agent_id || builtin.slug || builtin.id : ''
  } catch (error) {
    console.error('加载智能体列表失败', error)
    defaultAgentSlug.value = ''
  }
}

// ------------------------------------------------------------- 审核弹窗（采纳/拒绝/会话记录）
const reviewVisible = ref(false)
const accepting = ref(false)
const deleting = ref(false)
const acceptTarget = ref(null)
const acceptForm = reactive({ agent_slug: '', question: '', answer: '' })
const contextLoading = ref(false)
const contextConversation = ref(null)
const contextMessages = ref([])
const contextError = ref('')

// 打开审核弹窗：带出候选问答与默认归属，并加载来源会话供核对客服原话
function openReview(record) {
  acceptTarget.value = record
  acceptForm.agent_slug = defaultAgentSlug.value
  acceptForm.question = record.question
  acceptForm.answer = record.answer
  contextConversation.value = null
  contextMessages.value = []
  contextError.value = ''
  reviewVisible.value = true
  contextLoading.value = true
  dashboardApi
    .getQaCandidateContext(record.id)
    .then((response) => {
      contextConversation.value = response.conversation
      contextMessages.value = response.messages || []
    })
    .catch((error) => {
      console.error('加载来源会话失败', error)
      contextError.value = error?.message || t('candidates.contextLoadFailed')
    })
    .finally(() => {
      contextLoading.value = false
    })
}

async function confirmAccept() {
  accepting.value = true
  try {
    await dashboardApi.acceptQaCandidate(acceptTarget.value.id, {
      agent_slug: acceptForm.agent_slug,
      question: acceptForm.question.trim(),
      answer: acceptForm.answer.trim()
    })
    message.success(t('candidates.acceptSuccess'))
    reviewVisible.value = false
    await loadCandidates()
    await loadUdeskStatus() // 待审数变了，计数行要跟着走，否则与列表条数对不上
  } catch (error) {
    console.error('采纳候选失败', error)
    message.error(error?.message || t('candidates.acceptFailed'))
  } finally {
    accepting.value = false
  }
}

async function confirmDelete() {
  deleting.value = true
  try {
    const response = await dashboardApi.deleteQaCandidate(acceptTarget.value.id)
    message.success(t('qaPairs.deleteSuccess', { count: response.deleted || 0 }))
    reviewVisible.value = false
    await loadCandidates()
    await loadUdeskStatus() // 候选行少了一条，累计数要跟着走
  } catch (error) {
    console.error('删除候选失败', error)
    message.error(error?.message || t('qaPairs.deleteFailed'))
  } finally {
    deleting.value = false
  }
}

// ------------------------------------------------------------- 手动拉取
// 拉取在 arq 队列里异步执行，页面上要能看到「能不能拉、拉到哪了、上轮结果」，
// 否则点了按钮只有一句「已排队」，配置缺失时更是一个空跑任务。
// 回灌一轮要逐会话拉日志，实测分钟级（≈3–4 分钟），故排队等待上限给到 3 分钟；
// 已经在跑时不受该上限约束，一直跟到租约释放为止。
const PULL_POLL_MS = 3000
const PULL_POLL_MAX = 60

const MISSING_FIELD_LABEL_KEY = {
  enabled: 'settings.udeskEnabledLabel',
  subdomain: 'settings.udeskSubdomainLabel',
  email: 'settings.udeskEmailLabel',
  open_api_token: 'settings.udeskTokenLabel'
}
const PULL_STATUS_TAG = {
  succeeded: { color: 'green', key: 'candidates.pullStatusSucceeded' },
  failed: { color: 'red', key: 'candidates.pullStatusFailed' },
  skipped_lease: { color: 'default', key: 'candidates.pullStatusSkipped' }
}

const pulling = ref(false)
const polling = ref(false)
const udeskStatus = ref(null)

let pollTimer = null
let pollCount = 0
let sawRunning = false
let baselineRunAt = null

const pullBusy = computed(() => pulling.value || polling.value)
const pullBlocked = computed(() => (udeskStatus.value?.missing_fields || []).length > 0)
const missingFieldLabels = computed(() =>
  (udeskStatus.value?.missing_fields || [])
    .map((key) => t(MISSING_FIELD_LABEL_KEY[key] || key))
    .join(', ')
)
const pullState = computed(() => udeskStatus.value?.pull || null)
const summarize = computed(() => udeskStatus.value?.summarize || {})
const counts = computed(() => udeskStatus.value?.counts || {})

// 状态区只给甲方看结论不给过程：同步中只说「同步中...」，平时只留上次时间（带数量原文案已删）；
// 失败仍给出状态标签与错误信息，否则故障不可见。
const pullStatusTag = computed(() => {
  const status = pullState.value?.last_run_status
  // 运行中由状态文案本身表达，再挂个标签只是重复
  if (status === 'running') return null
  const tag = PULL_STATUS_TAG[status]
  return tag ? { color: tag.color, text: t(tag.key) } : null
})
const pullError = computed(() =>
  pullState.value?.last_run_status === 'failed' ? pullState.value.last_error || '' : ''
)
const pullSummary = computed(() => {
  const state = pullState.value
  if (!state?.last_run_status) return t('candidates.pullNever')
  if (state.last_run_status === 'running') return t('candidates.pullRunning')
  return t('candidates.pullLastRun', { time: formatFullDateTime(state.last_run_at) })
})

async function loadUdeskStatus() {
  try {
    udeskStatus.value = await dashboardApi.getUdeskStatus()
  } catch {
    udeskStatus.value = null // 无权限或接口异常时不展示状态，不影响其它操作
  }
}

function stopPoll() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
  polling.value = false
}

function startPoll() {
  pollCount = 0
  sawRunning = false
  stopPoll()
  polling.value = true
  pollTimer = setInterval(pollPull, PULL_POLL_MS)
}

// 本轮结束的判定：租约释放（!running）且本轮确实跑过——要么轮询期间见到过 running，
// 要么 last_run_at 已被本轮刷新（触发前的基线值变了）
async function pollPull() {
  await loadUdeskStatus()
  const state = pullState.value
  pollCount += 1
  if (state?.running) sawRunning = true
  const finished = state && !state.running && (sawRunning || state.last_run_at !== baselineRunAt)
  if (finished) {
    stopPoll()
    await loadCandidates() // 拉取结果直接体现在候选列表上
  } else if (!state?.running && pollCount >= PULL_POLL_MAX) {
    stopPoll() // 一直在排队没开跑：不再轮询，交由用户手动刷新
  }
}

async function triggerPull() {
  pulling.value = true
  baselineRunAt = pullState.value?.last_run_at ?? null
  try {
    await dashboardApi.triggerUdeskPull()
    message.success(t('candidates.pullQueued'))
    startPoll()
  } catch (error) {
    console.error('触发拉取失败', error)
    message.error(error?.message || t('candidates.pullFailed'))
  } finally {
    pulling.value = false
  }
}

// ------------------------------------------------------------- 手动总结（两步流程第二步）
// 拉取入库后，由管理员手动点「生成候选问答」送 LLM 结构化（cron 每小时也会兜底跑一轮）。
// 进度不另设计数器：已完成 = 会话总数 − 待总结数，由状态接口现算，天然单调；
// 运行与否看总结租约（与拉取租约相互独立，两者可以同时在跑）。
const SUMMARIZE_POLL_MS = 5000
const SUMMARIZE_POLL_MAX = 36 // 约 3 分钟，超时停止轮询（cron 会兜底，无需无限等）

const summarizeTriggering = ref(false)
const summarizePolling = ref(false)
let summarizeTimer = null
let summarizePollCount = 0
let lastPending = null

const summarizePending = computed(() => summarize.value.pending ?? 0)
const summarizeBusy = computed(() => summarizeTriggering.value || summarizePolling.value)
const summarizeBlocked = computed(() => summarizePending.value === 0)
// 总结行与拉取行同口径：不带数量与进度，生成中只说「生成中...」；
// 没有待总结时整行不显示（按钮的禁用 tooltip 已说明原因）
const summarizeSummary = computed(() => {
  if (summarize.value.running) return t('candidates.summarizeRunning')
  if (summarizePending.value > 0) return t('candidates.summarizePending')
  return ''
})
const summarizeError = computed(() =>
  summarize.value.last_run_status === 'failed' ? summarize.value.last_error || '' : ''
)
// 计数行只报待审数：累计口径（采纳不删行）与列表默认的待审过滤对不上，甲方只关心还有多少要审
const countsLine = computed(() =>
  t('candidates.countsLine', { pending: counts.value.candidates_pending ?? 0 })
)
// 新生成条数是**单轮**口径，由后端在总结收尾时落库；null 表示还没跑过任何一轮，
// 0 是有效值（跑了但一条新候选都没产出），两者要分开
const summarizeGeneratedLine = computed(() => {
  const generated = summarize.value.last_candidates
  return generated == null ? '' : t('candidates.summarizeGenerated', { count: generated })
})
// 同步范围：首次回灌多少天 + 之后每轮往前多回看多久。甲方问过「同步的是多久的记录」，
// 写在页面上省得再问——两个值都取自后端生效配置，不是前端写死的文案
const syncScopeLine = computed(() => {
  const backfillDays = udeskStatus.value?.backfill_start_days
  const overlapMinutes = udeskStatus.value?.sync_overlap_minutes
  if (backfillDays == null || overlapMinutes == null) return ''
  return t('candidates.syncScope', { days: backfillDays, minutes: overlapMinutes })
})

function stopSummarizePoll() {
  if (summarizeTimer) clearInterval(summarizeTimer)
  summarizeTimer = null
  summarizePolling.value = false
}

function startSummarizePoll() {
  summarizePollCount = 0
  lastPending = summarizePending.value
  stopSummarizePoll()
  summarizePolling.value = true
  summarizeTimer = setInterval(pollSummarize, SUMMARIZE_POLL_MS)
}

async function pollSummarize() {
  await loadUdeskStatus()
  summarizePollCount += 1
  if (summarizePending.value !== lastPending) {
    lastPending = summarizePending.value
    await loadCandidates() // 候选随总结陆续生成，列表实时可见
  }
  if (summarizePending.value === 0 || summarizePollCount >= SUMMARIZE_POLL_MAX) {
    stopSummarizePoll()
    await loadCandidates()
  }
}

async function triggerSummarize() {
  summarizeTriggering.value = true
  try {
    await dashboardApi.triggerUdeskSummarize()
    message.success(t('candidates.summarizeQueued'))
    startSummarizePoll()
  } catch (error) {
    console.error('触发总结失败', error)
    message.error(error?.message || t('candidates.summarizeFailed'))
  } finally {
    summarizeTriggering.value = false
  }
}

// ------------------------------------------------------------- 状态兜底轮询
// 拉取/总结在后台各有 cron（每日/每小时）兜底；不点按钮时页面也要反映它们的结果，
// 否则累计行与候选列表只在手动触发的那几分钟会动，其余时间都是静态数字。
const STATUS_POLL_MS = 30000
let statusTimer = null
let statusLastPending = null

async function pollStatus() {
  // 手动触发的快速轮询（3s/5s）在跟时让位，避免同一次变化刷两遍列表
  if (polling.value || summarizePolling.value) return
  await loadUdeskStatus()
  const pending = summarizePending.value
  if (statusLastPending !== null && pending !== statusLastPending) {
    await loadCandidates() // 后台总结产出了新候选，列表同步可见
  }
  statusLastPending = pending
}

onMounted(async () => {
  loadCandidates()
  loadAgents()
  await loadUdeskStatus()
  // 刷新页面时若上一轮仍在跑（租约未过期），跟随到结束，而不是显示一个静态的「拉取中」
  if (pullState.value?.running) {
    baselineRunAt = pullState.value.last_run_at
    startPoll()
  }
  if (summarize.value.running) startSummarizePoll()
  statusLastPending = summarizePending.value
  statusTimer = setInterval(pollStatus, STATUS_POLL_MS)
})

onUnmounted(() => {
  stopPoll()
  stopSummarizePoll()
  if (statusTimer) clearInterval(statusTimer)
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
.pull-btn-wrap { display: inline-flex; }

.pull-status { margin: 0; }
.pull-status-lines {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12px;
  color: var(--gray-600);
}
.status-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.status-progress {
  flex: none;
  width: 160px;
  margin: 0;
}
.status-counts { color: var(--gray-400); }
.pull-status-text { flex: none; }
.pull-status-error {
  flex: 1;
  min-width: 0;
  color: var(--color-error-700);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.qa-text {
  color: var(--gray-900);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.evidence-text { color: var(--gray-600); font-size: 12px; }
.muted { color: var(--gray-400); }
.ambiguity-note {
  color: var(--color-warning-700);
  font-size: 12px;
  max-width: 110px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.context-meta {
  padding: 12px 0;
  color: var(--gray-600);
  font-size: 13px;
  border-bottom: 1px solid var(--gray-150);
}
// 弹窗内长会话独立滚动，避免把弹窗撑出屏幕
.context-messages { max-height: 320px; overflow-y: auto; }
.context-msg {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
  .role-tag {
    flex: none;
    width: 52px;
    color: var(--gray-500);
    font-size: 12px;
  }
  .msg-content {
    color: var(--gray-900);
    white-space: pre-wrap;
    word-break: break-word;
  }
  &.role-customer .msg-content { background: var(--gray-50); border-radius: 6px; padding: 6px 10px; }
}
.context-error { color: var(--color-error-500); padding: 24px 0; }

.accept-hint {
  margin: 0 0 12px;
  color: var(--gray-600);
  font-size: 13px;
}

.evidence-quote {
  color: var(--gray-700);
  white-space: pre-wrap;
  word-break: break-word;
  background: var(--gray-50);
  border-radius: 6px;
  padding: 8px 12px;
}

.review-context-title {
  margin: 4px 0 8px;
  font-weight: 600;
  font-size: 14px;
  color: var(--gray-900);
  border-top: 1px solid var(--gray-150);
  padding-top: 16px;
}

.review-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
  border-top: 1px solid var(--gray-150);
  padding-top: 12px;
}
</style>
