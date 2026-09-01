<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'

import PageHeader from '@/components/shared/PageHeader.vue'
import AgentManagePanel from '@/components/model-management/AgentManagePanel.vue'
import ModelProviderManagePanel from '@/components/model-management/ModelProviderManagePanel.vue'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()

const activeTab = ref('agents')
const agentPanelRef = ref(null)
const providerPanelRef = ref(null)

const modelManageTabs = computed(() => {
  const tabs = [{ key: 'agents', label: t('modelMgmt.tabAgents') }]
  if (userStore.isAdmin) tabs.push({ key: 'providers', label: t('modelMgmt.tabProviders') })
  return tabs
})

const activePanel = computed(() =>
  activeTab.value === 'providers' ? providerPanelRef.value : agentPanelRef.value
)

const activeLoading = computed(() => activePanel.value?.loading || false)
const activeStats = computed(() => activePanel.value?.stats || {})

const normalizeTab = (tab) => {
  if (tab === 'providers' && userStore.isAdmin) return 'providers'
  return 'agents'
}

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
        <div v-else class="summary-strip">
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
      <div v-show="activeTab === 'agents'" class="tab-panel">
        <AgentManagePanel ref="agentPanelRef" />
      </div>
      <div v-if="userStore.isAdmin && activeTab === 'providers'" class="tab-panel">
        <ModelProviderManagePanel ref="providerPanelRef" />
      </div>
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
