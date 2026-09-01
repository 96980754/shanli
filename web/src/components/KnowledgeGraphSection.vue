<template>
  <div class="graph-section" v-if="isGraphSupported">
    <div class="graph-container-compact">
      <div v-if="!isGraphSupported" class="graph-disabled">
        <div class="disabled-content">
          <h4>{{ $t('graph.unavailableTitle') }}</h4>
          <p>{{ $t('graph.unsupportedByType', { type: kbTypeLabel }) }}</p>
          <p>{{ $t('graph.milvusOnly') }}</p>
        </div>
      </div>
      <div v-else class="graph-wrapper">
        <GraphCanvas
          ref="graphRef"
          :graph-data="graph.graphData"
          @node-click="graph.handleNodeClick"
          @edge-click="graph.handleEdgeClick"
          @canvas-click="graph.handleCanvasClick"
        >
          <template #top>
            <div class="compact-actions">
              <div class="actions-left">
                <a-input
                  v-model:value="searchInput"
                  :placeholder="$t('graph.searchEntity')"
                  style="width: 240px"
                  @keydown.enter="onSearch"
                  allow-clear
                >
                  <template #suffix>
                    <component
                      :is="graph.fetching ? Loader2 : Search"
                      :size="14"
                      class="search-suffix-icon"
                      @click="onSearch"
                    />
                  </template>
                </a-input>
                <a-button class="action-btn" @click="loadGraph" :title="$t('common.refresh')">
                  <RefreshCw :size="16" :class="{ spin: graph.fetching }" />
                </a-button>
              </div>
              <div class="actions-right">
                <a-button
                  v-if="isMilvus"
                  class="action-btn index-action-btn"
                  :class="{ 'has-index-label': hasPendingGraphChunks }"
                  @click="toggleBuildPanel"
                  :title="graphIndexButtonTitle"
                  :aria-label="graphIndexButtonTitle"
                >
                  <Database :size="16" />
                  <span v-if="hasPendingGraphChunks" class="index-status-label"
                    >{{ $t('graph.pendingIndex', { count: pendingGraphChunks }) }}</span
                  >
                  <span
                    v-if="graphIndexDotStatus"
                    class="status-dot"
                    :class="`status-dot--${graphIndexDotStatus}`"
                  ></span>
                </a-button>
                <a-button class="action-btn" @click="toggleSettingsPanel" :title="$t('graph.settings')">
                  <Settings :size="16" />
                </a-button>
              </div>
            </div>
          </template>
        </GraphCanvas>
        <ResourceEmptyState
          v-if="showGraphConfigEmpty"
          class="graph-empty-state"
          :title="$t('graph.emptyTitle')"
          :description="$t('graph.emptyDescription')"
          :icon="Network"
          full-height
        >
          <template #actions>
            <a-button type="primary" class="lucide-icon-btn" @click="openGraphConfig">
              <Settings :size="16" />
              {{ $t('graph.configureExtractor') }}
            </a-button>
          </template>
        </ResourceEmptyState>
        <ResourceEmptyState
          v-else-if="showGraphDataEmpty"
          class="graph-empty-state"
          :title="graphDataEmptyTitle"
          :description="graphDataEmptyDescription"
          :icon="Network"
          full-height
        >
          <template #actions>
            <a-button v-if="searchInput.trim()" class="lucide-icon-btn" @click="clearGraphSearch">
              <Search :size="16" />
              {{ $t('graph.clearSearch') }}
            </a-button>
            <a-button
              v-else-if="hasPendingGraphChunks && !isBuildActive"
              type="primary"
              class="lucide-icon-btn"
              @click="startGraphBuild"
            >
              <Database :size="16" />
              {{ $t('graph.startIndex') }}
            </a-button>
            <a-button v-else class="lucide-icon-btn" @click="loadGraph">
              <RefreshCw :size="16" :class="{ spin: graph.fetching }" />
              {{ $t('graph.refreshGraph') }}
            </a-button>
          </template>
        </ResourceEmptyState>

        <!-- 详情浮动卡片 -->
        <GraphDetailPanel
          :visible="graph.showDetailDrawer"
          :item="graph.selectedItem"
          :type="graph.selectedItemType"
          @close="graph.handleCanvasClick"
        />

        <!-- 设置浮动面板 -->
        <transition name="slide-fade">
          <div v-if="showSettings" class="floating-panel settings-panel">
            <div class="panel-header">
              <span class="panel-title">{{ $t('graph.settingsPanelTitle') }}</span>
            </div>
            <div class="panel-body">
              <a-form layout="vertical">
                <a-form-item :label="$t('graph.maxNodesLabel')">
                  <a-input-number
                    v-model:value="subgraphParams.maxNodes"
                    :min="10"
                    :max="1000"
                    :step="10"
                    style="width: 100%"
                  />
                </a-form-item>
                <a-form-item :label="$t('graph.maxDepthLabel')">
                  <a-input-number
                    v-model:value="subgraphParams.maxDepth"
                    :min="1"
                    :max="5"
                    :step="1"
                    style="width: 100%"
                  />
                </a-form-item>
                <a-form-item :label="$t('graph.excludeChunk')">
                  <a-switch v-model:checked="subgraphParams.excludeChunk" />
                </a-form-item>
                <a-form-item>
                  <a-button type="primary" @click="applySettings" style="width: 100%">
                    {{ $t('graph.apply') }}
                  </a-button>
                </a-form-item>
              </a-form>
            </div>
          </div>
        </transition>

        <!-- 索引管理浮动面板 -->
        <transition name="slide-fade">
          <div v-if="isMilvus && showBuildPanel" class="floating-panel build-panel">
            <div class="panel-header">
              <span class="panel-title">{{ $t('graph.indexManagement') }}</span>
              <a-button
                size="small"
                type="text"
                :disabled="graphBuildLoading"
                @click="loadGraphBuildStatus"
                class="panel-refresh-btn"
              >
                <RefreshCw :size="14" :class="{ spin: graphBuildLoading }" />
              </a-button>
            </div>
            <div class="panel-body">
              <div class="status-row">
                <span class="status-label">{{ $t('common.status') }}</span>
                <a-tag v-if="isBuildActive" color="blue" size="small">{{ $t('graph.buildActive') }}</a-tag>
                <a-tag v-else-if="isBuildFailed" color="red" size="small">{{ $t('graph.buildFailed') }}</a-tag>
                <a-tag v-else-if="isBuildCancelled" size="small">{{ $t('graph.buildCancelled') }}</a-tag>
                <a-tag v-else-if="graphBuildStatus?.published" color="green" size="small">{{ $t('graph.published') }}</a-tag>
                <a-tag v-else-if="graphBuildStatus?.configured && graphBuildStatus?.locked" color="orange" size="small">
                  {{ $t('graph.configuredNotCreated') }}
                </a-tag>
                <a-tag v-else-if="graphBuildStatus?.configured" color="orange" size="small">{{ $t('graph.configuredPending') }}</a-tag>
                <a-tag v-else color="orange" size="small">{{ $t('graph.notConfigured') }}</a-tag>
              </div>
              <a-progress
                v-if="isBuildActive"
                :percent="graphBuildStatus?.build_task_progress ?? 0"
                :stroke-color="{ '0%': '#108ee9', '100%': '#87d068' }"
                size="small"
                style="margin-bottom: 10px"
              />
              <a-alert
                v-if="buildTaskFailureMessage"
                :type="isBuildFailed ? 'error' : 'warning'"
                show-icon
                class="build-task-alert"
                :message="buildTaskFailureMessage"
              />
              <div v-if="buildTaskResult" class="build-result-summary">
                <span>{{ $t('graph.buildResultSuccess', { count: buildTaskResult.success ?? 0 }) }}</span>
                <span>{{ $t('graph.buildResultFailed', { count: buildTaskResult.failed ?? 0 }) }}</span>
                <span>{{ $t('graph.buildResultRemaining', { count: buildTaskResult.remaining ?? 0 }) }}</span>
              </div>
              <div class="stats-grid">
                <div class="stat-item">
                  <span class="stat-value">{{ graphBuildStatus?.total_chunks ?? '-' }}</span>
                  <span class="stat-label">{{ $t('graph.totalChunks') }}</span>
                </div>
                <div class="stat-item">
                  <span class="stat-value">{{ graphBuildStatus?.pending_chunks ?? '-' }}</span>
                  <span class="stat-label">{{ $t('graph.pendingBuild') }}</span>
                </div>
                <div class="stat-item">
                  <span class="stat-value">{{ graphBuildStatus?.indexed_chunks ?? '-' }}</span>
                  <span class="stat-label">{{ $t('graph.indexed') }}</span>
                </div>
                <div class="stat-item">
                  <span class="stat-value">{{ graphBuildStatus?.entity_count ?? '-' }}</span>
                  <span class="stat-label">{{ $t('graph.entity') }}</span>
                </div>
                <div class="stat-item">
                  <span class="stat-value">{{ graphBuildStatus?.relationship_count ?? '-' }}</span>
                  <span class="stat-label">{{ $t('graph.relationship') }}</span>
                </div>
              </div>
              <div class="build-actions">
                <a-button
                  v-if="!graphBuildStatus?.configured"
                  type="primary"
                  block
                  @click="openGraphConfig"
                >
                  {{ $t('graph.configureExtractor') }}
                </a-button>
                <a-button v-else-if="isBuildActive" type="primary" block disabled>
                  {{ $t('graph.buildProgress', { percent: graphBuildStatus?.build_task_progress ?? 0 }) }}
                </a-button>
                <a-button
                  v-else-if="canRetryBuild"
                  type="primary"
                  block
                  :disabled="!graphBuildStatus?.pending_chunks"
                  @click="startGraphBuild"
                >
                  {{ $t('graph.retryIndex') }}
                </a-button>
                <a-button
                  v-else
                  type="primary"
                  block
                  :disabled="!graphBuildStatus?.pending_chunks"
                  @click="startGraphBuild"
                >
                  {{ $t('graph.startIndex') }}
                </a-button>
                <div class="actions-secondary">
                  <a-button
                    v-if="graphBuildStatus?.configured && !isBuildActive"
                    size="small"
                    type="text"
                    @click="openGraphConfig"
                  >
                    {{ $t('graph.modifyConfig') }}
                  </a-button>
                  <a-button
                    size="small"
                    type="text"
                    danger
                    v-if="graphBuildStatus?.configured && !isBuildActive"
                    @click="confirmResetGraph"
                    >{{ $t('graph.reset') }}</a-button
                  >
                </div>
              </div>
            </div>
          </div>
        </transition>
      </div>
    </div>

    <a-modal
      v-model:open="showGraphConfig"
      :title="graphConfigTitle"
      width="640px"
      @ok="configureGraphBuild"
    >
      <a-form layout="vertical">
        <a-alert
          v-if="isEditingGraphConfig"
          class="config-warning"
          type="warning"
          show-icon
          :message="$t('graph.configChangeWarning')"
        />
        <a-form-item :label="$t('graph.extractorType')">
          <div class="extractor-type-cards" role="radiogroup" :aria-label="$t('graph.extractorType')">
            <div
              v-for="option in extractorTypeOptions"
              :key="option.value"
              class="extractor-type-card"
              :class="{
                active: graphConfigForm.extractor_type === option.value,
                disabled: isEditingGraphConfig || option.disabled
              }"
              role="radio"
              :aria-checked="graphConfigForm.extractor_type === option.value"
              :aria-disabled="isEditingGraphConfig || option.disabled"
              :tabindex="isEditingGraphConfig || option.disabled ? -1 : 0"
              @click="selectExtractorType(option)"
              @keydown.enter.prevent="selectExtractorType(option)"
              @keydown.space.prevent="selectExtractorType(option)"
            >
              <div class="card-header">
                <component :is="option.icon" class="type-icon" />
                <span class="type-title">{{ t(option.label) }}</span>
              </div>
              <div class="card-description">{{ t(option.description) }}</div>
              <div v-if="option.helper" class="card-helper" :class="{ warning: option.disabled }">
                {{ t(option.helper) }}
              </div>
            </div>
          </div>
        </a-form-item>
        <a-form-item :label="$t('graph.modelLabel')">
          <ModelSelectorComponent
            :model_spec="graphConfigForm.model_spec"
            :placeholder="t('graph.selectExtractorModel')"
            @select-model="(spec) => (graphConfigForm.model_spec = spec)"
          />
        </a-form-item>
        <a-form-item v-if="!isLegacyGraphConfig" label="Core Ontology">
          <a-select
            v-model:value="graphConfigForm.ontology_key"
            :loading="ontologyRegistryLoading"
            :options="ontologyRegistryOptions"
            :placeholder="$t('graph.selectCoreOntology')"
            show-search
            option-filter-prop="label"
            @change="selectOntologyRegistry"
          />
          <div class="ontology-help">
            {{ $t('graph.ontologyHelp') }}
          </div>
        </a-form-item>
        <a-form-item :label="isLegacyGraphConfig ? t('graph.legacyFreeTextSchema') : t('graph.domainOntologyExt')">
          <a-textarea
            v-if="isLegacyGraphConfig"
            v-model:value="graphConfigForm.schema"
            :rows="6"
            :placeholder="$t('graph.legacySchemaPlaceholder')"
          />
          <a-textarea
            v-else
            v-model:value="graphConfigForm.domain_schema"
            :rows="10"
            :placeholder="$t('graph.domainSchemaPlaceholder')"
          />
        </a-form-item>
        <div class="form-grid two-columns">
          <a-form-item :label="$t('graph.concurrencyLabel')">
            <a-input-number
              v-model:value="graphConfigForm.concurrency_count"
              :min="1"
              :max="MAX_GRAPH_CONCURRENCY"
              :step="1"
              style="width: 100%"
            />
          </a-form-item>
          <a-form-item :label="$t('graph.modelParamsJson')">
            <a-input
              v-model:value="graphConfigForm.model_params_text"
              :placeholder="$t('graph.modelParamsExample')"
            />
          </a-form-item>
        </div>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onUnmounted, reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDatabaseStore } from '@/stores/database'
import { useTaskerStore } from '@/stores/tasker'
import { useConfigStore } from '@/stores/config'
import {
  RefreshCw,
  Settings,
  Search,
  Loader2,
  Database,
  Network,
  BrainCircuit,
  ScanText
} from 'lucide-vue-next'
import GraphCanvas from '@/components/GraphCanvas.vue'
import GraphDetailPanel from '@/components/GraphDetailPanel.vue'
import ResourceEmptyState from '@/components/shared/ResourceEmptyState.vue'
import { getKbTypeLabel } from '@/utils/kb_utils'
import { unifiedApi } from '@/apis/graph_api'
import { graphBuildApi } from '@/apis/knowledge_api'
import { ontologyRegistryApi } from '@/apis/ontology_api'
import { Modal, message } from 'ant-design-vue'
import ModelSelectorComponent from '@/components/ModelSelectorComponent.vue'
import { useGraph } from '@/composables/useGraph'

const GRAPH_BUILD_TASK_TYPE = 'knowledge_graph_index'
const MILVUS_KB_TYPE = 'milvus'
const GRAPH_SUPPORTED_KB_TYPES = new Set([MILVUS_KB_TYPE])

const props = defineProps({
  active: {
    type: Boolean,
    default: false
  }
})

const { t } = useI18n()

const store = useDatabaseStore()
const taskerStore = useTaskerStore()
const configStore = useConfigStore()

const kbId = computed(() => store.kbId)
const kbType = computed(() => store.database.kb_type)
const kbTypeLabel = computed(() => getKbTypeLabel(kbType.value || 'milvus'))
const isMilvus = computed(() => kbType.value?.toLowerCase() === MILVUS_KB_TYPE)

const graphRef = ref(null)
const showSettings = ref(false)
const showBuildPanel = ref(false)
const subgraphParams = reactive({
  maxNodes: 100,
  maxDepth: 2,
  excludeChunk: true
})
const searchInput = ref('')
const graphBuildStatus = ref(null)
const graphBuildLoading = ref(false)
const ontologyRegistries = ref([])
const ontologyRegistryLoading = ref(false)
const showGraphConfig = ref(false)
let buildStatusPollTimer = null

const extractorTypeOptions = [
  {
    value: 'llm',
    label: 'LLM',
    description: 'graph.llmDescription',
    helper: 'graph.llmHelper',
    icon: BrainCircuit,
    disabled: false
  },
  {
    value: 'more',
    label: 'graph.moreLabel',
    description: 'graph.moreDescription',
    helper: 'graph.moreHelper',
    icon: ScanText,
    disabled: true
  }
]

const isBuildActive = computed(() => {
  const s = graphBuildStatus.value?.build_task_status
  return s === 'pending' || s === 'running'
})

const isBuildFailed = computed(() => {
  return graphBuildStatus.value?.build_task_status === 'failed'
})

const isBuildCancelled = computed(() => {
  return graphBuildStatus.value?.build_task_status === 'cancelled'
})

const buildTaskResult = computed(() => graphBuildStatus.value?.build_task_result || null)

const buildTaskFailureMessage = computed(() => {
  if (!isBuildFailed.value && !isBuildCancelled.value) return ''
  return (
    graphBuildStatus.value?.build_task_error ||
    graphBuildStatus.value?.build_task_message ||
    (isBuildCancelled.value ? t('graph.buildTaskCancelled') : t('graph.buildTaskFailed'))
  )
})

const pendingGraphChunks = computed(() => {
  return Number(graphBuildStatus.value?.pending_chunks ?? 0)
})

const canRetryBuild = computed(() => {
  return (isBuildFailed.value || isBuildCancelled.value) && pendingGraphChunks.value > 0
})

const hasPendingGraphChunks = computed(() => pendingGraphChunks.value > 0)

const isGraphIndexComplete = computed(() => {
  return (
    Boolean(graphBuildStatus.value?.locked) &&
    Boolean(graphBuildStatus.value?.published) &&
    !isBuildActive.value &&
    pendingGraphChunks.value === 0
  )
})

const graphIndexDotStatus = computed(() => {
  if (isBuildActive.value) return 'active'
  if (hasPendingGraphChunks.value) return 'pending'
  if (isGraphIndexComplete.value) return 'complete'
  return ''
})

const graphIndexButtonTitle = computed(() => {
  if (isBuildActive.value) return t('graph.indexButtonActive')
  if (hasPendingGraphChunks.value) return t('graph.indexButtonPending', { count: pendingGraphChunks.value })
  if (isGraphIndexComplete.value) return t('graph.indexButtonComplete')
  if (isGraphConfigured.value && !graphBuildStatus.value?.locked) return t('graph.indexButtonConfigUnconfirmed')
  if (graphBuildStatus.value?.locked && !graphBuildStatus.value?.published) return t('graph.indexButtonConfiguredNotCreated')
  return t('graph.indexButton')
})

const toggleBuildPanel = () => {
  showBuildPanel.value = !showBuildPanel.value
  showSettings.value = false
}

const toggleSettingsPanel = () => {
  showSettings.value = !showSettings.value
  showBuildPanel.value = false
}

const isGraphConfigured = computed(() => Boolean(graphBuildStatus.value?.configured))
const isEditingGraphConfig = computed(() => isGraphConfigured.value)
const isLegacyGraphConfig = computed(() => {
  if (!isEditingGraphConfig.value) return false
  return !graphBuildStatus.value?.config?.extractor_options?.ontology_registry_id
})

const graphConfigTitle = computed(() =>
  isEditingGraphConfig.value ? t('graph.editGraphConfigTitle') : t('graph.configGraphExtractorTitle')
)

const stopBuildStatusPoll = () => {
  if (buildStatusPollTimer) {
    clearInterval(buildStatusPollTimer)
    buildStatusPollTimer = null
  }
}

const startBuildStatusPoll = () => {
  stopBuildStatusPoll()
  buildStatusPollTimer = setInterval(() => {
    loadGraphBuildStatus()
  }, 5000)
}

watch(
  isBuildActive,
  (active) => {
    if (active) {
      startBuildStatusPoll()
    } else {
      stopBuildStatusPoll()
    }
  },
  { immediate: true }
)
const DEFAULT_GRAPH_CONCURRENCY = 5
const MAX_GRAPH_CONCURRENCY = 20

const graphConfigForm = reactive({
  extractor_type: 'llm',
  model_spec: '',
  ontology_key: '',
  ontology_registry_id: '',
  ontology_version: '',
  ontology_digest: '',
  domain_schema: '',
  schema: '',
  concurrency_count: DEFAULT_GRAPH_CONCURRENCY,
  model_params_text: ''
})

const graph = reactive(useGraph(graphRef))
const graphLoaded = ref(false)

// 计算属性：是否支持知识图谱
const isGraphSupported = computed(() => GRAPH_SUPPORTED_KB_TYPES.has(kbType.value?.toLowerCase()))
const hasGraphNodes = computed(() => graph.graphData.nodes.length > 0)
const showGraphConfigEmpty = computed(
  () => isMilvus.value && !isGraphConfigured.value && !graphBuildLoading.value
)
const showGraphDataEmpty = computed(
  () =>
    isMilvus.value &&
    isGraphConfigured.value &&
    graphLoaded.value &&
    !graph.fetching &&
    !hasGraphNodes.value
)
const graphDataEmptyTitle = computed(() =>
  searchInput.value.trim() ? t('graph.noMatchingEntity') : t('graph.emptyTitle')
)
const graphDataEmptyDescription = computed(() => {
  if (searchInput.value.trim()) return t('graph.emptySearchHint')
  if (isBuildActive.value) return t('graph.emptyBuildingHint')
  if (hasPendingGraphChunks.value) return t('graph.emptyPendingHint')
  if (!graphBuildStatus.value?.total_chunks) return t('graph.emptyNoChunk')
  if (!graphBuildStatus.value?.published) return t('graph.emptyNotPublished')
  return t('graph.emptyNoResult')
})

let pendingLoadTimer = null
let graphStatusRequestSeq = 0
let graphLoadRequestSeq = 0

const getErrorDetail = (e, fallback) => {
  return e?.response?.data?.detail || e?.response?.data?.message || e?.message || fallback
}

const loadGraphBuildStatus = async () => {
  if (!kbId.value || !isMilvus.value) return
  const requestSeq = ++graphStatusRequestSeq
  const currentDatabaseId = kbId.value
  graphBuildLoading.value = true
  try {
    const status = await graphBuildApi.getStatus(currentDatabaseId)
    if (requestSeq === graphStatusRequestSeq && currentDatabaseId === kbId.value) {
      graphBuildStatus.value = status
    }
  } catch (e) {
    console.error('Failed to load graph build status:', e)
    message.error(t('graph.loadBuildStatusFailed'))
  } finally {
    if (requestSeq === graphStatusRequestSeq) {
      graphBuildLoading.value = false
    }
  }
}

const parseModelParams = () => {
  const text = graphConfigForm.model_params_text.trim()
  if (!text) return {}
  let params
  try {
    params = JSON.parse(text)
  } catch {
    throw new Error(t('graph.modelParamsInvalidJson'))
  }
  if (!params || Array.isArray(params) || typeof params !== 'object') {
    throw new Error(t('graph.modelParamsNotObject'))
  }
  return params
}

const ontologyEntryKey = (entry) =>
  `${entry.registry_id}:${entry.version}:${entry.digest}`

const ontologyRegistryOptions = computed(() =>
  ontologyRegistries.value.map((entry) => ({
    value: ontologyEntryKey(entry),
    label: `${entry.name} · ${entry.registry_id} · ${entry.version}`
  }))
)

const selectOntologyRegistry = (key) => {
  const entry = ontologyRegistries.value.find((item) => ontologyEntryKey(item) === key)
  if (!entry) return
  graphConfigForm.ontology_registry_id = entry.registry_id
  graphConfigForm.ontology_version = entry.version
  graphConfigForm.ontology_digest = entry.digest
}

const findConfiguredOntology = (options, ontology) => {
  const registryId = options.ontology_registry_id || ontology.registry_id
  const version = options.ontology_version || ontology.version
  const digest = options.ontology_digest || ontology.digest
  return ontologyRegistries.value.find((entry) => {
    if (entry.registry_id !== registryId || entry.version !== version) return false
    return !digest || entry.digest === digest
  })
}

const loadOntologyRegistries = async () => {
  ontologyRegistryLoading.value = true
  try {
    const result = await ontologyRegistryApi.list()
    ontologyRegistries.value = result.items || []
  } catch (e) {
    console.error('Failed to load ontology registries:', e)
    message.error(getErrorDetail(e, t('graph.loadOntologyFailed')))
  } finally {
    ontologyRegistryLoading.value = false
  }
}

const fillGraphConfigForm = () => {
  const config = graphBuildStatus.value?.config
  const options = config?.extractor_options || {}
  const ontology = graphBuildStatus.value?.ontology || {}
  graphConfigForm.extractor_type = 'llm'
  graphConfigForm.model_spec = options.model_spec || configStore.config?.default_model || ''
  const selectedOntology = findConfiguredOntology(options, ontology)
    || ontologyRegistries.value.find((entry) => entry.is_default)
    || ontologyRegistries.value.find((entry) => entry.source === 'builtin')
    || ontologyRegistries.value[0]
  graphConfigForm.ontology_key = selectedOntology ? ontologyEntryKey(selectedOntology) : ''
  graphConfigForm.ontology_registry_id = selectedOntology?.registry_id || ''
  graphConfigForm.ontology_version = selectedOntology?.version || ''
  graphConfigForm.ontology_digest = selectedOntology?.digest || ''
  graphConfigForm.domain_schema = options.domain_schema || ''
  graphConfigForm.schema = options.schema || ''
  graphConfigForm.concurrency_count = Number(options.concurrency_count || DEFAULT_GRAPH_CONCURRENCY)
  graphConfigForm.model_params_text = options.model_params
    ? JSON.stringify(options.model_params)
    : ''
}

const openGraphConfig = async () => {
  if (!isLegacyGraphConfig.value) {
    await loadOntologyRegistries()
    if (!ontologyRegistries.value.length) return
  }
  fillGraphConfigForm()
  showGraphConfig.value = true
}

const selectExtractorType = (option) => {
  if (isEditingGraphConfig.value || option.disabled) return
  graphConfigForm.extractor_type = option.value
}

const buildExtractorOptions = () => {
  const options = {
    model_spec: graphConfigForm.model_spec,
    concurrency_count: graphConfigForm.concurrency_count || DEFAULT_GRAPH_CONCURRENCY,
    model_params: parseModelParams()
  }
  if (isLegacyGraphConfig.value) {
    options.schema = graphConfigForm.schema.trim()
    return options
  }
  options.ontology_registry_id = graphConfigForm.ontology_registry_id
  options.ontology_version = graphConfigForm.ontology_version
  options.ontology_digest = graphConfigForm.ontology_digest
  options.domain_schema = graphConfigForm.domain_schema.trim()
  return options
}

const hasExistingGraphData = () => {
  const status = graphBuildStatus.value || {}
  return [
    status.indexed_chunks,
    status.extraction_result_count,
    status.entity_count,
    status.relationship_count
  ].some((count) => Number(count || 0) > 0)
}

const isOntologyChanged = () => {
  const options = graphBuildStatus.value?.config?.extractor_options || {}
  const ontology = graphBuildStatus.value?.ontology || {}
  return (
    graphConfigForm.ontology_registry_id !==
      (options.ontology_registry_id || ontology.registry_id) ||
    graphConfigForm.ontology_version !== (options.ontology_version || ontology.version) ||
    graphConfigForm.ontology_digest !== (options.ontology_digest || ontology.digest)
  )
}

const configureGraphBuild = async () => {
  try {
    if (
      !isLegacyGraphConfig.value &&
      isEditingGraphConfig.value &&
      isOntologyChanged() &&
      hasExistingGraphData()
    ) {
      message.warning(t('graph.switchOntologyRequiresReset'))
      return
    }
    document.activeElement?.blur()
    await nextTick()
    await graphBuildApi.configure(kbId.value, {
      extractor_type: 'llm',
      extractor_options: buildExtractorOptions()
    })
    message.success(isEditingGraphConfig.value ? t('graph.configUpdated') : t('graph.configSaved'))
    showGraphConfig.value = false
    await loadGraphBuildStatus()
  } catch (e) {
    console.error('Failed to configure graph build:', e)
    message.error(getErrorDetail(e, t('graph.configFailed')))
  }
}

const startGraphBuild = async () => {
  try {
    const data = await graphBuildApi.startIndex(kbId.value, 20)
    message.success(data.message || t('graph.buildSubmitted'))
    if (data.task_id) {
      taskerStore.registerQueuedTask({
        task_id: data.task_id,
        name: t('graph.buildTaskName', { kbId: kbId.value }),
        task_type: GRAPH_BUILD_TASK_TYPE,
        message: data.message,
        payload: { kb_id: kbId.value }
      })
    }
    await loadGraphBuildStatus()
  } catch (e) {
    console.error('Failed to start graph build:', e)
    message.error(getErrorDetail(e, t('graph.submitBuildFailed')))
  }
}

const confirmResetGraph = () => {
  Modal.confirm({
    title: t('graph.resetConfirmTitle'),
    content: t('graph.resetConfirmContent'),
    okText: t('graph.resetConfirmOk'),
    cancelText: t('common.cancel'),
    onOk: resetGraphBuild
  })
}

const resetGraphBuild = async () => {
  try {
    await graphBuildApi.reset(kbId.value, {
      clear_extraction_result: true,
      clear_config: true
    })
    message.success(t('graph.resetSuccess'))
    graphLoaded.value = false
    graph.clearGraph()
    await loadGraphBuildStatus()
  } catch (e) {
    console.error('Failed to reset graph build:', e)
    message.error(getErrorDetail(e, t('graph.resetFailed')))
  }
}

const loadGraph = async () => {
  if (!kbId.value || !isGraphSupported.value) return

  const requestSeq = ++graphLoadRequestSeq
  const currentDatabaseId = kbId.value
  graph.fetching = true
  if (!hasGraphNodes.value) {
    graphLoaded.value = false
  }
  try {
    const res = await unifiedApi.getSubgraph({
      kb_id: currentDatabaseId,
      node_label: searchInput.value || '*',
      max_nodes: subgraphParams.maxNodes,
      max_depth: subgraphParams.maxDepth,
      exclude_chunk: subgraphParams.excludeChunk
    })

    if (
      requestSeq === graphLoadRequestSeq &&
      currentDatabaseId === kbId.value &&
      res.success &&
      res.data
    ) {
      graph.updateGraphData(res.data.nodes, res.data.edges)
    }
  } catch (e) {
    console.error('Failed to load graph:', e)
    message.error(t('graph.loadGraphFailed'))
  } finally {
    if (requestSeq === graphLoadRequestSeq) {
      graph.fetching = false
      graphLoaded.value = true
    }
  }
}

const applySettings = () => {
  showSettings.value = false
  loadGraph()
}

const onSearch = () => {
  loadGraph()
}

const clearGraphSearch = () => {
  searchInput.value = ''
  loadGraph()
}

const scheduleGraphLoad = (delay = 200) => {
  if (!props.active || !isGraphSupported.value || !kbId.value) {
    return
  }

  if (pendingLoadTimer) {
    clearTimeout(pendingLoadTimer)
  }
  pendingLoadTimer = setTimeout(async () => {
    pendingLoadTimer = null
    await nextTick()
    if (props.active && isGraphSupported.value && kbId.value) {
      await loadGraph()
    }
  }, delay)
}

watch(
  () => props.active,
  (active) => {
    if (active) {
      if (isMilvus.value) {
        loadGraphBuildStatus()
      }
      scheduleGraphLoad()
    }
  },
  { immediate: true }
)

watch(kbId, () => {
  graphStatusRequestSeq += 1
  graphLoadRequestSeq += 1
  graphLoaded.value = false
  graph.clearGraph()
  graphBuildStatus.value = null
  if (isMilvus.value) {
    loadGraphBuildStatus()
  }
  if (isGraphSupported.value) {
    scheduleGraphLoad(300)
  }
})

watch(isGraphSupported, (supported) => {
  if (!supported) {
    graphLoaded.value = false
    graph.clearGraph()
    graphBuildStatus.value = null
    return
  }
  if (isMilvus.value) {
    loadGraphBuildStatus()
  }
  scheduleGraphLoad(200)
})

onUnmounted(() => {
  if (pendingLoadTimer) {
    clearTimeout(pendingLoadTimer)
    pendingLoadTimer = null
  }
  stopBuildStatusPoll()
})
</script>

<style scoped lang="less">
.ontology-help {
  margin-top: 6px;
  color: var(--gray-600);
  font-size: 12px;
  line-height: 1.5;
}

.graph-section {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  position: relative;
  user-select: none;
}

.graph-container-compact {
  flex: 1;
  min-height: 0;
  overflow: hidden;
  position: relative;
}

.graph-wrapper {
  height: 100%;
  width: 100%;
  position: relative;
}

.graph-empty-state {
  position: absolute;
  inset: 0;
  z-index: 30;
  pointer-events: none;

  :deep(.resource-empty-state__actions) {
    pointer-events: auto;
  }
}

.compact-actions {
  position: absolute;
  top: 10px;
  left: 10px;
  right: 10px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  pointer-events: none; /* Let clicks pass through empty areas */

  .actions-left,
  .actions-right {
    pointer-events: auto; /* Re-enable clicks for buttons/inputs */
    display: flex;
    align-items: center;
    gap: 4px;
    background: var(--color-trans-light);
    backdrop-filter: blur(12px);
    padding: 2px;
    border-radius: 8px;
    box-shadow: 0 0 4px 0px var(--shadow-2);
    border: 1px solid var(--gray-100);
  }

  :deep(.ant-input-affix-wrapper) {
    padding: 4px 11px;
    border-radius: 6px;
    border-color: transparent;
    box-shadow: none;
    background: var(--color-trans-light);

    &:hover,
    &:focus,
    &-focused {
      background: var(--main-0);
      border-color: var(--primary-color);
    }

    input {
      background: transparent;
    }
  }

  .action-btn {
    width: 32px;
    height: 32px;
    padding: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    background: transparent;
    color: var(--gray-600);
    border-radius: 6px;
    box-shadow: none;
    position: relative;

    &:hover {
      background: var(--shadow-1);
      color: var(--primary-color);
    }
  }

  .index-action-btn {
    gap: 6px;
    overflow: visible;

    &.has-index-label {
      width: auto;
      min-width: 84px;
      padding: 0 22px 0 8px;
      justify-content: flex-start;
    }

    .index-status-label {
      font-size: 12px;
      line-height: 1;
      color: var(--gray-700);
      white-space: nowrap;
    }
  }

  .status-dot {
    position: absolute;
    bottom: 4px;
    right: 4px;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    box-shadow: 0 0 0 1px var(--color-trans-light);
  }

  .status-dot--pending {
    background: var(--color-warning-500);
  }

  .status-dot--active {
    background: var(--color-warning-500);
    animation: blink 1.2s ease-in-out infinite;
  }

  .status-dot--complete {
    background: var(--color-success-500);
  }

  .search-suffix-icon {
    cursor: pointer;
  }

  .spin {
    animation: spin 1s linear infinite;
  }
}

@keyframes blink {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.2;
  }
}

.graph-disabled {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 100%;
}

.disabled-content {
  text-align: center;
  color: var(--gray-400);

  h4 {
    margin-bottom: 8px;
  }
}

.floating-panel {
  position: absolute;
  top: 60px;
  right: 10px;
  width: 300px;
  max-height: calc(100% - 60px);
  overflow-y: auto;
  z-index: 100;
  background: var(--color-trans-light);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-radius: 8px;
  border: 1px solid var(--gray-100);
  box-shadow: 0 0 4px 0px var(--shadow-2);
  font-size: 13px;

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    border-bottom: 1px solid var(--gray-200);

    .panel-title {
      font-size: 13px;
      font-weight: 600;
      color: var(--gray-1000);
    }

    .panel-refresh-btn {
      padding: 2px 6px;
    }
  }

  .panel-body {
    padding: 10px 14px;
  }
}

.build-panel {
  .status-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;

    .status-label {
      color: var(--gray-600);
      font-size: 12px;
    }
  }

  .build-task-alert {
    margin-bottom: 8px;
  }

  .build-result-summary {
    display: flex;
    gap: 12px;
    margin-bottom: 10px;
    color: var(--gray-600);
    font-size: 12px;
  }

  .stats-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
    margin-bottom: 12px;
  }

  .stat-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding: 6px 4px;
    border-radius: 4px;
    background: var(--gray-50);

    .stat-value {
      font-size: 15px;
      font-weight: 600;
      color: var(--gray-1000);
      line-height: 1.2;
    }

    .stat-label {
      font-size: 11px;
      color: var(--gray-500);
      margin-top: 2px;
    }
  }

  .build-actions {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .actions-secondary {
    display: flex;
    justify-content: space-between;
  }
}

.config-warning {
  margin-bottom: 16px;
}

.extractor-type-cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;

  .extractor-type-card {
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    padding: 14px;
    cursor: pointer;
    transition: all 0.2s ease;
    background: var(--gray-0);

    &:hover {
      border-color: var(--main-color);
    }

    &.active {
      border-color: var(--main-color);
      background: var(--main-10);
      box-shadow: 0 0 0 1px var(--main-20);

      .type-icon {
        color: var(--main-color);
      }
    }

    &.disabled {
      cursor: not-allowed;
      opacity: 0.72;
      background: var(--gray-50);

      &:hover {
        border-color: var(--gray-150);
      }
    }

    .card-header {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 10px;
    }

    .type-icon {
      width: 20px;
      height: 20px;
      color: var(--main-color);
      flex-shrink: 0;
    }

    .type-title {
      font-size: 15px;
      font-weight: 600;
      color: var(--gray-800);
    }

    .card-description {
      font-size: 13px;
      color: var(--gray-600);
      line-height: 1.5;
    }

    .card-helper {
      margin-top: 8px;
      font-size: 12px;
      color: var(--gray-500);

      &.warning {
        color: var(--color-warning-500);
      }
    }
  }
}

.form-grid.two-columns {
  display: grid;
  grid-template-columns: 180px 1fr;
  gap: 12px;

  @media (max-width: 640px) {
    grid-template-columns: 1fr;
  }
}

.slide-fade-enter-active {
  transition: all 0.25s ease-out;
}

.slide-fade-leave-active {
  transition: all 0.2s cubic-bezier(1, 0.5, 0.8, 1);
}

.slide-fade-enter-from,
.slide-fade-leave-to {
  transform: translateX(20px);
  opacity: 0;
}
</style>
