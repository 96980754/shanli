<template>
  <div class="database-info-container extension-detail-page">
    <FileDetailModal
      v-model:open="store.state.fileDetailModalVisible"
      :kb-id="kbId"
      :file-id="store.fileDetailFileId"
      :can-download="kbPermissions.can_download"
      @closed="store.closeFileDetail"
    />

    <FileUploadModal
      v-model:visible="addFilesModalVisible"
      :folder-tree="folderTree"
      :current-folder-id="currentFolderId"
      :current-path-prefix="currentPathPrefix"
      :is-folder-mode="isFolderUploadMode"
      :mode="addFilesMode"
      :can-upload="kbPermissions.can_upload"
      :can-manage="kbPermissions.can_manage"
      :defer-processing="!userStore.isAdmin"
      @success="onFileUploadSuccess"
    />

    <div v-if="detailLoading" class="database-detail-loading">
      <a-spin :tip="t('dbInfo.loadingKbInfo')" />
    </div>

    <template v-else>
      <div class="detail-top-bar">
        <button class="detail-back-btn" type="button" @click="backToDatabase">
          <ArrowLeft :size="16" />
          <span>{{ $t('common.back') }}</span>
        </button>
        <div class="detail-title-area">
          <div class="detail-icon">
            <component :is="kbTypeIcon" :size="18" />
          </div>
          <div class="detail-title-text">
            <h2>{{ database.name || $t('dbInfo.kbLoading') }}</h2>
            <span class="detail-subtitle">{{ databaseSubtitle }}</span>
          </div>
        </div>
        <div class="detail-actions">
          <a-space :size="8">
            <button
              type="button"
              class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
              @click="copyDatabaseId"
            >
              <Copy :size="14" />
              <span>{{ $t('dbInfo.copyId') }}</span>
            </button>
            <button
              v-if="kbPermissions.can_grant"
              type="button"
              class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
              @click="activeTab = 'permissions'"
            >
              <Settings :size="14" />
              <span>{{ $t('dbInfo.permissionSettings') }}</span>
            </button>
            <button
              v-if="kbPermissions.can_manage"
              type="button"
              class="lucide-icon-btn extension-panel-action extension-panel-action-primary"
              @click="showEditModal"
            >
              <Pencil :size="14" />
              <span>{{ $t('common.edit') }}</span>
            </button>
          </a-space>
        </div>
      </div>

      <div class="database-detail-body">
        <div class="database-tab-bar" :aria-label="t('dbInfo.kbFunctionNav')">
          <nav class="database-tab-list" :aria-label="t('dbInfo.kbFunctionTabs')" role="tablist">
            <button
              v-for="tab in visibleTabs"
              :key="tab.key"
              type="button"
              class="database-tab-item"
              :class="{ active: activeTab === tab.key }"
              role="tab"
              :aria-selected="activeTab === tab.key"
              @click="activeTab = tab.key"
            >
              <component :is="tab.icon" :size="17" />
              <span>{{ getTabLabel(tab.label) }}</span>
            </button>
          </nav>
        </div>

        <main class="database-tab-content">
          <div v-if="isMilvus" v-show="activeTab === 'filetable'" class="tab-panel file-panel">
            <div class="file-management-info">
              <div class="file-info-title">
                <div class="file-info-title-row">
                  <button
                    v-if="kbPermissions.can_upload"
                    type="button"
                    class="lucide-icon-btn extension-panel-action extension-panel-action-primary"
                    @click="showAddFilesModal()"
                  >
                    <FileUp :size="14" />
                    <span>{{ $t('common.upload') }}</span>
                  </button>
                  <button
                    v-if="userStore.isAdmin"
                    type="button"
                    class="lucide-icon-btn extension-panel-action extension-panel-action-secondary"
                    @click="showCreateFolderModal"
                  >
                    <FolderPlus :size="14" />
                    <span>{{ $t('dbInfo.newFolder') }}</span>
                  </button>
                </div>
              </div>
              <div class="file-panel-status">
                <button
                  v-if="userStore.isAdmin && pendingParseCount > 0"
                  type="button"
                  class="file-stat-card file-stat-action file-stat-summary"
                  :disabled="store.state.chunkLoading"
                  @click="confirmBatchParse"
                >
                  <FileText :size="16" />
                  <div class="file-stat-inline">
                    <strong>{{ pendingParseCount }}</strong>
                    <span>{{ $t('dbInfo.pendingReview') }}</span>
                  </div>
                </button>
                <button
                  v-if="userStore.isAdmin && pendingIndexCount > 0"
                  type="button"
                  class="file-stat-card file-stat-action file-stat-summary"
                  :disabled="store.state.chunkLoading"
                  @click="confirmBatchIndex"
                >
                  <DatabaseIcon :size="16" />
                  <div class="file-stat-inline">
                    <strong>{{ pendingIndexCount }}</strong>
                    <span>{{ $t('dbInfo.pendingIndex') }}</span>
                  </div>
                </button>
                <div class="file-stat-card file-stat-summary">
                  <FileText :size="16" />
                  <div class="file-stat-inline">
                    <strong>{{ fileStats.count }}</strong>
                    <span>{{ $t('dbInfo.files') }}</span>
                  </div>
                </div>
                <div v-if="fileStats.sizeText" class="file-stat-card file-stat-summary">
                  <DatabaseIcon :size="16" />
                  <div class="file-stat-inline">
                    <strong>{{ fileStats.sizeText }}</strong>
                    <span>{{ $t('dbInfo.totalSize') }}</span>
                  </div>
                </div>
                <button
                  v-if="userStore.isAdmin"
                  type="button"
                  class="file-stat-card file-stat-summary file-stat-repair"
                  :disabled="statsRepairing"
                  :aria-busy="statsRepairing"
                  :aria-label="t('dbInfo.repairStatsLabel')"
                  :title="t('dbInfo.repairStatsLabel')"
                  @click="repairDatabaseStats"
                >
                  <LoaderCircle v-if="statsRepairing" :size="16" class="file-stat-spinner" />
                  <DatabaseIcon v-else :size="16" />
                  <div class="file-stat-inline">
                    <strong>{{ fileStats.chunkText }}</strong>
                    <span>Chunks</span>
                  </div>
                </button>
                <button
                  v-if="userStore.isAdmin"
                  type="button"
                  class="file-stat-card file-stat-summary file-stat-repair"
                  :disabled="statsRepairing"
                  :aria-busy="statsRepairing"
                  :aria-label="t('dbInfo.repairStatsLabel')"
                  :title="t('dbInfo.repairStatsLabel')"
                  @click="repairDatabaseStats"
                >
                  <LoaderCircle v-if="statsRepairing" :size="16" class="file-stat-spinner" />
                  <Hash v-else :size="16" />
                  <div class="file-stat-inline">
                    <strong>{{ fileStats.tokenText }}</strong>
                    <span>Tokens</span>
                  </div>
                </button>
              </div>
            </div>
            <FileTable
              ref="fileTableRef"
              :can-upload="userStore.isAdmin"
              :can-download="kbPermissions.can_download"
              :can-delete="kbPermissions.can_delete"
              :can-manage="userStore.isAdmin"
            />
          </div>

          <div
            v-if="kbPermissions.can_search"
            v-show="activeTab === 'query'"
            class="tab-panel query-config-panel"
          >
            <div class="query-config-layout">
              <div class="query-test-pane">
                <QuerySection
                  ref="querySectionRef"
                  :visible="true"
                  :can-manage="kbPermissions.can_manage"
                  @toggle-visible="() => {}"
                />
              </div>
              <aside
                v-if="kbPermissions.can_manage"
                class="query-config-pane"
                :aria-label="t('dbInfo.queryConfigAria')"
              >
                <div class="search-config-wrapper">
                  <div v-if="kbPermissions.can_manage" class="query-config-header">
                    <div>
                      <h3>{{ $t('dbInfo.queryConfig') }}</h3>
                      <p>{{ $t('dbInfo.queryConfigDesc') }}</p>
                    </div>
                    <button
                      type="button"
                      class="lucide-icon-btn extension-panel-action extension-panel-action-primary"
                      :disabled="searchConfigSaving"
                      @click="handleInlineSearchConfigSave"
                    >
                      <Save :size="14" />
                      <span>{{ $t('common.save') }}</span>
                    </button>
                  </div>
                  <div class="search-config-body">
                    <SearchConfigPanel
                      ref="searchConfigPanelRef"
                      :kb-id="kbId"
                      @save="handleSearchConfigSave"
                    />
                  </div>
                </div>
              </aside>
            </div>
          </div>

          <div v-if="isMilvus && userStore.isAdmin && activeTab === 'graph'" class="tab-panel">
            <KnowledgeGraphSection
              :visible="true"
              :active="activeTab === 'graph'"
              @toggle-visible="() => {}"
            />
          </div>

          <div v-if="activeTab === 'permissions'" class="tab-panel">
            <KnowledgePermissionPanel v-if="kbId" :kb-id="kbId" :database="database" />
          </div>
        </main>
      </div>
    </template>

    <a-modal v-model:open="editModalVisible" :title="t('dbInfo.editKbTitle')" width="700px">
      <template #footer>
        <a-button danger @click="deleteDatabase" style="margin-right: auto; margin-left: 0">
          <template #icon>
            <Trash2 :size="16" style="vertical-align: -3px; margin-right: 4px" />
          </template>
          {{ $t('dbInfo.deleteKb') }}
        </a-button>
        <a-button key="back" @click="editModalVisible = false">{{ $t('common.cancel') }}</a-button>
        <a-button key="submit" type="primary" @click="handleEditSubmit">{{ $t('common.ok') }}</a-button>
      </template>
      <a-form :model="editForm" :rules="rules" ref="editFormRef" layout="vertical">
        <a-form-item :label="t('dbInfo.kbNameLabel')" name="name" required>
          <a-input v-model:value="editForm.name" :placeholder="t('dbInfo.kbNamePlaceholder')" />
        </a-form-item>
        <a-form-item :label="t('dbInfo.categoryLabel')" name="category_id" required>
          <a-select
            v-model:value="editForm.category_id"
            :options="categoryOptions"
            :placeholder="t('dbInfo.categoryPlaceholder')"
          />
        </a-form-item>
        <a-form-item :label="t('dbInfo.kbDescLabel')" name="description">
          <AiTextarea
            v-model="editForm.description"
            :name="editForm.name"
            :files="fileList"
            :placeholder="t('dbInfo.kbDescPlaceholder')"
            action-placement="header"
            :rows="4"
          />
        </a-form-item>

        <a-form-item v-if="isMilvus && !isConnector" :label="t('dbInfo.embeddingModelLabel')" name="embedding_model_spec">
          <EmbeddingModelSelector
            :value="editForm.embedding_model_spec"
            @update:value="handleEmbeddingModelChange"
          />
        </a-form-item>

        <a-form-item v-if="!isConnector" :label="t('dbInfo.autoGenQuestionsLabel')" name="auto_generate_questions">
          <a-switch
            v-model:checked="editForm.auto_generate_questions"
            :checked-children="t('dbInfo.on')"
            :un-checked-children="t('dbInfo.off')"
          />
          <span style="margin-left: 8px; font-size: 12px; color: var(--gray-500)">
            {{ $t('dbInfo.autoGenQuestionsHint') }}
          </span>
        </a-form-item>

        <a-form-item v-if="!isConnector" name="chunk_preset_id">
          <template #label>
            <span class="chunk-preset-label">
              {{ $t('dbInfo.chunkStrategy') }}
              <a-tooltip :title="editPresetDescription">
                <QuestionCircleOutlined class="chunk-preset-help-icon" />
              </a-tooltip>
            </span>
          </template>
          <a-select
            v-model:value="editForm.chunk_preset_id"
            :options="chunkPresetOptions"
            :loading="chunkPresetLoading"
          />
        </a-form-item>

        <template v-if="isDifyKb">
          <a-form-item :label="t('dbInfo.difyApiUrlLabel')" name="dify_api_url">
            <a-input
              v-model:value="editForm.dify_api_url"
              :placeholder="t('dbInfo.difyApiUrlPlaceholder')"
            />
          </a-form-item>
          <a-form-item :label="t('dbInfo.difyTokenLabel')" name="dify_token">
            <a-input-password
              v-model:value="editForm.dify_token"
              :placeholder="t('dbInfo.difyTokenPlaceholder')"
            />
          </a-form-item>
          <a-form-item :label="t('dbInfo.datasetIdLabel')" name="dify_dataset_id">
            <a-input
              v-model:value="editForm.dify_dataset_id"
              :placeholder="t('dbInfo.datasetIdPlaceholder')"
            />
          </a-form-item>
        </template>

        <template v-if="isNotionKb">
          <a-form-item :label="t('dbInfo.notionTokenLabel')" name="notion_token">
            <a-input-password
              v-model:value="editForm.notion_token"
              :placeholder="t('dbInfo.notionTokenPlaceholder')"
            />
          </a-form-item>
          <a-form-item :label="t('dbInfo.dataSourceIdLabel')" name="notion_data_source_id">
            <a-input
              v-model:value="editForm.notion_data_source_id"
              :placeholder="t('dbInfo.dataSourceIdPlaceholder')"
            />
          </a-form-item>
          <a-form-item :label="t('dbInfo.notionApiVersionLabel')" name="notion_version">
            <a-input v-model:value="editForm.notion_version" placeholder="2026-03-11" />
          </a-form-item>
        </template>

        <a-form-item v-if="canEditShareConfig" :label="t('dbInfo.shareSettingsLabel')" name="share_config">
          <a-form-item-rest>
            <ShareConfigForm ref="shareConfigFormRef" :model-value="database.share_config" />
          </a-form-item-rest>
        </a-form-item>
        <a-form-item
          v-else-if="database.share_config"
          :label="t('dbInfo.shareSettingsLabel')"
          name="share_config_readonly"
        >
          <div class="share-config-readonly">
            <a-tag :color="shareConfigDisplay.color">
              {{ shareConfigDisplay.label }}
            </a-tag>
            <span class="access-names">{{ shareConfigDisplay.detail }}</span>
          </div>
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, defineAsyncComponent, h, onMounted, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { useDatabaseStore } from '@/stores/database'
import { useTaskerStore } from '@/stores/tasker'
import { useUserStore } from '@/stores/user'
import {
  ArrowLeft,
  Copy,
  Database as DatabaseIcon,
  FileUp,
  FileText,
  FolderPlus,
  Hash,
  LoaderCircle,
  Network,
  Pencil,
  Save,
  Search,
  Settings,
  Trash2
} from 'lucide-vue-next'
import { QuestionCircleOutlined } from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import FileTable from '@/components/FileTable.vue'
import FileDetailModal from '@/components/FileDetailModal.vue'
import FileUploadModal from '@/components/FileUploadModal.vue'
// 图谱区重（g6/sigma/graphology），懒加载：仅在打开「图谱」Tab 时才拉取渲染，
// 避免进入知识库详情页就同步解析大 chunk 造成切换卡顿。
const KnowledgeGraphSection = defineAsyncComponent({
  loader: () => import('@/components/KnowledgeGraphSection.vue'),
  loadingComponent: {
    setup() {
      const { t } = useI18n()
      return () => h('div', { class: 'graph-section-loading' }, t('dbInfo.graphLoading'))
    }
  }
})
import QuerySection from '@/components/QuerySection.vue'
import SearchConfigPanel from '@/components/SearchConfigPanel.vue'
import EmbeddingModelSelector from '@/components/EmbeddingModelSelector.vue'
import KnowledgePermissionPanel from '@/components/KnowledgePermissionPanel.vue'
import AiTextarea from '@/components/AiTextarea.vue'
import ShareConfigForm from '@/components/ShareConfigForm.vue'
import { databaseApi, categoryApi } from '@/apis/knowledge_api'
import { departmentApi } from '@/apis/department_api'
import { authApi } from '@/apis/auth_api'
import { useChunkPresetOptions } from '@/composables/useChunkPresetOptions'
import { DEFAULT_CHUNK_PRESET_ID } from '@/utils/chunkUtils'
import { formatFileSize } from '@/utils/file_utils'
import { getKbTypeIcon, getKbTypeLabel, kbUtils } from '@/utils/kb_utils'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const store = useDatabaseStore()
const taskerStore = useTaskerStore()
const userStore = useUserStore()
const {
  chunkPresetSelectOptions: chunkPresetOptions,
  chunkPresetLoading,
  loadChunkPresetOptions,
  getChunkPresetDescription
} = useChunkPresetOptions()

const kbId = computed(() => store.kbId)
const database = computed(() => store.database)
const isCurrentDatabaseLoaded = computed(() => database.value?.kb_id === kbId.value)
const kbType = computed(() =>
  isCurrentDatabaseLoaded.value ? database.value.kb_type?.toLowerCase() || 'milvus' : ''
)
const isMilvus = computed(() => kbType.value === 'milvus')
const isDifyKb = computed(() => kbType.value === 'dify')
const isNotionKb = computed(() => kbType.value === 'notion')
const isConnector = computed(
  () => isCurrentDatabaseLoaded.value && kbUtils.isReadOnlyDatabase(database.value)
)
const kbPermissions = reactive({
  can_view: false,
  can_search: false,
  can_upload: false,
  can_download: false,
  can_delete: false,
  can_manage: false,
  can_grant: false,
  can_export: false
})

const loadDatabaseAccess = async (databaseId) => {
  const access = await databaseApi.getDatabaseAccess(databaseId)
  Object.assign(kbPermissions, access)
}

const kbTypeIcon = computed(() => getKbTypeIcon(kbType.value || 'milvus'))

const databaseSubtitle = computed(() => {
  const typeLabel = getKbTypeLabel(kbType.value || 'milvus')
  if (!isCurrentDatabaseLoaded.value) return t('dbInfo.loadingKbInfoProgress')

  const description = database.value.description?.trim()
  if (description) return description

  if (isConnector.value) return t('dbInfo.connectorSuffix', { type: typeLabel })
  return t('dbInfo.kbSubtitle', { type: typeLabel, count: database.value.row_count || 0 })
})

const getTabLabel = (label) => (typeof label === 'string' && label.includes('.') ? t(label) : label)

const tabs = computed(() => {
  const items = []
  if (isMilvus.value && kbPermissions.can_view) {
    items.push({ key: 'filetable', label: 'dbInfo.tabFileManagement', icon: FileText })
  }
  if (kbPermissions.can_search) {
    items.push({ key: 'query', label: 'dbInfo.tabQueryTest', icon: Search })
  }
  if (isMilvus.value && userStore.isAdmin) {
    items.push({ key: 'graph', label: 'dbInfo.tabKnowledgeGraph', icon: Network })
  }
  if (kbPermissions.can_grant) {
    items.push({ key: 'permissions', label: 'dbInfo.permissionSettings', icon: Settings })
  }
  return items
})

const visibleTabs = computed(() => tabs.value)
const activeTab = ref('filetable')

watch(
  () => [kbId.value, isMilvus.value],
  ([newDbId, isMilvusType]) => {
    if (!newDbId) return
    activeTab.value = isMilvusType ? 'filetable' : 'query'
  },
  { immediate: true }
)

watch(visibleTabs, (nextTabs) => {
  if (!nextTabs.some((tab) => tab.key === activeTab.value)) {
    activeTab.value = nextTabs[0]?.key || 'query'
  }
})

const pendingParseCount = computed(() => {
  return Number(store.database.stats?.pending_parse_count || 0)
})

const formatStatNumber = (value) => {
  const number = Number(value ?? 0)
  return Number.isFinite(number) ? number.toLocaleString('zh-CN') : '0'
}

const formatTokenStatNumber = (value) => {
  const number = Number(value ?? 0)
  if (!Number.isFinite(number)) return '0'
  const absNumber = Math.abs(number)
  if (absNumber > 1024 * 1000) return `${(number / 1_000_000).toFixed(1)} m`
  if (absNumber >= 1000) return `${Math.round(number / 1000).toLocaleString('zh-CN')} k`
  return number.toLocaleString('zh-CN')
}

const statsRepairing = ref(false)

const fileStats = computed(() => {
  const stats = store.database.stats || {}
  const statsFileCount = Number(stats.file_count)
  const totalSize = Number(stats.total_size || 0)

  return {
    count: Number.isFinite(statsFileCount) ? statsFileCount : 0,
    sizeText: totalSize > 0 ? formatFileSize(totalSize) : '',
    chunkText: formatStatNumber(stats.chunk_count),
    tokenText: formatTokenStatNumber(stats.token_count)
  }
})

const repairDatabaseStats = async () => {
  if (!kbId.value || statsRepairing.value) return

  statsRepairing.value = true
  try {
    const result = await databaseApi.repairDatabaseStats(kbId.value)
    await store.getDatabaseInfo(undefined, true, true)

    const updatedTokenFiles = Number(result?.updated_token_files || 0)
    const updatedChunkFiles = Number(result?.updated_chunk_files || 0)
    if (updatedTokenFiles || updatedChunkFiles) {
      message.success(
        t('dbInfo.statsRepaired', {
          tokenCount: updatedTokenFiles,
          chunkCount: updatedChunkFiles
        })
      )
    } else {
      message.info(t('dbInfo.statsUpToDate'))
    }
  } catch (error) {
    console.error(error)
    message.error(error.message || t('dbInfo.statsRepairFailed'))
  } finally {
    statsRepairing.value = false
  }
}

const pendingIndexCount = computed(() => {
  return Number(store.database.stats?.pending_index_count || 0)
})

const confirmBatchParse = () => {
  const count = pendingParseCount.value
  if (count <= 0) {
    message.info(t('dbInfo.noPendingReviewDocs'))
    return
  }

  Modal.confirm({
    title: t('dbInfo.parsePendingTitle'),
    content: t('dbInfo.parsePendingContent', { count: formatStatNumber(count) }),
    okText: t('dbInfo.submitParse'),
    cancelText: t('common.cancel'),
    onOk: () => store.parsePendingFiles(count)
  })
}

const confirmBatchIndex = () => {
  const count = pendingIndexCount.value
  if (count <= 0) {
    message.info(t('fileTable.noPendingIndexDocs'))
    return
  }

  const opened = fileTableRef.value?.startPendingIndex?.(count)
  if (!opened) {
    message.error(t('dbInfo.fileListNotReady'))
  }
}

const querySectionRef = ref(null)
const searchConfigSaving = ref(false)
const searchConfigPanelRef = ref(null)

const handleSearchConfigSave = () => {
  store.getDatabaseInfo()
}

const handleInlineSearchConfigSave = async () => {
  if (!searchConfigPanelRef.value) return
  searchConfigSaving.value = true
  try {
    await searchConfigPanelRef.value.save()
  } finally {
    searchConfigSaving.value = false
  }
}

const addFilesModalVisible = ref(false)
const currentFolderId = ref(null)
const currentPathPrefix = ref('')
const isFolderUploadMode = ref(false)
const addFilesMode = ref('file')
const isInitialLoad = ref(true)
const detailLoading = ref(true)
const fileTableRef = ref(null)

const showAddFilesModal = (options = {}) => {
  const { isFolder = false, mode = 'file' } = options
  isFolderUploadMode.value = isFolder
  addFilesMode.value = mode
  addFilesModalVisible.value = true
  currentFolderId.value =
    fileTableRef.value?.getCurrentFolderId?.() || store.fileBrowser.parentId || null
  // 路径型虚拟目录：无 parent_id，上传时透传 path_prefix 避免落到根目录
  currentPathPrefix.value = store.fileBrowser.pathPrefix || ''
}

const showCreateFolderModal = () => {
  fileTableRef.value?.showCreateFolderModal()
}

const folderTree = computed(() => {
  const roots = []
  let currentLevel = roots
  for (const item of (store.folderBreadcrumbs || [])
    .slice(1)
    .filter((node) => !node.is_virtual_folder)) {
    const node = {
      file_id: item.file_id,
      filename: item.filename,
      is_folder: true,
      children: []
    }
    currentLevel.push(node)
    currentLevel = node.children
  }
  return roots
})

const onFileUploadSuccess = () => {
  taskerStore.loadTasks()
}

const resetFileSelectionState = () => {
  store.selectedRowKeys = []
  store.closeFileDetail()
  // 不在此处 resetFileBrowser：文件浏览器由 FileTable 在 kbId watch 中负责重置与导航，
  // 这里再 reset 会因 fileBrowserContextId 失效丢弃 ?path= 直达目录的加载结果（空白目录问题）
}

watch(
  () => route.params.kbId,
  async (nextKbId) => {
    isInitialLoad.value = true
    detailLoading.value = true
    store.kbId = nextKbId
    resetFileSelectionState()
    store.stopAutoRefresh()
    try {
      await loadDatabaseAccess(nextKbId)
      await store.getDatabaseInfo(nextKbId, !userStore.isAdmin)
      store.startAutoRefresh()
    } finally {
      detailLoading.value = false
    }
  },
  { immediate: true }
)

const previousFileCount = ref(0)

watch(
  () => database.value?.stats?.file_count,
  (newFileCountValue) => {
    const newFileCount = Number(newFileCountValue || 0)
    const oldFileCount = previousFileCount.value

    if (isInitialLoad.value) {
      previousFileCount.value = newFileCount
      isInitialLoad.value = false
      return
    }

    if (newFileCount !== oldFileCount) {
      if (newFileCount > 0) {
        setTimeout(async () => {
          if (querySectionRef.value) {
            if (database.value.additional_params?.auto_generate_questions) {
              await querySectionRef.value.generateSampleQuestions(true)
            }
          } else {
            setTimeout(async () => {
              if (
                querySectionRef.value &&
                database.value.additional_params?.auto_generate_questions
              ) {
                await querySectionRef.value.generateSampleQuestions(true)
              }
            }, 2000)
          }
        }, 3000)
      } else {
        setTimeout(() => {
          querySectionRef.value?.clearQuestions()
        }, 1000)
      }
    }

    previousFileCount.value = newFileCount
  },
  { deep: false }
)

const backToDatabase = () => {
  router.push({ path: '/extensions', query: { tab: 'knowledge' } })
}

const copyDatabaseId = async () => {
  if (!database.value.kb_id) {
    message.warning(t('dbInfo.kbIdEmpty'))
    return
  }

  try {
    await navigator.clipboard.writeText(database.value.kb_id)
    message.success(t('dbInfo.kbIdCopied'))
  } catch {
    const textArea = document.createElement('textarea')
    textArea.value = database.value.kb_id
    document.body.appendChild(textArea)
    textArea.select()
    document.execCommand('copy')
    document.body.removeChild(textArea)
    message.success(t('dbInfo.kbIdCopied'))
  }
}

const departments = ref([])
const users = ref([])
const categories = ref([])
const categoryOptions = computed(() =>
  categories.value.map((category) => ({ label: category.name, value: category.id }))
)
const editModalVisible = ref(false)
const editFormRef = ref(null)
const shareConfigFormRef = ref(null)
const editForm = reactive({
  name: '',
  description: '',
  embedding_model_spec: '',
  category_id: null,
  auto_generate_questions: false,
  chunk_preset_id: DEFAULT_CHUNK_PRESET_ID,
  dify_api_url: '',
  dify_token: '',
  dify_dataset_id: '',
  notion_token: '',
  notion_data_source_id: '',
  notion_version: '2026-03-11'
})

const rules = computed(() => ({
  name: [{ required: true, message: t('dbInfo.kbNamePlaceholder') }],
  category_id: [{ required: true, message: t('dbInfo.categoryPlaceholder') }]
}))

const editPresetDescription = computed(() => getChunkPresetDescription(editForm.chunk_preset_id))
const fileList = computed(() => {
  return (store.documentFiles || []).map((f) => f.filename).filter(Boolean)
})

const canEditShareConfig = computed(() => kbPermissions.can_manage)

const shareConfigDisplay = computed(() => {
  const shareConfig = database.value?.share_config || { access_level: 'global' }
  if (shareConfig.access_level === 'department') {
    const departmentIds = shareConfig.department_ids || []
    const names = departmentIds.map((id) => getDepartmentName(id)).join('、') || t('dbInfo.none')
    return {
      color: 'blue',
      label: t('dbInfo.departmentShare'),
      detail: t('dbInfo.departmentShareDetail', { count: departmentIds.length, names })
    }
  }

  if (shareConfig.access_level === 'user') {
    const userUids = shareConfig.user_uids || []
    const names = userUids.map((uid) => getUserName(uid)).join('、') || t('dbInfo.none')
    return {
      color: 'purple',
      label: t('dbInfo.specificUsers'),
      detail: t('dbInfo.userShareDetail', { count: userUids.length, names })
    }
  }

  return {
    color: 'green',
    label: t('dbInfo.globalShare'),
    detail: t('dbInfo.globalShareDetail')
  }
})

const getDepartmentName = (id) => {
  const dept = departments.value.find((item) => Number(item.id) === Number(id))
  return dept?.name || t('dbInfo.departmentName', { id })
}

const getUserName = (uid) => {
  const user = users.value.find((item) => item.uid === uid)
  return user?.username || uid
}

const loadDepartments = async () => {
  try {
    const res = await departmentApi.getDepartments()
    departments.value = res.departments || res || []
  } catch {
    departments.value = []
  }
}

const loadUsers = async () => {
  try {
    users.value = await authApi.getUserAccessOptions()
  } catch {
    users.value = []
  }
}

const loadCategories = async () => {
  try {
    const data = await categoryApi.getCategories()
    categories.value = data.items || []
  } catch {
    categories.value = []
  }
}

const showEditModal = () => {
  editForm.name = database.value.name || ''
  editForm.description = database.value.description || ''
  editForm.embedding_model_spec = database.value.embedding_model_spec || ''
  editForm.category_id = database.value.category_id || null
  editForm.auto_generate_questions =
    database.value.additional_params?.auto_generate_questions || false
  editForm.chunk_preset_id =
    database.value.additional_params?.chunk_preset_id || DEFAULT_CHUNK_PRESET_ID
  editForm.dify_api_url = database.value.additional_params?.dify_api_url || ''
  editForm.dify_token = database.value.additional_params?.dify_token || ''
  editForm.dify_dataset_id = database.value.additional_params?.dify_dataset_id || ''
  editForm.notion_token = ''
  editForm.notion_data_source_id = database.value.additional_params?.notion_data_source_id || ''
  editForm.notion_version = database.value.additional_params?.notion_version || '2026-03-11'
  editModalVisible.value = true
}

const handleEmbeddingModelChange = (spec) => {
  if (!spec || spec === editForm.embedding_model_spec) return
  Modal.confirm({
    title: t('dbInfo.switchEmbeddingModel'),
    content: t('dbInfo.switchEmbeddingModelContent'),
    okText: t('dbInfo.confirmSwitch'),
    cancelText: t('common.cancel'),
    onOk: () => {
      editForm.embedding_model_spec = spec
    }
  })
}

watch(
  () => [route.query.action, detailLoading.value, isCurrentDatabaseLoaded.value],
  ([action, loading, loaded]) => {
    if (action !== 'edit' || loading || !loaded) return
    showEditModal()
    router.replace({ path: route.path, query: { ...route.query, action: undefined } })
  },
  { immediate: true }
)

const handleEditSubmit = () => {
  editFormRef.value
    .validate()
    .then(async () => {
      if (shareConfigFormRef.value) {
        const validation = shareConfigFormRef.value.validate()
        if (!validation.valid) {
          message.warning(validation.message)
          return
        }
      }

      const formConfig = shareConfigFormRef.value?.config || { access_level: 'global' }
      const updateData = {
        name: editForm.name,
        description: editForm.description,
        category_id: editForm.category_id,
        additional_params: {},
        share_config: {
          access_level: formConfig.access_level,
          department_ids:
            formConfig.access_level === 'department' ? formConfig.department_ids || [] : [],
          user_uids: formConfig.access_level === 'user' ? formConfig.user_uids || [] : []
        }
      }

      if (isDifyKb.value) {
        if (
          !editForm.dify_api_url?.trim() ||
          !editForm.dify_token?.trim() ||
          !editForm.dify_dataset_id?.trim()
        ) {
          message.error(t('dbInfo.difyFieldsRequired'))
          return
        }
        if (!editForm.dify_api_url.trim().endsWith('/v1')) {
          message.error(t('dbInfo.difyUrlMustEndV1'))
          return
        }
        updateData.additional_params = {
          dify_api_url: editForm.dify_api_url.trim(),
          dify_token: editForm.dify_token.trim(),
          dify_dataset_id: editForm.dify_dataset_id.trim()
        }
      } else if (isNotionKb.value) {
        if (!editForm.notion_data_source_id?.trim()) {
          message.error(t('dbInfo.notionDataSourceRequired'))
          return
        }
        updateData.additional_params = {
          notion_data_source_id: editForm.notion_data_source_id.trim(),
          notion_version: editForm.notion_version?.trim() || '2026-03-11'
        }
        if (editForm.notion_token?.trim()) {
          updateData.additional_params.notion_token = editForm.notion_token.trim()
        }
      } else {
        updateData.additional_params = {
          auto_generate_questions: editForm.auto_generate_questions,
          chunk_preset_id: editForm.chunk_preset_id || DEFAULT_CHUNK_PRESET_ID
        }
        if (editForm.embedding_model_spec) {
          updateData.embedding_model_spec = editForm.embedding_model_spec
        }
      }

      await store.updateDatabaseInfo(updateData)
      editModalVisible.value = false
    })
    .catch((err) => {
      console.error('表单验证失败:', err)
    })
}

const deleteDatabase = () => {
  store.deleteDatabase()
}

onMounted(() => {
  loadChunkPresetOptions()
  loadCategories()
  loadDepartments()
  loadUsers()
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';
@import '@/assets/css/extension-detail.less';

.graph-section-loading {
  padding: 48px 0;
  text-align: center;
  color: var(--text-color-secondary, #8c8c8c);
}

.database-info-container {
  .detail-content-wrapper {
    flex: 1;
    min-height: 0;
  }

  .detail-subtitle {
    display: block;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.database-detail-body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: var(--gray-10);
  overflow: hidden;
}

.database-detail-loading {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--gray-10);
}

.database-tab-bar {
  flex-shrink: 0;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
  padding: 8px 12px 0;
  overflow-x: auto;
  overflow-y: hidden;
}

.database-tab-list {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: max-content;
}

.database-tab-item {
  position: relative;
  min-height: 40px;
  border: none;
  border-radius: 8px 8px 0 0;
  background: transparent;
  color: var(--gray-600);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 0 14px 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition:
    background 0.15s,
    color 0.15s;

  svg {
    flex-shrink: 0;
  }

  &:hover {
    color: var(--gray-900);
    background: var(--gray-50);
  }

  &:focus-visible {
    outline: 2px solid var(--main-200);
    outline-offset: 2px;
  }

  &.active {
    color: var(--main-color);
    background: var(--main-20);

    &::before {
      content: '';
      position: absolute;
      left: 12px;
      right: 12px;
      bottom: 0;
      height: 3px;
      border-radius: 3px 3px 0 0;
      background: var(--main-color);
    }
  }
}

.database-tab-content {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.tab-panel {
  flex: 1;
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 16px var(--page-padding);
}

.file-panel {
  gap: 8px;
}

.query-config-panel {
  overflow: hidden;
}

.query-config-layout {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 12px;
}

.query-test-pane {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
}

.query-test-pane :deep(.query-section) {
  flex: 1;
  min-width: 0;
}

.query-config-pane {
  width: 360px;
  flex: 0 0 360px;
  min-height: 0;
  display: flex;
}

.query-config-pane .search-config-wrapper {
  width: 100%;
}

.query-config-pane :deep(.ant-row) {
  margin-right: 0 !important;
  margin-left: 0 !important;
}

.query-config-pane :deep(.ant-col) {
  max-width: 100%;
  flex: 0 0 100%;
  padding-right: 0 !important;
  padding-left: 0 !important;
}

.file-panel-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-shrink: 0;
  padding: 10px 12px;
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.file-panel-summary {
  display: flex;
  align-items: baseline;
  min-width: 0;
  gap: 8px;
}

.file-panel-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--gray-900);
  white-space: nowrap;
}

.file-panel-count {
  font-size: 12px;
  color: var(--gray-500);
  white-space: nowrap;
}

.file-management-info {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  flex-shrink: 0;
}

.file-info-title {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 180px;
}

.file-info-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.file-panel-desc {
  font-size: 12px;
  color: var(--gray-500);
}

.file-panel-status {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}

.file-stat-card {
  min-width: 60px;
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--main-0);
  border: 1px solid var(--gray-100);
  color: var(--main-color);
  font: inherit;
  appearance: none;
  text-align: left;

  div {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  strong {
    font-size: 14px;
    line-height: 1.2;
    color: var(--gray-900);
    white-space: nowrap;
  }

  span {
    font-size: 11px;
    color: var(--gray-500);
    white-space: nowrap;
  }
}

.file-stat-summary {
  min-width: 87px;
  min-height: 36px;
  gap: 8px;
  padding: 5px 10px;

  .file-stat-inline {
    flex-direction: row;
    align-items: baseline;
    gap: 4px;
  }
}

.file-stat-action {
  cursor: pointer;
  color: var(--color-warning-500);
  border: 1px solid var(--color-warning-100);
  background-color: var(--color-warning-50);
  transition:
    background 0.15s,
    border-color 0.15s;

  &:hover {
    border-color: var(--color-warning-700);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.6;
  }
}

.file-stat-repair {
  cursor: pointer;
  transition:
    background 0.15s,
    border-color 0.15s;

  &:hover:not(:disabled) {
    border-color: var(--main-300);
    background-color: var(--main-30);
  }

  &:disabled {
    cursor: wait;
    opacity: 0.72;
  }
}

.file-stat-spinner {
  animation: file-stat-spin 0.8s linear infinite;
}

@keyframes file-stat-spin {
  to {
    transform: rotate(360deg);
  }
}

.search-config-wrapper {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--gray-0);
  overflow: hidden;
}

.search-config-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--gray-150);
  flex-shrink: 0;

  h3 {
    margin: 0 0 4px;
    font-size: 16px;
    font-weight: 600;
    color: var(--gray-900);
  }

  p {
    margin: 0;
    font-size: 13px;
    color: var(--gray-500);
  }
}

.search-config-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
}

.share-config-readonly {
  display: flex;
  align-items: center;
  gap: 8px;

  .access-names {
    font-size: 13px;
    color: var(--gray-600);
  }
}

.chunk-preset-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.chunk-preset-help-icon {
  color: var(--gray-500);
  cursor: help;
  font-size: 14px;
}

@media (max-width: 1024px) {
  .query-config-layout {
    flex-direction: column;
    overflow-y: auto;
  }

  .query-test-pane {
    min-height: 360px;
  }

  .query-config-pane {
    width: 100%;
    flex: 0 0 auto;
    min-height: 320px;
  }
}

@media (max-width: 767px) {
  .detail-top-bar {
    gap: 10px;
  }

  .detail-actions :deep(.extension-panel-action span) {
    display: none;
  }

  .database-tab-bar {
    padding: 8px 8px 0;
  }

  .database-tab-item {
    min-width: 104px;
  }

  .query-config-layout {
    flex-direction: column;
  }

  .query-config-pane {
    width: 100%;
    flex: 0 0 auto;
    min-height: 320px;
  }

  .file-panel-toolbar {
    align-items: flex-start;
    flex-direction: column;
  }
}
</style>

<style lang="less">
@media (max-width: 767px) {
  .app-layout:has(.database-info-container) {
    min-width: 0;
  }
}

/* 全局样式作为备用方案 */
.ant-popover .query-params-compact {
  width: 220px;
}

.ant-popover .query-params-compact .params-loading {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 80px;
}

.ant-popover .query-params-compact .params-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}

.ant-popover .query-params-compact .param-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
}

.ant-popover .query-params-compact .param-item label {
  font-weight: 500;
  color: var(--gray-700);
  margin-right: 8px;
}

/* Improve panel transitions */
.panel-section {
  display: flex;
  flex-direction: column;
  border-radius: 4px;
  transition: all 0.3s;
  min-height: 0;

  &.collapsed {
    height: 36px;
    flex: none;
  }

  .section-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid var(--gray-150);
    background-color: var(--gray-25);

    .header-left {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .section-title {
      font-size: 14px;
      font-weight: 500;
      color: var(--gray-700);
      margin: 0;
    }

    .panel-actions {
      display: flex;
      gap: 0px;
    }
  }

  .content {
    flex: 1;
    min-height: 0;
  }
}

.query-section,
.graph-section {
  .panel-section();

  .content {
    padding: 8px;
    flex: 1;
    overflow: hidden;
  }
}

.graph-section {
  border: 1px solid var(--gray-100);
  border-radius: 12px;
}

.benchmark-management-container {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.benchmark-content {
  flex: 1;
  overflow: hidden;
  min-height: 0;
}
</style>
