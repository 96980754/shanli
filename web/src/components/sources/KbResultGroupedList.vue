<template>
  <div class="kb-result-grouped-list">
    <div v-if="showSummary" class="result-summary">
      找到 {{ normalizedChunks.length }} 个相关文档片段，来自 {{ fileGroupList.length }} 个文件
    </div>

    <div class="kb-results" v-if="normalizedChunks.length > 0">
      <div v-for="fileGroup in fileGroupList" :key="getFileKey(fileGroup)" class="file-group">
        <div
          class="file-header"
          :class="{ expanded: expandedFiles.has(getFileKey(fileGroup)) }"
          @click="toggleFile(fileGroup)"
        >
          <div class="file-info">
            <ChevronRight
              v-if="!expandedFiles.has(getFileKey(fileGroup))"
              :size="14"
              class="expand-icon"
            />
            <ChevronDown v-else :size="14" class="expand-icon" />
            <FileText :size="14" color="var(--gray-600)" />
            <span class="file-name">{{ fileGroup.displayName }}</span>
            <span v-if="getSourceVersion(fileGroup)?.document_version" class="current-version">
              V{{ getSourceVersion(fileGroup).document_version }} 当前版本
            </span>
            <span class="chunk-count">{{ fileGroup.chunks.length }} chunks</span>
          </div>
          <div v-if="fileGroup.kb_id && fileGroup.file_id" class="file-actions">
            <button
              class="file-action-btn"
              @click.stop="openFileDetail(fileGroup)"
              title="查看文件"
            >
              <Eye :size="14" />
              <span>查看</span>
            </button>
            <button
              class="file-action-btn download-btn"
              :disabled="downloadingFileKey === getFileKey(fileGroup)"
              @click.stop="downloadOriginal(fileGroup)"
              title="下载原文"
            >
              <LoaderCircle
                v-if="downloadingFileKey === getFileKey(fileGroup)"
                :size="14"
                class="loading-icon"
              />
              <Download v-else :size="14" />
              <span>下载原文</span>
            </button>
          </div>
        </div>

        <div v-if="getHistoryVersions(fileGroup).length" class="history-versions" @click.stop>
          <button class="history-toggle" type="button" @click="toggleHistory(fileGroup)">
            <History :size="13" />
            <span>历史版本（{{ getHistoryVersions(fileGroup).length }}）</span>
            <ChevronDown
              :size="13"
              class="history-expand-icon"
              :class="{ rotated: expandedHistories.has(getFileKey(fileGroup)) }"
            />
          </button>
          <div v-if="expandedHistories.has(getFileKey(fileGroup))" class="history-list">
            <div
              v-for="version in getHistoryVersions(fileGroup)"
              :key="version.file_id"
              class="history-item"
            >
              <div class="history-info">
                <strong>V{{ version.document_version }}</strong>
                <span class="history-label">历史版本</span>
                <span>{{ formatVersionTime(version.updated_at) }}</span>
              </div>
              <button
                class="file-action-btn"
                type="button"
                :disabled="downloadingFileKey === getVersionFileKey(fileGroup, version)"
                @click="downloadHistoryVersion(fileGroup, version)"
              >
                <LoaderCircle
                  v-if="downloadingFileKey === getVersionFileKey(fileGroup, version)"
                  :size="13"
                  class="loading-icon"
                />
                <Download v-else :size="13" />
                <span>下载</span>
              </button>
            </div>
          </div>
        </div>

        <div v-if="expandedFiles.has(getFileKey(fileGroup))" class="chunks-container">
          <div
            v-for="(chunk, index) in fileGroup.chunks"
            :key="getChunkKey(chunk, index)"
            class="chunk-item"
            :class="{ 'high-relevance': typeof chunk.score === 'number' && chunk.score > 0.5 }"
            @click="openChunkDetail(chunk, index + 1)"
          >
            <div class="chunk-summary">
              <span class="chunk-index">#{{ index + 1 }}</span>
              <div class="chunk-scores">
                <span v-if="typeof chunk.score === 'number'" class="score-item"
                  >相似度 {{ (chunk.score * 100).toFixed(0) }}%</span
                >
                <span v-if="typeof chunk.rerank_score === 'number'" class="score-item"
                  >重排序 {{ (chunk.rerank_score * 100).toFixed(0) }}%</span
                >
                <span v-if="getLineRange(chunk)" class="score-item">{{ getLineRange(chunk) }}</span>
              </div>
              <span class="chunk-preview">{{ getPreviewText(chunk.content) }}</span>
              <Eye :size="14" class="view-icon" />
            </div>
          </div>
        </div>
      </div>
      <div v-if="sourceVersionsLoading" class="source-versions-status">正在加载版本信息...</div>
      <button
        v-else-if="sourceVersionsError"
        type="button"
        class="source-versions-status source-versions-retry"
        @click="loadSourceVersions(fileGroupList)"
      >
        {{ sourceVersionsError }}，点击重试
      </button>
    </div>

    <div v-else class="no-results">
      <p>{{ emptyText }}</p>
    </div>

    <KbChunkDetailModal
      v-model:open="modalVisible"
      :chunk="selectedChunk"
      :title-prefix="`文档片段 #${selectedChunkIndex || '-'} `"
    />

    <FileDetailModal
      v-model:open="fileDetailOpen"
      :kb-id="fileDetailKbId"
      :file-id="fileDetailFileId"
    />
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import {
  FileText,
  ChevronRight,
  ChevronDown,
  Download,
  Eye,
  LoaderCircle,
  History
} from 'lucide-vue-next'
import { documentApi } from '@/apis/knowledge_api'
import {
  buildVersionedFilename,
  groupSourceFileIdsByKnowledgeBase,
  normalizeSourceVersions
} from '@/utils/knowledgeSourceVersions'
import { MessageProcessor } from '@/utils/messageProcessor'
import KbChunkDetailModal from './KbChunkDetailModal.vue'
import FileDetailModal from '@/components/FileDetailModal.vue'

const props = defineProps({
  chunks: {
    type: [Array, Object],
    default: () => []
  },
  showSummary: {
    type: Boolean,
    default: true
  },
  emptyText: {
    type: String,
    default: '未找到相关知识库内容'
  }
})

const expandedFiles = ref(new Set())
const expandedHistories = ref(new Set())
const modalVisible = ref(false)
const selectedChunk = ref(null)
const selectedChunkIndex = ref(null)
const fileDetailOpen = ref(false)
const fileDetailKbId = ref('')
const fileDetailFileId = ref('')
const downloadingFileKey = ref('')
const sourceVersions = ref(new Map())
const sourceVersionsLoading = ref(false)
const sourceVersionsError = ref('')
let sourceVersionLoadId = 0

const resolveChunks = (input) => {
  if (Array.isArray(input)) return input
  if (!input || typeof input !== 'object') return []

  if (Array.isArray(input.chunks)) return input.chunks
  if (Array.isArray(input.data?.chunks)) return input.data.chunks

  return []
}

const normalizedChunks = computed(() =>
  resolveChunks(props.chunks)
    .filter((item) => item && typeof item === 'object' && item.content)
    .map((item) => {
      const metadata = item.metadata && typeof item.metadata === 'object' ? item.metadata : {}
      const source =
        metadata.source ||
        metadata.file_name ||
        metadata.filename ||
        metadata.title ||
        item.file_name ||
        item.filename ||
        item.file_id ||
        item.kb_id ||
        '未知来源'

      return {
        ...item,
        score: typeof item.score === 'number' ? item.score : metadata.score,
        metadata: {
          ...metadata,
          source,
          chunk_id: metadata.chunk_id || item.id
        }
      }
    })
)

// 来源展示只取文件名（去掉前缀路径），分组按文档名去重合并：
// 同一文档在多个知识库命中（source 路径不同但文件名一致）时归为一组，避免来源面板出现重复文档卡片
const fileGroupList = computed(() =>
  MessageProcessor.groupKnowledgeChunksByDocument(normalizedChunks.value)
)

const toggleFile = (fileGroup) => {
  const fileKey = getFileKey(fileGroup)
  if (expandedFiles.value.has(fileKey)) {
    expandedFiles.value.delete(fileKey)
  } else {
    expandedFiles.value.add(fileKey)
  }
}

const toggleHistory = (fileGroup) => {
  const fileKey = getFileKey(fileGroup)
  if (expandedHistories.value.has(fileKey)) {
    expandedHistories.value.delete(fileKey)
  } else {
    expandedHistories.value.add(fileKey)
  }
}

const getChunkKey = (chunk, index) => {
  if (chunk?.metadata?.chunk_id) return `${chunk.metadata.chunk_id}-${index}`
  return `${chunk?.metadata?.source || 'chunk'}-${index}`
}

const getPreviewText = (text = '') => {
  const content = String(text)
  return content.length <= 100 ? content : `${content.substring(0, 100)}...`
}

const getLineRange = (chunk) => {
  const startLine = Number(chunk?.metadata?.start_line || 0)
  const endLine = Number(chunk?.metadata?.end_line || 0)
  if (!startLine || !endLine) return ''
  return startLine === endLine ? `第 ${startLine} 行` : `第 ${startLine}-${endLine} 行`
}

const openChunkDetail = (chunk, index) => {
  selectedChunk.value = chunk
  selectedChunkIndex.value = index
  modalVisible.value = true
}

const openFileDetail = (fileGroup) => {
  fileDetailKbId.value = fileGroup.kb_id || ''
  fileDetailFileId.value = fileGroup.file_id || ''
  fileDetailOpen.value = Boolean(fileDetailKbId.value && fileDetailFileId.value)
}

const getFileKey = (fileGroup) => `${fileGroup.kb_id}::${fileGroup.file_id || fileGroup.filename}`
const getVersionFileKey = (fileGroup, version) => `${fileGroup.kb_id}::${version.file_id}`
const getSourceVersion = (fileGroup) => sourceVersions.value.get(getFileKey(fileGroup))
const getHistoryVersions = (fileGroup) => getSourceVersion(fileGroup)?.history_versions || []
const formatVersionTime = (value) => (value ? new Date(value).toLocaleString() : '-')

const loadSourceVersions = async (groups) => {
  const requests = groupSourceFileIdsByKnowledgeBase(groups)
  const loadId = ++sourceVersionLoadId
  sourceVersions.value = new Map()
  sourceVersionsError.value = ''
  if (!requests.length) {
    sourceVersionsLoading.value = false
    return
  }

  sourceVersionsLoading.value = true
  const settled = await Promise.allSettled(
    requests.map(async ({ kbId, fileIds }) => ({
      kbId,
      ...(await documentApi.getSourceVersions(kbId, fileIds))
    }))
  )
  if (loadId !== sourceVersionLoadId) return

  const responses = settled.filter((item) => item.status === 'fulfilled').map((item) => item.value)
  sourceVersions.value = normalizeSourceVersions(responses)
  if (settled.some((item) => item.status === 'rejected')) {
    sourceVersionsError.value = responses.length ? '部分版本信息加载失败' : '版本信息加载失败'
  }
  sourceVersionsLoading.value = false
}

watch(
  fileGroupList,
  (groups) => {
    // 分组变化时仅清理失效展开项，默认保持折叠状态。
    const validFileKeys = new Set(groups.map((item) => getFileKey(item)))
    expandedFiles.value = new Set(
      [...expandedFiles.value].filter((fileKey) => validFileKeys.has(fileKey))
    )
    expandedHistories.value = new Set(
      [...expandedHistories.value].filter((fileKey) => validFileKeys.has(fileKey))
    )
    loadSourceVersions(groups)
  },
  { immediate: true }
)

const getDownloadFilename = (response, fallback) => {
  const contentDisposition = response.headers.get('content-disposition')
  if (!contentDisposition) return fallback

  const encodedMatch = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i)
  if (encodedMatch) {
    try {
      return decodeURIComponent(encodedMatch[1])
    } catch {
      return fallback
    }
  }

  const filenameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/i)
  return filenameMatch?.[1]?.replace(/['"]/g, '') || fallback
}

const downloadOriginal = async (fileGroup) => {
  const fileKey = getFileKey(fileGroup)
  downloadingFileKey.value = fileKey
  try {
    const response = await documentApi.downloadDocument(fileGroup.kb_id, fileGroup.file_id)
    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = getDownloadFilename(response, fileGroup.displayName || 'document')
    link.style.display = 'none'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
    message.success('原文下载成功')
  } catch (error) {
    console.error('下载知识库原文失败:', error)
    message.error(error?.message || '原文下载失败')
  } finally {
    downloadingFileKey.value = ''
  }
}

const downloadHistoryVersion = async (fileGroup, version) => {
  const fileKey = getVersionFileKey(fileGroup, version)
  downloadingFileKey.value = fileKey
  try {
    const response = await documentApi.downloadDocument(fileGroup.kb_id, version.file_id)
    const blob = await response.blob()
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    const originalFilename = getDownloadFilename(
      response,
      version.filename || fileGroup.filename || 'document'
    )
    link.download = buildVersionedFilename(originalFilename, version.document_version)
    link.style.display = 'none'
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
    message.success(`历史版本 V${version.document_version} 下载成功`)
  } catch (error) {
    console.error('下载知识库历史版本失败:', error)
    message.error(error?.message || '历史版本下载失败')
  } finally {
    downloadingFileKey.value = ''
  }
}
</script>

<style scoped lang="less">
.kb-result-grouped-list {
  padding: 4px;
  .result-summary {
    padding: 6px 10px;
    background: var(--gray-25);
    font-size: 12px;
    color: var(--gray-700);
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    margin-bottom: 6px;
  }

  .kb-results {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .file-group {
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
    overflow: hidden;

    .file-header {
      padding: 5px 10px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      cursor: pointer;
      background: var(--gray-10);

      &:hover {
        background: var(--gray-25);
      }

      &.expanded {
        background: var(--gray-25);
        border-bottom: 1px solid var(--gray-100);
      }

      .file-info {
        display: flex;
        align-items: center;
        gap: 8px;
        flex: 1;
        min-width: 0;

        .expand-icon {
          flex-shrink: 0;
          color: var(--gray-500);
        }

        .file-name {
          font-size: 13px;
          color: var(--gray-700);
          font-weight: 400;
          flex: 1;
          min-width: 0;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .current-version {
          flex-shrink: 0;
          padding: 1px 6px;
          border-radius: 10px;
          background: var(--color-success-50);
          color: var(--color-success-700);
          font-size: 11px;
          white-space: nowrap;
        }

        .chunk-count {
          font-size: 11px;
          color: var(--gray-700);
          white-space: nowrap;
        }
      }

      .file-actions {
        flex-shrink: 0;
        display: flex;
        align-items: center;
        gap: 4px;
      }

      .file-action-btn {
        display: flex;
        align-items: center;
        gap: 4px;
        height: 26px;
        padding: 0 8px;
        border: 1px solid var(--gray-150);
        background: var(--gray-0);
        border-radius: 5px;
        cursor: pointer;
        color: var(--gray-700);
        font-size: 12px;
        white-space: nowrap;
        transition: all 0.15s;

        &:hover:not(:disabled) {
          border-color: var(--gray-300);
          background: var(--gray-25);
          color: var(--gray-900);
        }

        &:disabled {
          cursor: not-allowed;
          opacity: 0.6;
        }

        &.download-btn {
          color: var(--color-primary-700);
          border-color: var(--color-primary-100);
          background: var(--color-primary-50);

          &:hover:not(:disabled) {
            color: var(--color-primary-900);
            border-color: var(--color-primary-500);
            background: var(--color-primary-100);
          }
        }

        .loading-icon {
          animation: spin 1s linear infinite;
        }
      }
    }

    .history-versions {
      padding: 5px 10px;
      border-bottom: 1px solid var(--gray-100);
      background: var(--gray-5);

      .history-toggle {
        display: flex;
        align-items: center;
        gap: 5px;
        width: 100%;
        padding: 2px 0;
        border: none;
        background: transparent;
        color: var(--gray-700);
        cursor: pointer;
        font-size: 12px;
        text-align: left;
      }

      .history-expand-icon {
        margin-left: auto;
        transition: transform 0.2s;

        &.rotated {
          transform: rotate(180deg);
        }
      }

      .history-list {
        display: flex;
        flex-direction: column;
        gap: 5px;
        margin-top: 6px;
      }

      .history-item {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        padding: 5px 8px;
        border: 1px solid var(--gray-100);
        border-radius: 6px;
        background: var(--gray-0);
      }

      .history-info {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
        color: var(--gray-600);
        font-size: 11px;

        strong {
          color: var(--gray-800);
        }
      }

      .history-label {
        padding: 1px 5px;
        border-radius: 8px;
        background: var(--gray-100);
        color: var(--gray-700);
      }

      .file-action-btn {
        display: flex;
        flex-shrink: 0;
        align-items: center;
        gap: 4px;
        height: 24px;
        padding: 0 7px;
        border: 1px solid var(--gray-150);
        border-radius: 5px;
        background: var(--gray-0);
        color: var(--gray-700);
        cursor: pointer;
        font-size: 11px;

        &:hover:not(:disabled) {
          border-color: var(--main-300);
          color: var(--main-color);
        }

        &:disabled {
          cursor: not-allowed;
          opacity: 0.6;
        }

        .loading-icon {
          animation: spin 1s linear infinite;
        }
      }
    }

    .chunk-item {
      padding: 6px 10px;
      border-bottom: 1px solid var(--gray-100);
      cursor: pointer;

      &:last-child {
        border-bottom: none;
      }

      &.high-relevance {
        background: var(--gray-5);
      }

      &:hover {
        background: var(--gray-25);
      }

      .chunk-summary {
        display: flex;
        align-items: center;
        gap: 8px;

        .chunk-index {
          color: var(--gray-700);
          font-size: 11px;
          min-width: 22px;
          text-align: center;
          background: var(--gray-25);
          border-radius: 4px;
          padding: 1px 4px;
        }

        .chunk-scores {
          display: flex;
          gap: 6px;

          .score-item {
            font-size: 11px;
            color: var(--gray-700);
            background: var(--gray-25);
            border: 1px solid var(--gray-100);
            border-radius: 4px;
            padding: 1px 5px;
            white-space: nowrap;
          }
        }

        .chunk-preview {
          flex: 1;
          min-width: 0;
          font-size: 12px;
          color: var(--gray-700);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .view-icon {
          color: var(--gray-700);
          opacity: 0.5;
        }
      }
    }
  }

  .no-results {
    text-align: center;
    color: var(--gray-700);
    padding: 10px;
    font-size: 12px;
    border: 1px dashed var(--gray-200);
    border-radius: 8px;
  }

  .source-versions-status {
    padding: 3px 6px;
    border: none;
    background: transparent;
    color: var(--gray-500);
    font-size: 11px;
    text-align: left;
  }

  .source-versions-retry {
    cursor: pointer;

    &:hover {
      color: var(--main-color);
    }
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
