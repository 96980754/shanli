<template>
  <div class="extensions-view extension-page-root">
    <PageHeader
      v-if="!isDetailPage"
      v-model:active-key="activeTab"
      :title="userStore.isAdmin ? $t('nav.extensions') : $t('nav.knowledgeBase')"
      :tabs="extensionTabs"
      :loading="activeChildLoading"
      :show-border="true"
      :aria-label="$t('tools.viewSwitchAria')"
    />

    <div v-if="!isDetailPage" class="extensions-content">
      <div v-if="activeTab === 'knowledge'" class="tab-panel">
        <DataBaseView ref="knowledgeRef" embedded />
      </div>
      <div v-if="activeTab === 'skills'" class="tab-panel">
        <SkillCardList ref="skillsRef" />
      </div>
    </div>

    <router-view v-else />
  </div>
</template>

<script setup>
import { ref, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import SkillCardList from '@/components/extensions/SkillCardList.vue'
import PageHeader from '@/components/shared/PageHeader.vue'
import DataBaseView from '@/views/DataBaseView.vue'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()
const { t } = useI18n()
const activeTab = ref(null)
const knowledgeRef = ref(null)
const skillsRef = ref(null)

const extensionTabs = computed(() =>
  userStore.isAdmin
    ? [
        { key: 'knowledge', label: t('nav.knowledgeBase') },
        { key: 'skills', label: t('tools.skillsTab') }
      ]
    : [{ key: 'knowledge', label: t('nav.knowledgeBase') }]
)
const allowedTabKeys = computed(() => extensionTabs.value.map((tab) => tab.key))
const defaultTabKey = computed(() => extensionTabs.value[0]?.key || 'skills')

const normalizeTab = (tab) => {
  if (allowedTabKeys.value.includes(tab)) return tab
  return defaultTabKey.value
}

const replaceTabQuery = (tab) => {
  const query = { ...route.query }
  if (tab === defaultTabKey.value) {
    delete query.tab
  } else {
    query.tab = tab
  }
  router.replace({ query })
}

const isDetailPage = computed(() => {
  return (
    route.path.startsWith('/extensions/knowledgebase/') ||
    route.path.startsWith('/extensions/skill/')
  )
})

const activeChildLoading = computed(() => {
  const refMap = {
    knowledge: knowledgeRef,
    skills: skillsRef
  }
  const child = refMap[activeTab.value]
  return child?.value?.loading || false
})

watch(
  () => [route.query.tab, userStore.isAdmin],
  ([tab]) => {
    const nextTab = normalizeTab(tab)
    if (activeTab.value !== nextTab) activeTab.value = nextTab
    if (tab && tab !== nextTab) replaceTabQuery(nextTab)
  },
  { immediate: true }
)

watch(activeTab, (tab) => {
  if (!tab) return
  const nextTab = normalizeTab(tab)
  if (nextTab !== tab) {
    activeTab.value = nextTab
    return
  }
  if (route.query.tab === nextTab || (!route.query.tab && nextTab === defaultTabKey.value)) return
  replaceTabQuery(nextTab)
})
</script>

<style scoped lang="less">
@import '@/assets/css/extensions.less';

.extensions-view {
  .extensions-content {
    flex: 1;
    min-height: 0;
    overflow: hidden;

    .tab-panel {
      height: 100%;
      min-height: 0;
      overflow-y: auto;
    }
  }
}
</style>
