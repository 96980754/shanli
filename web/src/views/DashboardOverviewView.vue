<template>
  <div class="dashboard-overview-page">
    <div class="filters">
      <a-range-picker
        v-model:value="customRange"
        value-format="YYYY-MM-DD"
        :disabled-date="disableFutureDate"
        :placeholder="[$t('qaRecords.rangeStartPlaceholder'), $t('qaRecords.rangeEndPlaceholder')]"
        @change="loadData"
      />
      <a-button class="refresh-btn" :loading="loading" @click="loadData">{{ $t('common.refresh') }}</a-button>
    </div>

    <!-- 分类覆盖率卡片：与问答明细同口径（unknown 即未分类） -->
    <div class="stat-cards">
      <div class="stat-card">
        <div class="stat-label">{{ t('opsOverview.cardTotal') }}</div>
        <div class="stat-value">{{ coverage.total }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">{{ t('opsOverview.cardClassified') }}</div>
        <div class="stat-value">{{ coverage.classified }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">{{ t('opsOverview.cardUnclassified') }}</div>
        <div class="stat-value">{{ coverage.unclassified }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">{{ t('opsOverview.cardCoverageRate') }}</div>
        <div class="stat-value">{{ coverage.classified_rate }}%</div>
      </div>
    </div>

    <div class="chart-grid">
      <a-card class="chart-card" :title="t('opsOverview.chartDomainTitle')" :loading="loading">
        <div ref="domainChartRef" class="chart"></div>
      </a-card>
      <a-card class="chart-card" :title="t('opsOverview.chartRefusalTitle')" :loading="loading">
        <div ref="refusalChartRef" class="chart"></div>
      </a-card>
    </div>

    <a-card class="chart-card trend-card" :title="t('opsOverview.chartTrendTitle')" :loading="loading">
      <div ref="trendChartRef" class="chart"></div>
    </a-card>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import * as echarts from 'echarts'
import dayjs from 'dayjs'
import { dashboardApi } from '@/apis/dashboard_api'
import { getColorByIndex } from '@/utils/chartColors'
import { useConfigStore } from '@/stores/config'
import { useThemeStore } from '@/stores/theme'

const { t } = useI18n()
const configStore = useConfigStore()
const themeStore = useThemeStore()

function getCSSVariable(variableName, element = document.documentElement) {
  return getComputedStyle(element).getPropertyValue(variableName).trim()
}

const loading = ref(false)
const customRange = ref(null)
const stats = ref(null)
const trend = ref(null)

const coverage = computed(() => stats.value?.coverage || { total: 0, classified: 0, unclassified: 0, classified_rate: 0.0 })
const domainRows = computed(() => stats.value?.lines || [])
const REFUSAL_TYPES = [
  { key: 'knowledge_refusal', labelKey: 'qaRecords.typeKnowledgeRefusal' },
  { key: 'scope_refusal', labelKey: 'qaRecords.typeScopeRefusal' },
  { key: 'policy_refusal', labelKey: 'qaRecords.typePolicyRefusal' }
]

const domainLabel = (domain) => {
  if (domain === 'unknown') return t('qaRecords.domainUnknown')
  const line = (configStore.config.business_lines || []).find((item) => item.code === domain)
  return line ? line.name : domain
}

const disableFutureDate = (current) => current && current > dayjs().endOf('day')

const activeRange = computed(() => {
  if (customRange.value?.[0] && customRange.value?.[1]) {
    return { start_date: customRange.value[0], end_date: customRange.value[1] }
  }
  return null
})

// ---------------------------------------------------------------- 图表
const domainChartRef = ref(null)
const refusalChartRef = ref(null)
const trendChartRef = ref(null)
let charts = { domain: null, refusal: null, trend: null }

function baseChartOption() {
  return {
    // bottom 预留两档：x 轴类目标签 + 底部图例（图例紧贴 bottom:5，二者不挤同一行）
    grid: { left: '3%', right: '4%', top: 40, bottom: 50, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: getCSSVariable('--gray-0'),
      borderColor: getCSSVariable('--gray-200'),
      borderWidth: 1,
      textStyle: { color: getCSSVariable('--gray-600'), fontSize: 12 }
    },
    legend: {
      // 趋势图图例多达业务线数，窄屏换行会顶进 x 轴标签区，滚动图例恒为一行
      type: 'scroll',
      bottom: 5,
      textStyle: { color: getCSSVariable('--gray-500'), fontSize: 12 },
      itemWidth: 14,
      itemHeight: 14
    },
    xAxis: {
      type: 'category',
      axisLine: { lineStyle: { color: getCSSVariable('--gray-200') } },
      axisTick: { show: false },
      axisLabel: { color: getCSSVariable('--gray-500'), fontSize: 12 }
    }
  }
}

function axisLabelStyle() {
  return {
    color: getCSSVariable('--gray-500'),
    fontSize: 12
  }
}

function splitLineStyle() {
  return { lineStyle: { color: getCSSVariable('--gray-100') } }
}

// 业务线类目仅个位数，强制显示全部标签，避免 interval 自动抽稀漏掉中间业务线（趋势图日期密集，不适用）
function domainXAxis(rows) {
  return {
    ...baseChartOption().xAxis,
    data: rows.map((row) => domainLabel(row.domain)),
    axisLabel: { ...axisLabelStyle(), interval: 0 }
  }
}

// 图 a：各业务线问答数（柱，左轴）+ 拒答率（折线，右轴）
function renderDomainChart() {
  const container = domainChartRef.value
  if (!container || !stats.value) return
  charts.domain?.dispose()
  charts.domain = echarts.init(container)

  const rows = domainRows.value
  charts.domain.setOption({
    ...baseChartOption(),
    xAxis: domainXAxis(rows),
    yAxis: [
      { type: 'value', name: t('opsOverview.yAxisCount'), nameTextStyle: axisLabelStyle(), axisLabel: axisLabelStyle(), axisLine: { show: false }, axisTick: { show: false }, splitLine: splitLineStyle() },
      { type: 'value', name: t('opsOverview.yAxisRate'), nameTextStyle: axisLabelStyle(), axisLabel: axisLabelStyle(), axisLine: { show: false }, axisTick: { show: false }, splitLine: { show: false }, max: 100 }
    ],
    series: [
      {
        name: t('opsOverview.yAxisCount'),
        type: 'bar',
        barMaxWidth: 40,
        data: rows.map((row) => row.total),
        itemStyle: { color: getColorByIndex(0), borderRadius: [4, 4, 0, 0] }
      },
      {
        name: t('opsOverview.yAxisRate'),
        type: 'line',
        yAxisIndex: 1,
        data: rows.map((row) => row.refusal_rate),
        symbolSize: 7,
        itemStyle: { color: getColorByIndex(3) },
        lineStyle: { color: getColorByIndex(3), width: 2 }
      }
    ]
  })
}

// 图 b：各业务线拒答类型堆叠柱（复用问答明细的回答类型词条）
function renderRefusalChart() {
  const container = refusalChartRef.value
  if (!container || !stats.value) return
  charts.refusal?.dispose()
  charts.refusal = echarts.init(container)

  const rows = domainRows.value
  charts.refusal.setOption({
    ...baseChartOption(),
    xAxis: domainXAxis(rows),
    yAxis: { type: 'value', axisLabel: axisLabelStyle(), axisLine: { show: false }, axisTick: { show: false }, splitLine: splitLineStyle() },
    series: REFUSAL_TYPES.map((type, index) => ({
      name: t(type.labelKey),
      type: 'bar',
      stack: 'total',
      barMaxWidth: 40,
      emphasis: { focus: 'series' },
      data: rows.map((row) => row.answer_types?.[type.key] || 0),
      itemStyle: { color: getColorByIndex(index + 1) }
    }))
  })
}

// 图 c：按日趋势堆叠柱（categories/data 信封与调用分析一致）
function renderTrendChart() {
  const container = trendChartRef.value
  if (!container || !trend.value?.data?.length) return
  charts.trend?.dispose()
  charts.trend = echarts.init(container)

  const points = trend.value.data
  const categories = trend.value.categories || []
  charts.trend.setOption({
    ...baseChartOption(),
    xAxis: {
      ...baseChartOption().xAxis,
      data: points.map((point) => point.date.slice(5))
    },
    yAxis: { type: 'value', axisLabel: axisLabelStyle(), axisLine: { show: false }, axisTick: { show: false }, splitLine: splitLineStyle() },
    series: categories.map((category, index) => ({
      name: domainLabel(category),
      type: 'bar',
      stack: 'total',
      emphasis: { focus: 'series' },
      data: points.map((point) => point.data[category] || 0),
      itemStyle: { color: getColorByIndex(index) }
    }))
  })
}

function renderAll() {
  renderDomainChart()
  renderRefusalChart()
  renderTrendChart()
}

const handleResize = () => {
  Object.values(charts).forEach((chart) => chart?.resize())
}
const resizeListenerOptions = { passive: true }

// ---------------------------------------------------------------- 加载
async function loadData() {
  loading.value = true
  try {
    const [statsResponse, trendResponse] = await Promise.all([
      dashboardApi.getQaStatsByDomain(activeRange.value || {}),
      dashboardApi.getQaStatsByDomainTrend(activeRange.value || {})
    ])
    stats.value = statsResponse
    trend.value = trendResponse
  } catch (error) {
    console.error('加载运营总览失败', error)
    message.error(error?.message || t('opsOverview.loadFailed'))
  } finally {
    loading.value = false
  }
  // a-card 的 loading 骨架会替换卡片内容，图表容器须在 loading 结束、容器恢复渲染后才能 init
  if (stats.value) {
    await nextTick()
    renderAll()
  }
}

onMounted(() => {
  loadData()
  window.addEventListener('resize', handleResize, resizeListenerOptions)
})

watch(
  () => themeStore.isDark,
  () => nextTick().then(renderAll)
)

// business_lines 由 AppLayout 挂载后 refreshConfig 异步到达；晚于首次渲染时重绘，x 轴才显示业务线名而非原始 code
watch(
  () => configStore.config.business_lines,
  () => nextTick().then(renderAll)
)

onUnmounted(() => {
  window.removeEventListener('resize', handleResize, resizeListenerOptions)
  Object.values(charts).forEach((chart) => chart?.dispose())
  charts = { domain: null, refusal: null, trend: null }
})
</script>

<style scoped lang="less">
.dashboard-overview-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  // 自持页面内边距（数据总览 Tab 壳只提供页头与 Tab 栏）；上方间距由 Tab 栏自带 margin 提供
  padding: 0 var(--page-padding) var(--page-padding);
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
.refresh-btn { margin-left: auto; }
.stat-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 16px;
}
.stat-card {
  padding: 16px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  .stat-label { font-size: 13px; color: var(--gray-600); margin-bottom: 8px; }
  .stat-value { font-size: 26px; font-weight: 600; color: var(--gray-1000); }
}
.chart-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 16px;
}
.chart-card {
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
  // 网格项默认 min-width:auto 会被 canvas 固定像素宽撑开，窄屏时卡片横向溢出
  min-width: 0;
}
.chart {
  width: 100%;
  height: 320px;
}
@media (max-width: 768px) {
  .chart-grid { grid-template-columns: 1fr; }
}
</style>
