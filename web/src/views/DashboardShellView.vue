<template>
  <div class="dashboard-tabs-page">
    <!-- Tab 与子路由一一对应：main = /dashboard 主看板本身；问答明细是独立全页（/dashboard/qa-records），不在此列 -->
    <a-tabs :active-key="activeTab" @change="switchTab">
      <a-tab-pane key="main" :tab="t('dash.tabMain')" />
      <a-tab-pane key="overview" :tab="t('opsOverview.pageTitle')" />
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

const activeTab = computed(() => (route.path.endsWith('/overview') ? 'overview' : 'main'))

function switchTab(key) {
  router.push(key === 'overview' ? '/dashboard/overview' : '/dashboard')
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
