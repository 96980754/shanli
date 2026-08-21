<template>
  <section class="feedback-summary-section">
    <div class="section-header">
      <div>
        <h3 class="section-title">答案反馈统计</h3>
        <p class="section-subtitle">汇总用户点赞、点踩及不满意原因，便于持续优化答案质量</p>
      </div>
      <a-button size="small" :loading="loading" @click="loadSummary">刷新</a-button>
    </div>

    <div v-if="loading && !summary" class="summary-loading">
      <a-spin />
    </div>

    <template v-else>
      <div class="metric-grid">
        <div class="metric-card">
          <div class="metric-label">总反馈</div>
          <div class="metric-value">{{ summary?.total_feedbacks || 0 }}</div>
        </div>
        <div class="metric-card positive">
          <div class="metric-label">点赞</div>
          <div class="metric-value">{{ summary?.like_count || 0 }}</div>
        </div>
        <div class="metric-card negative">
          <div class="metric-label">点踩</div>
          <div class="metric-value">{{ summary?.dislike_count || 0 }}</div>
        </div>
        <div class="metric-card satisfaction">
          <div class="metric-label">满意度</div>
          <div class="metric-value">{{ formatRate(summary?.satisfaction_rate) }}</div>
        </div>
      </div>

      <div class="reason-panel">
        <div class="reason-panel-header">
          <span class="reason-title">点踩原因分布</span>
          <span class="reason-total">共 {{ summary?.dislike_count || 0 }} 条点踩</span>
        </div>

        <div v-if="reasonRows.length" class="reason-list">
          <div v-for="item in reasonRows" :key="item.code" class="reason-row">
            <div class="reason-meta">
              <span class="reason-label">{{ item.label }}</span>
              <span class="reason-count">{{ item.count }}</span>
            </div>
            <div class="reason-track">
              <div class="reason-bar" :style="{ width: `${getReasonPercent(item.count)}%` }"></div>
            </div>
          </div>
        </div>

        <a-empty v-else :image="simpleImage" description="暂无点踩原因数据" class="reason-empty" />
      </div>
    </template>
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { Empty, message } from 'ant-design-vue'
import { dashboardApi } from '@/apis/dashboard_api'

const props = defineProps({
  agentId: {
    type: String,
    default: null
  }
})

const summary = ref(null)
const loading = ref(false)
const simpleImage = Empty.PRESENTED_IMAGE_SIMPLE

const reasonRows = computed(() => {
  const rows = Array.isArray(summary.value?.reason_stats) ? [...summary.value.reason_stats] : []
  const legacyCount = Number(summary.value?.legacy_unclassified_count || 0)
  if (legacyCount > 0) {
    rows.push({ code: 'legacy_unclassified', label: '历史未分类', count: legacyCount })
  }
  return rows.filter((item) => Number(item.count || 0) > 0)
})

const formatRate = (rate) =>
  `${Number(rate || 0)
    .toFixed(2)
    .replace(/\.00$/, '')}%`

const getReasonPercent = (count) => {
  const total = Number(summary.value?.dislike_count || 0)
  if (total <= 0) return 0
  return Math.min(100, Math.round((Number(count || 0) / total) * 100))
}

const loadSummary = async () => {
  loading.value = true
  try {
    summary.value = await dashboardApi.getFeedbackSummary({
      agent_id: props.agentId || undefined
    })
  } catch (error) {
    console.error('加载反馈统计失败:', error)
    message.error('加载反馈统计失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

watch(
  () => props.agentId,
  () => loadSummary()
)

onMounted(() => loadSummary())

defineExpose({ refresh: loadSummary })
</script>

<style scoped lang="less">
.feedback-summary-section {
  margin: 16px var(--page-padding) 0;
  padding: 20px;
  background: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 12px;
}

.section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.section-title {
  margin: 0;
  color: var(--gray-1000);
  font-size: 16px;
  font-weight: 600;
}

.section-subtitle {
  margin: 4px 0 0;
  color: var(--gray-500);
  font-size: 12px;
}

.summary-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 180px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 20px;
}

.metric-card {
  padding: 14px 16px;
  background: var(--gray-25);
  border: 1px solid var(--gray-100);
  border-radius: 8px;

  &.positive {
    border-left: 3px solid var(--color-success-500);
  }

  &.negative {
    border-left: 3px solid var(--color-error-500);
  }

  &.satisfaction {
    border-left: 3px solid var(--main-color);
  }
}

.metric-label {
  color: var(--gray-500);
  font-size: 12px;
}

.metric-value {
  margin-top: 6px;
  color: var(--gray-1000);
  font-size: 24px;
  font-weight: 700;
  line-height: 1.1;
}

.reason-panel {
  padding-top: 16px;
  border-top: 1px solid var(--gray-100);
}

.reason-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.reason-title {
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 600;
}

.reason-total {
  color: var(--gray-500);
  font-size: 12px;
}

.reason-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px 24px;
}

.reason-row {
  min-width: 0;
}

.reason-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.reason-label {
  color: var(--gray-700);
  font-size: 13px;
}

.reason-count {
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 600;
}

.reason-track {
  height: 7px;
  overflow: hidden;
  background: var(--gray-100);
  border-radius: 999px;
}

.reason-bar {
  min-width: 0;
  height: 100%;
  background: var(--main-color);
  border-radius: inherit;
  transition: width 0.25s ease;
}

.reason-empty {
  margin: 8px 0 0;
}

@media (max-width: 900px) {
  .metric-grid,
  .reason-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 600px) {
  .feedback-summary-section {
    margin: 12px 0 0;
    padding: 16px;
  }

  .metric-grid,
  .reason-list {
    grid-template-columns: 1fr;
  }

  .section-header {
    align-items: center;
  }
}
</style>
