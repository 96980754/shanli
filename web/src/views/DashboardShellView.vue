<template>
  <div class="dashboard-tabs-page">
    <!-- Tab 与子路由一一对应：main = /dashboard 主看板本身，另两个是子路由 -->
    <a-tabs :active-key="activeTab" @change="switchTab">
      <a-tab-pane key="main" :tab="t('dash.tabMain')" />
      <a-tab-pane key="overview" :tab="t('opsOverview.pageTitle')" />
      <a-tab-pane key="qa-records" :tab="t('qaRecords.pageTitle')" />
    </a-tabs>

    <RouterView />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterView, useRoute, useRouter } from 'vue-router'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

// Tab key 与子路由路径后缀一致；主看板自身是 /dashboard（无后缀）
const TAB_PATHS = { main: '/dashboard', overview: '/dashboard/overview', 'qa-records': '/dashboard/qa-records' }
const activeTab = computed(
  () => Object.keys(TAB_PATHS).find((key) => route.path === TAB_PATHS[key]) || 'main'
)

function switchTab(key) {
  router.push(TAB_PATHS[key])
}
</script>

<style scoped lang="less">
.dashboard-tabs-page {
  min-height: 100%;
  background: var(--gray-25);
  // 主看板子页自管布局（StatusBar 全幅自持内边距），壳只提供 Tab 栏，不额外包页面级 padding
  :deep(.ant-tabs) {
    padding: 0 var(--page-padding);
  }
}
</style>
