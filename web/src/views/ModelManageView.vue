<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import PageHeader from '@/components/shared/PageHeader.vue'
import AgentManagePanel from '@/components/model-management/AgentManagePanel.vue'
import ModelProviderManagePanel from '@/components/model-management/ModelProviderManagePanel.vue'
import SkillCardList from '@/components/extensions/SkillCardList.vue'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()

// 「智能体」页签暂隐藏——与 AppLayout.vue 的「全库搜索」同一做法：把这里改为 true 即恢复。
// 页签、面板、统计条、切换逻辑都原样留着，由这一个开关决定它们出不出现。
const SHOW_AGENT_TAB = false

const activeTab = ref('agents')
const agentPanelRef = ref(null)
const providerPanelRef = ref(null)
const skillsPanelRef = ref(null)

// Skills 原在知识库页签里，现归到本页（技能是智能体的能力配置，与知识库并列不合适）
const modelManageTabs = computed(() => {
  const tabs = SHOW_AGENT_TAB ? [{ key: 'agents', label: t('modelMgmt.tabAgents') }] : []
  if (userStore.isAdmin) {
    tabs.push({ key: 'providers', label: t('modelMgmt.tabProviders') })
    tabs.push({ key: 'skills', label: t('modelMgmt.tabSkills') })
  }
  return tabs
})

const activePanel = computed(() => {
  if (activeTab.value === 'skills') return skillsPanelRef.value
  return activeTab.value === 'providers' ? providerPanelRef.value : agentPanelRef.value
})

const activeLoading = computed(() => activePanel.value?.loading || false)
const activeStats = computed(() => activePanel.value?.stats || {})

const normalizeTab = (tab) => {
  // 两个管理页签都只对管理员开放，非管理员的任何请求都退回默认页签
  if (userStore.isAdmin && (tab === 'providers' || tab === 'skills')) return tab
  // 页签隐藏时，任何指向「智能体」的请求（含 activeTab 的默认值）都落到模型供应商
  if (!SHOW_AGENT_TAB) return 'providers'
  return 'agents'
}

// 详情页由子路由接管整页，隐藏列表页头
const isDetailPage = computed(() => route.path.startsWith('/model-manage/skill/'))

watch(
  () => [route.query.tab, userStore.isAdmin],
  ([tab]) => {
    const nextTab = normalizeTab(tab)
    if (activeTab.value !== nextTab) activeTab.value = nextTab
  },
  { immediate: true }
)

watch(activeTab, (tab) => {
  const nextTab = normalizeTab(tab)
  if (nextTab !== tab) {
    activeTab.value = nextTab
    return
  }
  if (route.query.tab === nextTab) return
  router.replace({ query: { ...route.query, tab: nextTab } })
})
</script>

<template>
  <div class="model-manage-view">
    <PageHeader
      v-if="!isDetailPage"
      v-model:active-key="activeTab"
      :title="t('nav.agentManage')"
      :tabs="modelManageTabs"
      :loading="activeLoading"
      :show-border="true"
      :aria-label="t('modelMgmt.viewSwitchAriaLabel')"
    >
      <template #info>
        <div v-if="activeTab === 'agents'" class="summary-strip">
          <span>{{ t('modelMgmt.agentsCount', { count: activeStats.total || 0 }) }}</span>
          <span>{{ t('modelMgmt.globalCount', { count: activeStats.global || 0 }) }}</span>
          <span v-if="activeStats.builtin">
            {{ t('modelMgmt.builtinCount', { count: activeStats.builtin }) }}
          </span>
          <span>{{ t('modelMgmt.manageableCount', { count: activeStats.manageable || 0 }) }}</span>
        </div>
        <div v-else-if="activeTab === 'providers'" class="summary-strip">
          <span>{{ t('modelMgmt.providersCount', { count: activeStats.total || 0 }) }}</span>
          <span>{{ t('modelMgmt.enabledCountLabel', { count: activeStats.enabled || 0 }) }}</span>
          <span v-if="activeStats.warning > 0" class="warning-count">
            {{ t('modelMgmt.credentialMissingCount', { count: activeStats.warning }) }}
          </span>
          <span>{{ t('modelMgmt.modelsCount', { count: activeStats.models || 0 }) }}</span>
        </div>
      </template>
    </PageHeader>

    <div class="model-manage-content">
      <template v-if="!isDetailPage">
        <template v-if="SHOW_AGENT_TAB">
          <div v-show="activeTab === 'agents'" class="tab-panel">
            <AgentManagePanel ref="agentPanelRef" />
          </div>
        </template>
        <div v-if="userStore.isAdmin && activeTab === 'providers'" class="tab-panel">
          <ModelProviderManagePanel ref="providerPanelRef" />
        </div>
        <div v-if="userStore.isAdmin && activeTab === 'skills'" class="tab-panel">
          <SkillCardList ref="skillsPanelRef" />
        </div>
      </template>

      <router-view v-else />
    </div>
  </div>
</template>

<style lang="less" scoped>
.model-manage-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--gray-0);
  color: var(--gray-1000);
}

.model-manage-content {
  flex: 1;
  min-height: 0;
  overflow: hidden;

  .tab-panel {
    height: 100%;
    min-height: 0;
    overflow-y: auto;
  }
}

.summary-strip {
  display: flex;
  gap: 8px;

  span {
    padding: 6px 10px;
    border: 1px solid var(--gray-100);
    border-radius: 7px;
    background: var(--gray-10);
    color: var(--gray-700);
    font-size: 12px;
    line-height: 18px;
  }

  .warning-count {
    background: var(--color-warning-50);
    border-color: var(--color-warning-100);
    color: var(--color-warning-700);
  }
}
</style>
