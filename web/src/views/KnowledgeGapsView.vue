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
          <a-button type="link" @click="openAnswer(record)">补答</a-button>
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
      <a-space v-if="detail" direction="vertical" class="drawer-actions">
        <a-button type="primary" block @click="openAnswer(detail)">补答并保存问答对</a-button>
      </a-space>
    </a-drawer>

    <a-modal
      v-model:open="webSearchOpen"
      title="人工补答"
      width="760px"
      ok-text="确认并保存问答对"
      cancel-text="取消"
      :confirm-loading="savingQa"
      :ok-button-props="{ disabled: webSearching || !webAnswer.trim() }"
      @ok="saveWebQa"
    >
      <a-alert
        type="info"
        show-icon
        class="web-search-alert"
        message="可直接输入答案，也可联网搜索生成草稿"
        description="保存的答案会作为人工问答对，同一智能体再次收到相同问题时才优先使用，不会自动进入普通问答。"
      />

      <a-form layout="vertical">
        <a-form-item label="未覆盖问题">
          <a-textarea :value="webSearchGap?.question || ''" :rows="2" readonly />
        </a-form-item>

        <div class="web-search-toolbar">
          <span class="web-search-agent">Agent：{{ webSearchGap?.agent_slug || '-' }}</span>
          <a-button :loading="webSearching" @click="runWebSearch">联网搜索生成草稿</a-button>
        </div>

        <div v-if="webSearching" class="web-search-loading">
          <a-spin />
          <span>正在联网检索并生成候选答案...</span>
        </div>

        <template v-else>
          <a-form-item label="人工确认答案" required>
            <a-textarea
              v-model:value="webAnswer"
              :rows="8"
              :maxlength="20000"
              show-count
              placeholder="可直接输入答案；点击上方「联网搜索生成草稿」可自动生成候选后编辑"
            />
          </a-form-item>

          <div class="source-section">
            <div class="source-title">参考来源</div>
            <a-empty v-if="webSources.length === 0" :image="simpleImage" description="暂无可展示来源" />
            <div v-else class="source-list">
              <div v-for="(source, index) in webSources" :key="`${source.url}-${index}`" class="source-item">
                <a :href="source.url" target="_blank" rel="noopener noreferrer">
                  {{ index + 1 }}. {{ source.title || source.url }}
                </a>
                <p v-if="source.content">{{ source.content }}</p>
              </div>
            </div>
          </div>
        </template>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { Empty, message } from 'ant-design-vue'
import { dashboardApi } from '@/apis/dashboard_api'
import { formatFullDateTime } from '@/utils/time'

const statusOptions = [
  { label: '全部状态', value: '' },
  { label: '新发现', value: 'new' },
  { label: '处理中', value: 'processing' },
  { label: '已解决', value: 'resolved' },
  { label: '已忽略', value: 'ignored' }
]
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
  { title: '操作', key: 'actions', width: 150, fixed: 'right' }
]

const loading = ref(false)
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const detailOpen = ref(false)
const detail = ref(null)
const filters = reactive({ query: '', status: '', reason: '' })

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

function openAnswer(record) {
  webSearchGap.value = { ...record }
  webAnswer.value = ''
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
      message.warning('联网检索完成，但未生成候选答案，请结合来源人工填写')
    }
  } catch (error) {
    console.error('联网搜索生成草稿失败', error)
    message.error(error?.message || '联网搜索生成草稿失败')
  } finally {
    webSearching.value = false
  }
}

async function saveWebQa() {
  const answer = webAnswer.value.trim()
  if (!webSearchGap.value?.id || !answer) {
    message.warning('请先确认并填写答案')
    return
  }

  savingQa.value = true
  try {
    const response = await dashboardApi.saveKnowledgeGapQaPair(webSearchGap.value.id, {
      answer,
      sources: webSources.value
    })
    message.success('已保存人工问答对，知识缺口已标记为已解决')
    webSearchOpen.value = false
    if (detail.value?.id === response.gap?.id) detail.value = response.gap
    await loadGaps()
  } catch (error) {
    console.error('保存问答对失败', error)
    message.error(error?.message || '保存问答对失败')
  } finally {
    savingQa.value = false
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
.drawer-actions { width: 100%; margin-top: 20px; }
.web-search-alert { margin-bottom: 18px; }
.web-search-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.web-search-agent { color: var(--gray-600); font-size: 13px; }
.web-search-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 180px;
  color: var(--gray-600);
}
.source-section {
  padding-top: 14px;
  border-top: 1px solid var(--gray-150);
}
.source-title {
  margin-bottom: 10px;
  color: var(--gray-900);
  font-weight: 600;
}
.source-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  max-height: 260px;
  overflow-y: auto;
}
.source-item {
  padding: 10px 12px;
  background: var(--gray-25);
  border: 1px solid var(--gray-100);
  border-radius: 6px;
  a { font-size: 13px; font-weight: 500; }
  p {
    margin: 6px 0 0;
    color: var(--gray-600);
    font-size: 12px;
    line-height: 1.5;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
}
</style>
