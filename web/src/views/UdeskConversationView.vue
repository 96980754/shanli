<template>
  <div class="udesk-conversation-page">
    <div class="filters">
      <a-input-search
        v-model:value="filters.keyword"
        :placeholder="t('udeskConversations.searchPlaceholder')"
        allow-clear
        class="query-input"
        @search="applyFilters"
      />
      <a-select
        v-model:value="filters.has_candidates"
        :options="candidateOptions"
        class="filter-select"
        @change="applyFilters"
      />
      <a-button class="refresh-btn" :loading="loading" @click="loadConversations">
        {{ $t('common.refresh') }}
      </a-button>
    </div>

    <p class="page-hint">{{ t('udeskConversations.windowHint') }}</p>

    <a-table
      :columns="columns"
      :data-source="items"
      :loading="loading"
      :pagination="pagination"
      :locale="{ emptyText: t('udeskConversations.noData') }"
      row-key="conversation_id"
      :scroll="{ x: 1000 }"
      @change="handleTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'conversation_id'">
          <span class="conversation-id">{{ record.conversation_id }}</span>
        </template>

        <template v-else-if="column.key === 'started_at'">
          {{ formatFullDateTime(record.started_at) }}
        </template>

        <template v-else-if="column.key === 'message_count'">
          <span>{{ record.message_count }}</span>
          <div class="sub-text">{{ t('udeskConversations.customerMessages', { count: record.customer_message_count }) }}</div>
        </template>

        <template v-else-if="column.key === 'eligible'">
          <a-tag :color="record.eligible ? 'green' : 'default'">
            {{ record.eligible ? t('udeskConversations.eligibleYes') : t('udeskConversations.eligibleNo') }}
          </a-tag>
        </template>

        <template v-else-if="column.key === 'candidate_count'">
          <span>{{ record.candidate_count }}</span>
        </template>

        <template v-else-if="column.key === 'summarized_at'">
          {{ formatFullDateTime(record.summarized_at) }}
        </template>

        <template v-else-if="column.key === 'actions'">
          <a-button type="link" size="small" @click="openDetail(record)">
            {{ $t('udeskConversations.viewAction') }}
          </a-button>
        </template>
      </template>
    </a-table>

    <!-- 会话详情（拉取侧已脱敏的消息原文） -->
    <a-drawer
      v-model:open="detailVisible"
      :title="t('udeskConversations.detailTitle')"
      width="min(560px, 100vw)"
      :destroy-on-close="true"
    >
      <a-spin :spinning="detailLoading">
        <div v-if="detailError" class="detail-error">{{ detailError }}</div>
        <template v-else-if="detailConversation">
          <div class="detail-meta">
            <div>{{ t('udeskConversations.detailConversation') }}：{{ detailConversation.conversation_id }}</div>
            <div v-if="detailConversation.started_at">
              {{ t('udeskConversations.detailStartedAt') }}：{{ formatFullDateTime(detailConversation.started_at) }}
            </div>
          </div>
          <div class="detail-messages">
            <div v-for="msg in detailMessages" :key="msg.id" class="detail-msg" :class="`role-${msg.role}`">
              <span class="role-tag">{{ roleLabel(msg.role) }}</span>
              <div class="msg-content">{{ msg.content }}</div>
            </div>
          </div>
        </template>
      </a-spin>
    </a-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import { dashboardApi } from '@/apis/dashboard_api'
import { formatFullDateTime } from '@/utils/time'

const { t } = useI18n()

const candidateOptions = computed(() => [
  { label: t('udeskConversations.candidateAll'), value: '' },
  { label: t('udeskConversations.candidateYes'), value: 'true' },
  { label: t('udeskConversations.candidateNo'), value: 'false' }
])
const columns = computed(() => [
  { title: t('udeskConversations.conversationColumn'), key: 'conversation_id', width: 160 },
  { title: t('udeskConversations.startedAtColumn'), key: 'started_at', width: 170 },
  { title: t('udeskConversations.messagesColumn'), key: 'message_count', width: 130 },
  { title: t('udeskConversations.eligibleColumn'), key: 'eligible', width: 110 },
  { title: t('udeskConversations.candidatesColumn'), key: 'candidate_count', width: 100 },
  { title: t('udeskConversations.summarizedColumn'), key: 'summarized_at', width: 170 },
  { title: t('udeskConversations.actionsColumn'), key: 'actions', width: 100, fixed: 'right' }
])

const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const filters = reactive({ keyword: '', has_candidates: '' })

const pagination = computed(() => ({
  current: page.value,
  pageSize: pageSize.value,
  total: total.value,
  showSizeChanger: true,
  showQuickJumper: true,
  pageSizeOptions: ['10', '20', '50', '100'],
  showTotal: (value) => t('qaPairs.totalCount', { total: value })
}))

const roleLabel = (role) =>
  ({
    customer: t('candidates.roleCustomer'),
    agent: t('candidates.roleAgent'),
    robot: t('candidates.roleRobot'),
    system: t('candidates.roleSystem')
  })[role] || role

async function loadConversations() {
  loading.value = true
  try {
    const response = await dashboardApi.getUdeskConversations({
      keyword: filters.keyword.trim(),
      has_candidates: filters.has_candidates,
      limit: pageSize.value,
      offset: (page.value - 1) * pageSize.value
    })
    items.value = response.items || []
    total.value = response.total || 0
  } catch (error) {
    console.error('加载客服会话列表失败', error)
    message.error(error?.message || t('udeskConversations.loadFailed'))
  } finally {
    loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  loadConversations()
}

function handleTableChange(next) {
  page.value = next.current
  pageSize.value = next.pageSize
  loadConversations()
}

// ------------------------------------------------------------- 会话详情
const detailVisible = ref(false)
const detailLoading = ref(false)
const detailConversation = ref(null)
const detailMessages = ref([])
const detailError = ref('')

async function openDetail(record) {
  detailVisible.value = true
  detailLoading.value = true
  detailConversation.value = null
  detailMessages.value = []
  detailError.value = ''
  try {
    const response = await dashboardApi.getUdeskConversation(record.conversation_id)
    detailConversation.value = response.conversation || null
    detailMessages.value = response.messages || []
  } catch (error) {
    console.error('加载会话详情失败', error)
    detailError.value = error?.message || t('udeskConversations.detailFailed')
  } finally {
    detailLoading.value = false
  }
}

onMounted(() => {
  loadConversations()
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
.filter-select { width: 160px; }
.query-input { width: 280px; }
.refresh-btn { margin-left: auto; }

.page-hint {
  margin: 8px 0 12px;
  color: var(--gray-500);
  font-size: 12px;
}

.conversation-id {
  font-family: var(--font-mono, monospace);
  color: var(--gray-900);
}
.sub-text {
  margin-top: 2px;
  color: var(--gray-500);
  font-size: 12px;
}

.detail-error {
  color: var(--color-error-700);
}
.detail-meta {
  margin-bottom: 12px;
  color: var(--gray-600);
  font-size: 12px;
}
.detail-messages {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.detail-msg {
  padding: 8px 10px;
  border-radius: 6px;
  background: var(--gray-50);

  &.role-customer { background: var(--color-primary-50); }
  &.role-system { background: var(--gray-100); }

  .role-tag {
    display: inline-block;
    margin-bottom: 4px;
    color: var(--gray-600);
    font-size: 12px;
  }
  .msg-content {
    color: var(--gray-900);
    white-space: pre-wrap;
    word-break: break-word;
  }
}
</style>
