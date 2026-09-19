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

    <!-- 待总结会话提醒：UDesk 拉取产生了新会话而总结未跑时，进入本页即告知 -->
    <a-modal
      v-model:open="pendingModalVisible"
      :title="t('knowledgeOps.pendingSummaryTitle')"
      :ok-text="t('knowledgeOps.pendingSummaryGo')"
      :cancel-text="t('knowledgeOps.pendingSummaryDismiss')"
      @ok="goCandidates"
    >
      <p class="pending-summary-body">
        {{ t('knowledgeOps.pendingSummaryBody', { count: pendingCount }) }}
      </p>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { RouterView, useRoute, useRouter } from 'vue-router'
import { dashboardApi } from '@/apis/dashboard_api'

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

// ---------------------------------------------------------------- 待总结提醒
// 每次进入知识运营查一次状态（视图不在 keep-alive 名单里，每次进菜单都会重新挂载）；
// 接口是 superadmin 专属，无权限或异常时静默跳过，不打扰普通用户。
const pendingModalVisible = ref(false)
const pendingCount = ref(0)

onMounted(async () => {
  try {
    const status = await dashboardApi.getUdeskStatus()
    const pending = status?.summarize?.pending ?? 0
    // 直达候选审核页时页面上已有同一信息，不再弹窗
    if (pending > 0 && activeTab.value !== 'candidates') {
      pendingCount.value = pending
      pendingModalVisible.value = true
    }
  } catch {
    /* 无权限或接口异常时不弹窗 */
  }
})

function goCandidates() {
  pendingModalVisible.value = false
  router.push('/knowledge-ops/candidates')
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
.pending-summary-body { margin: 0; color: var(--gray-700); }
</style>
