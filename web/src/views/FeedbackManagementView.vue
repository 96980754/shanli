<template>
  <div class="feedback-page">
    <div class="page-header">
      <div>
        <h1>{{ $t('feedback.pageTitle') }}</h1>
        <p>{{ $t('feedback.pageSubtitle') }}</p>
      </div>
      <a-button :loading="loading" @click="loadFeedbacks">{{ $t('common.refresh') }}</a-button>
    </div>

    <div class="filters">
      <a-select
        v-model:value="filters.rating"
        :options="ratingOptions"
        class="filter-select"
        @change="applyFilters"
      />
      <a-select
        v-model:value="filters.status"
        :options="statusOptions"
        class="filter-select"
        @change="applyFilters"
      />
      <a-select
        v-model:value="filters.agent_id"
        :options="agentOptions"
        class="filter-select"
        allow-clear
        @change="applyFilters"
      />
      <a-input-search
        v-model:value="filters.keyword"
        :placeholder="t('feedback.searchPlaceholder')"
        allow-clear
        class="query-input"
        @search="applyFilters"
      />
    </div>

    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      :pagination="pagination"
      :locale="{ emptyText: t('feedback.noFeedbackData') }"
      row-key="id"
      :scroll="{ x: 1200 }"
      @change="handleTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'rating'">
          <a-tag :color="record.rating === 'like' ? 'green' : 'volcano'">
            {{ ratingLabel(record.rating) }}
          </a-tag>
        </template>

        <template v-else-if="column.key === 'message'">
          <a-tooltip :title="record.message_content" placement="topLeft">
            <div class="message-content">{{ record.message_content || '-' }}</div>
          </a-tooltip>
          <div v-if="record.reason" class="message-reason">{{ displayReason(record.reason) }}</div>
          <div class="message-tags">
            <a-tag v-if="record.is_refusal_source" color="purple">{{ $t('feedback.refusalSourceTag') }}</a-tag>
            <a-tag v-if="record.has_qa_pair" color="cyan">{{ $t('feedback.hasQaPairTag') }}</a-tag>
          </div>
        </template>

        <template v-else-if="column.key === 'conversation'">
          <a-button
            v-if="record.conversation_thread_id"
            type="link"
            class="conversation-link"
            @click="openConversation(record)"
          >
            {{ record.conversation_title || record.conversation_thread_id }}
          </a-button>
          <span v-else>-</span>
          <div class="agent-id">{{ record.agent_id }}</div>
        </template>

        <template v-else-if="column.key === 'user'">
          <div class="user-cell">
            <FallbackAvatar
              :src="record.avatar"
              :default-src="record.uid ? generatePixelAvatar(record.uid) : ''"
              :name="record.username"
              :seed="record.uid || record.username"
              kind="user"
              :size="28"
              shape="circle"
              :alt="record.username"
            />
            <div class="user-details">
              <div class="username">{{ record.username || $t('feedback.unknownUser') }}</div>
              <div v-if="record.uid" class="uid">{{ record.uid }}</div>
            </div>
          </div>
        </template>

        <template v-else-if="column.key === 'status'">
          <a-select
            :value="record.status"
            :options="statusOptions"
            size="small"
            class="status-select"
            :class="`status-${record.status}`"
            @change="(value) => updateStatus(record, value)"
          />
        </template>

        <template v-else-if="column.key === 'created_at'">
          {{ formatFullDateTime(record.created_at) }}
        </template>

        <template v-else-if="column.key === 'actions'">
          <a-space :size="0" wrap>
            <a-button type="link" size="small" @click="openTuning(record)">{{ $t('feedback.tuneAnswer') }}</a-button>
            <a-button type="link" size="small" @click="openConversation(record)">
              {{ $t('feedback.viewConversationAction') }}
            </a-button>
            <a-button type="link" size="small" @click="copyMessage(record)">
              {{ $t('feedback.copyAction') }}
            </a-button>
          </a-space>
        </template>
      </template>
    </a-table>

    <a-drawer
      v-model:open="conversationOpen"
      :title="t('feedback.conversationDrawerTitle')"
      width="560"
    >
      <a-spin v-if="conversationLoading" />
      <template v-else-if="conversation">
        <div class="conversation-meta">
          <div class="conversation-title">{{ conversation.title }}</div>
          <a-space>
            <a-tag>{{ conversation.agent_id }}</a-tag>
            <span class="meta-muted">{{ conversation.message_count }} msgs</span>
          </a-space>
        </div>
        <div class="message-list">
          <div
            v-for="msg in conversation.messages"
            :key="msg.id"
            class="message-row"
            :class="msg.role"
          >
            <div class="message-head">
              <a-tag :color="msg.role === 'user' ? 'blue' : 'green'" size="small">
                {{ msg.role === 'user' ? $t('feedback.roleUser') : $t('feedback.roleAssistant') }}
              </a-tag>
              <span class="meta-muted">{{ formatFullDateTime(msg.created_at) }}</span>
            </div>
            <div class="message-body">{{ msg.content }}</div>
          </div>
        </div>
      </template>
    </a-drawer>

    <FeedbackTuningModal ref="tuningModal" @saved="loadFeedbacks" />
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import { dashboardApi } from '@/apis/dashboard_api'
import { agentApi } from '@/apis/agent_api'
import { formatFullDateTime } from '@/utils/time'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import { formatFeedbackReason } from '@/utils/feedbackReason'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import FeedbackTuningModal from '@/components/dashboard/FeedbackTuningModal.vue'

const { t } = useI18n()

const ratingOptions = computed(() => [
  { label: t('feedback.ratingAll'), value: '' },
  { label: t('feedback.likeLabel'), value: 'like' },
  { label: t('feedback.dislikeLabel'), value: 'dislike' }
])
const statusOptions = computed(() => [
  { label: t('feedback.statusPending'), value: 'pending' },
  { label: t('feedback.statusProcessed'), value: 'processed' },
  { label: t('feedback.statusIgnored'), value: 'ignored' }
])
const columns = computed(() => [
  { title: t('feedback.ratingColumn'), key: 'rating', width: 80 },
  { title: t('feedback.messageColumn'), key: 'message', width: 280 },
  { title: t('feedback.conversationColumn'), key: 'conversation', width: 180 },
  { title: t('feedback.userColumn'), key: 'user', width: 160 },
  { title: t('feedback.statusColumn'), key: 'status', width: 120 },
  { title: t('feedback.timeColumn'), key: 'created_at', width: 170 },
  { title: t('feedback.actionsColumn'), key: 'actions', width: 230, fixed: 'right' }
])

const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filters = reactive({ rating: '', status: '', agent_id: '', keyword: '' })
const agentOptions = ref([])

const conversationOpen = ref(false)
const conversationLoading = ref(false)
const conversation = ref(null)
const tuningModal = ref(null)

const pagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  total: total.value,
  showSizeChanger: true,
  showQuickJumper: true,
  pageSizeOptions: ['10', '20', '50', '100'],
  showTotal: (value) => t('feedback.totalCount', { total: value })
}))

const ratingLabel = (rating) =>
  ratingOptions.value.find((item) => item.value === rating)?.label || rating

const displayReason = (reason) => formatFeedbackReason(reason, t)

async function loadFeedbacks() {
  loading.value = true
  try {
    const response = await dashboardApi.getFeedbacks({
      rating: filters.rating,
      status: filters.status,
      keyword: filters.keyword.trim(),
      agent_id: filters.agent_id,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value
    })
    items.value = response.items || []
    total.value = response.total || 0
  } catch (error) {
    console.error('加载反馈列表失败', error)
    message.error(error?.message || t('feedback.loadFeedbackFailed'))
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  loadFeedbacks()
}

function handleTableChange(next) {
  page.value = next.current
  pageSize.value = next.pageSize
  loadFeedbacks()
}

async function loadAgents() {
  try {
    const response = await agentApi.getAgents()
    agentOptions.value = [
      { label: t('feedback.agentAll'), value: '' },
      ...(response.agents || []).map((agent) => ({
        label: agent.name || agent.agent_id || agent.slug || agent.id,
        value: agent.agent_id || agent.slug || agent.id
      }))
    ]
  } catch (error) {
    console.error('加载智能体列表失败', error)
    agentOptions.value = [{ label: t('feedback.agentAll'), value: '' }]
  }
}

async function updateStatus(record, status) {
  if (record.status === status) return
  const previous = record.status
  try {
    await dashboardApi.updateFeedbackStatus(record.id, status)
    record.status = status
    message.success(t('feedback.statusUpdateSuccess'))
  } catch (error) {
    console.error('更新反馈状态失败', error)
    record.status = previous
    message.error(error?.message || t('feedback.statusUpdateFailed'))
  }
}

function openTuning(record) {
  tuningModal.value?.show(record.id)
}

async function openConversation(record) {
  if (!record.conversation_thread_id) return
  conversationOpen.value = true
  conversationLoading.value = true
  conversation.value = null
  try {
    conversation.value = await dashboardApi.getConversationDetail(record.conversation_thread_id)
  } catch (error) {
    console.error('加载会话上下文失败', error)
    message.error(error?.message || t('feedback.loadConversationFailed'))
  } finally {
    conversationLoading.value = false
  }
}

async function copyMessage(record) {
  const content = record.message_content || ''
  try {
    await navigator.clipboard.writeText(content)
    message.success(t('feedback.copySuccess'))
  } catch (error) {
    console.error('复制原文失败', error)
    message.error(t('feedback.copyFailed'))
  }
}

onMounted(() => {
  loadFeedbacks()
  loadAgents()
})
</script>

<style scoped lang="less">
.feedback-page {
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
  flex-wrap: wrap;
  gap: 12px;
  padding: 16px;
  margin-bottom: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}
.filter-select { width: 140px; }
.query-input { width: 280px; }

.message-content {
  color: var(--gray-900);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.message-reason {
  margin-top: 2px;
  color: var(--gray-500);
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.message-tags { margin-top: 4px; }
.message-tags :deep(.ant-tag) { margin-bottom: 0; }
.conversation-link { height: auto; padding: 0; }
.agent-id { color: var(--gray-500); font-size: 12px; }
.user-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  .user-details { min-width: 0; }
  .username {
    color: var(--gray-900);
    font-size: 13px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .uid { color: var(--gray-500); font-size: 12px; }
}
.status-select { width: 104px; }
.status-pending { color: var(--color-warning-700); }
.status-processed { color: var(--color-success-700); }
.status-ignored { color: var(--gray-500); }

.conversation-title {
  color: var(--gray-900);
  font-weight: 600;
  margin-bottom: 6px;
}
.conversation-meta { margin-bottom: 16px; }
.meta-muted { color: var(--gray-500); font-size: 12px; }
.message-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.message-row {
  padding: 10px 12px;
  border: 1px solid var(--gray-100);
  border-radius: 6px;
  background: var(--gray-0);
  &.user { border-left: 3px solid var(--color-info-500); }
  &.assistant { border-left: 3px solid var(--color-success-500); }
}
.message-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.message-body {
  color: var(--gray-800);
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.6;
  font-size: 13px;
}
</style>
