<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { message, Modal } from 'ant-design-vue'
import { Plus, RefreshCw, Trash2, SquarePen, Bot, MessageCirclePlus } from 'lucide-vue-next'
import { useRouter } from 'vue-router'

import { agentApi } from '@/apis/agent_api'
import AgentEditModal from '@/components/model-management/AgentEditModal.vue'
import { isBuiltinAgent, useAgentStore } from '@/stores/agent'
import PageShoulder from '@/components/shared/PageShoulder.vue'
import InfoCard from '@/components/shared/InfoCard.vue'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import ExtensionCardGrid from '@/components/extensions/ExtensionCardGrid.vue'
import { generatePixelAvatar } from '@/utils/pixelAvatar'

const { t } = useI18n()
const agentStore = useAgentStore()
const router = useRouter()
const agentLoading = ref(false)
const searchQuery = ref('')

const agentBackendOptions = ref([])
const managedAgents = ref([])
const agentEditModalRef = ref(null)

const normalizeAgent = (agent) => {
  const agentId = agent?.agent_id || agent?.slug || agent?.id
  return agentId
    ? { ...agent, id: agentId, agent_id: agentId, slug: agent?.slug || agentId }
    : agent
}

const filteredAgents = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  const list = managedAgents.value || []
  const filtered = keyword
    ? list.filter(
        (agent) =>
          String(agent.name || '')
            .toLowerCase()
            .includes(keyword) ||
          String(agent.id || '')
            .toLowerCase()
            .includes(keyword) ||
          String(agent.backend_id || '')
            .toLowerCase()
            .includes(keyword)
      )
    : list
  return [...filtered].sort((a, b) => {
    if (isBuiltinAgent(a) !== isBuiltinAgent(b)) return isBuiltinAgent(a) ? -1 : 1
    return String(a.name || a.id).localeCompare(String(b.name || b.id), 'zh-CN')
  })
})

const groupedAgents = computed(() => {
  const agents = filteredAgents.value.filter((agent) => !agent.is_subagent)
  const subagents = filteredAgents.value.filter((agent) => agent.is_subagent)
  return [
    { key: 'agents', title: t('agentCfg.agents'), agents },
    { key: 'subagents', title: t('agentCfg.subagents'), agents: subagents }
  ].filter((group) => group.agents.length > 0)
})

const agentStats = computed(() => ({
  total: managedAgents.value.length,
  builtin: managedAgents.value.filter(isBuiltinAgent).length,
  manageable: managedAgents.value.filter((agent) => agent.can_manage).length,
  global: managedAgents.value.filter((agent) => agent.share_config?.access_level === 'global')
    .length
}))
const canManageAgent = (agent) => !!agent?.can_manage
const getAgentDefaultIconSrc = (agent) => (agent.id ? generatePixelAvatar(agent.id) : '')

// ============ Agent Operations ============
const loadAgentBackends = async () => {
  try {
    const response = await agentApi.getAgentBackends()
    agentBackendOptions.value = (response.backends || []).map((backend) => ({
      label: backend.name || backend.backend_id,
      value: backend.backend_id
    }))
  } catch (error) {
    message.error(error.message || t('agentCfg.loadBackendsFailed'))
  }
}

const loadAgents = async () => {
  agentLoading.value = true
  try {
    const response = await agentApi.getAgents({ includeSubagents: true })
    managedAgents.value = (response.agents || []).map(normalizeAgent)
  } catch (error) {
    message.error(error.message || t('agentCfg.loadAgentsFailed'))
  } finally {
    agentLoading.value = false
  }
}

const openCreateAgentModal = () => {
  agentEditModalRef.value?.openCreate()
}

const openEditAgentModal = (agent) => {
  if (!canManageAgent(agent)) return
  agentEditModalRef.value?.openEdit(agent)
}

const openAgentChat = (agent) => {
  if (!agent?.id || agent.is_subagent) return
  router.push({ name: 'AgentComp', query: { agent_id: agent.id } })
}

const refreshAgentLists = async () => {
  await Promise.all([loadAgents(), agentStore.fetchAgents()])
}

const deleteAgent = async (agent) => {
  if (isBuiltinAgent(agent)) {
    message.warning(t('agentCfg.builtinNotDeletable'))
    return
  }
  Modal.confirm({
    title: t('agentCfg.deleteAgentTitle', { name: agent.name }),
    content: t('agentCfg.deleteAgentContent'),
    okText: t('common.delete'),
    okType: 'danger',
    cancelText: t('common.cancel'),
    async onOk() {
      try {
        await agentApi.deleteAgent(agent.id)
        await refreshAgentLists()
        message.success(t('agentCfg.agentDeleted'))
      } catch (error) {
        message.error(error.message || t('agentCfg.deleteAgentFailed'))
      }
    }
  })
}

onMounted(async () => {
  await Promise.all([loadAgentBackends(), loadAgents()])
})

defineExpose({
  loading: agentLoading,
  stats: agentStats,
  refresh: loadAgents
})
</script>

<template>
  <div class="agent-manage-panel">
    <PageShoulder v-model:search="searchQuery" :search-placeholder="$t('agentCfg.searchAgentPlaceholder')">
      <template #actions>
        <a-button type="primary" class="lucide-icon-btn" @click="openCreateAgentModal">
          <Plus :size="14" />
          {{ $t('agentCfg.addAgent') }}
        </a-button>
        <a-button class="lucide-icon-btn" @click="loadAgents" :loading="agentLoading">
          <RefreshCw :size="14" :class="{ spinning: agentLoading }" />
        </a-button>
      </template>
    </PageShoulder>

    <div v-if="groupedAgents.length === 0" class="agent-empty-state">
      <a-empty
        :image="false"
        :description="searchQuery ? $t('agentCfg.noMatchingAgents') : $t('agentCfg.noAgents')"
      />
    </div>

    <template v-else>
      <section v-for="group in groupedAgents" :key="group.key" class="agent-group-section">
        <div class="agent-group-header">
          <span>{{ group.title }}</span>
        </div>
        <ExtensionCardGrid :min-width="320">
          <InfoCard
            v-for="agent in group.agents"
            :key="agent.id"
            :title="agent.name"
            :subtitle="agent.slug || agent.id"
            :description="agent.description || $t('common.noDescription')"
            :default-icon="Bot"
            :tags="[]"
            class="config-card agent-card"
            @click="canManageAgent(agent) && openEditAgentModal(agent)"
          >
            <template #icon>
              <FallbackAvatar
                class="agent-card-icon-image"
                :src="agent.icon"
                :default-src="getAgentDefaultIconSrc(agent)"
                :name="agent.name || agent.id"
                :seed="agent.id || agent.name"
                kind="agent"
                :size="40"
                shape="rounded"
                :alt="$t('agentCfg.agentIconAlt', { name: agent.name || $t('agentCfg.agent') })"
              />
            </template>

            <template v-if="canManageAgent(agent)" #card-more-action-corner>
              <a-menu>
                <a-menu-item key="edit" @click.stop="openEditAgentModal(agent)">
                  <span class="lucide-menu-item">
                    <SquarePen :size="14" />
                    <span>{{ $t('agentCfg.editAgent') }}</span>
                  </span>
                </a-menu-item>
                <a-menu-item
                  key="delete"
                  :disabled="isBuiltinAgent(agent)"
                  :danger="!isBuiltinAgent(agent)"
                  @click.stop="deleteAgent(agent)"
                >
                  <span class="lucide-menu-item">
                    <Trash2 :size="14" />
                    <span>{{ $t('agentCfg.deleteAgent') }}</span>
                  </span>
                </a-menu-item>
              </a-menu>
            </template>

            <template v-if="group.key === 'agents'" #tags>
              <div class="agent-card-actions">
                <a-button
                  type="primary"
                  size="small"
                  class="lucide-icon-btn agent-chat-entry"
                  @click.stop="openAgentChat(agent)"
                >
                  <MessageCirclePlus :size="14" />
                  {{ $t('agentCfg.goChat') }}
                </a-button>
              </div>
            </template>
          </InfoCard>
        </ExtensionCardGrid>
      </section>
    </template>

    <AgentEditModal
      ref="agentEditModalRef"
      :backend-options="agentBackendOptions"
      @saved="refreshAgentLists"
    />
  </div>
</template>

<style lang="less" scoped>
.agent-manage-panel {
  height: 100%;
  min-height: 0;
}

.agent-empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 100px 20px;
  text-align: center;
}

.agent-group-section + .agent-group-section {
  padding-top: 2px;
}

.agent-group-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px var(--page-padding) 0;
  color: var(--gray-500);
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.4px;
  line-height: 18px;
}

.agent-card-icon-image {
  display: block;
  width: 100%;
  height: 100%;
  border: 0;
}

.agent-card :deep(.info-card-tags) {
  justify-content: flex-start;
  margin-top: auto;
}

.agent-card-actions {
  display: flex;
  justify-content: flex-start;
  width: 100%;
  margin-top: auto;
}

.agent-chat-entry {
  min-width: 78px;
  border: 0;
  box-shadow: none;
  font-size: 12px;

  &:hover,
  &:focus {
    border: 0;
    box-shadow: none;
  }
}

.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
