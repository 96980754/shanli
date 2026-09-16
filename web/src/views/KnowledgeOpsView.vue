<template>
  <div class="knowledge-ops-page">
    <div class="page-header">
      <div>
        <h1>{{ $t('knowledgeOps.pageTitle') }}</h1>
        <p>{{ subtitle }}</p>
      </div>
    </div>

    <a-tabs :active-key="activeTab" @change="switchTab">
      <a-tab-pane key="gaps" :tab="t('gaps.pageTitle')" />
      <a-tab-pane key="feedback" :tab="t('feedback.pageTitle')" />
      <a-tab-pane key="qa-pairs" :tab="t('qaPairs.pageTitle')" />
      <a-tab-pane key="candidates" :tab="t('candidates.pageTitle')" />
      <a-tab-pane key="conversations" :tab="t('udeskConversations.pageTitle')" />
    </a-tabs>

    <RouterView v-slot="{ Component }">
      <KeepAlive>
        <component :is="Component" />
      </KeepAlive>
    </RouterView>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterView, useRoute, useRouter } from 'vue-router'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

// Tab 与子路由一一对应：切换即跳转，可分享/收藏各 Tab 地址
const TAB_SUBTITLE_KEYS = {
  gaps: 'gaps.pageSubtitle',
  feedback: 'feedback.pageSubtitle',
  'qa-pairs': 'qaPairs.pageSubtitle',
  candidates: 'candidates.pageSubtitle',
  conversations: 'udeskConversations.pageSubtitle'
}
const activeTab = computed(() =>
  Object.keys(TAB_SUBTITLE_KEYS).find((key) => route.path.endsWith(`/${key}`)) || 'gaps'
)
const subtitle = computed(() => t(TAB_SUBTITLE_KEYS[activeTab.value]))

function switchTab(key) {
  router.push(`/knowledge-ops/${key}`)
}
</script>

<style scoped lang="less">
.knowledge-ops-page {
  min-height: 100vh;
  padding: var(--page-padding);
  background: var(--gray-25);
}
.page-header {
  margin-bottom: 12px;
  h1 { margin: 0 0 6px; font-size: 24px; color: var(--gray-1000); }
  p { margin: 0; color: var(--gray-600); }
}
</style>
