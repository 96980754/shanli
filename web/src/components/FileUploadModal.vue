<template>
  <DocumentSearchModal
    v-model:open="documentSearchVisible"
    :kb-id="kbId"
    :selected-file-id="activeVersionCandidate?.currentFileId"
    @select="applyDocumentSelection"
  />
  <a-modal v-model:open="visible" title="添加文件" width="800px" @cancel="handleCancel">
    <template #footer>
      <div class="footer-container">
        <a-button type="link" class="help-link-btn" @click="openDocLink">
          <CircleHelp :size="14" /> 文档处理说明
        </a-button>
        <div class="footer-buttons">
          <a-button key="back" @click="handleCancel">取消</a-button>
          <a-button
            key="submit"
            type="primary"
            @click="chunkData"
            :loading="chunkLoading"
            :disabled="!canSubmit"
          >
            添加到知识库
          </a-button>
        </div>
      </div>
    </template>

    <div class="add-files-content">
      <!-- 1. 顶部操作栏 -->
      <div class="top-action-bar">
        <div class="mode-switch">
          <a-segmented
            v-model:value="uploadMode"
            :options="uploadModeOptions"
            class="custom-segmented"
          />
        </div>
        <div v-if="!props.deferProcessing" class="auto-index-toggle">
          <a-checkbox v-model:checked="autoIndex">上传后自动入库</a-checkbox>
          <a-checkbox v-if="!HIDE_CLEAN" v-model:checked="enableClean" class="clean-toggle">AI 清洗排版</a-checkbox>
        </div>
      </div>

      <!-- 2. 配置面板 -->
      <div
        class="settings-panel"
        v-if="folderTreeData.length > 0 || uploadMode !== 'url' || autoIndex"
      >
        <!-- 第一行：存储位置 + OCR 引擎 -->
        <div
          class="setting-row"
          v-if="folderTreeData.length > 0 || uploadMode !== 'url'"
          :class="{ 'two-cols': uploadMode !== 'url' && folderTreeData.length > 0 }"
        >
          <div class="col-item" v-if="folderTreeData.length > 0">
            <div class="setting-label">存储位置</div>
            <div class="setting-content flex-row">
              <a-tree-select
                v-model:value="selectedFolderId"
                show-search
                class="folder-select"
                :dropdown-style="{ maxHeight: '400px', overflow: 'auto' }"
                placeholder="选择目标文件夹（默认为根目录）"
                allow-clear
                tree-default-expand-all
                :tree-data="folderTreeData"
                tree-node-filter-prop="title"
              >
              </a-tree-select>
            </div>
            <p class="param-description">选择文件保存的目标文件夹</p>
          </div>
          <div class="col-item" v-if="uploadMode !== 'url'">
            <div class="setting-label">
              OCR 引擎（仅应用于 PDF/图片文件）
              <a-tooltip title="检查服务状态">
                <ReloadOutlined
                  class="action-icon refresh-icon"
                  :class="{ spinning: ocrHealthChecking }"
                  @click="checkOcrHealth"
                />
              </a-tooltip>
            </div>
            <div class="setting-content">
              <a-popover
                v-model:open="ocrPanelOpen"
                placement="bottomLeft"
                trigger="click"
                overlayClassName="ocr-engine-popover"
                @openChange="handleOcrPanelOpenChange"
              >
                <template #content>
                  <div class="ocr-engine-panel">
                    <button
                      v-for="option in availableOcrOptions"
                      :key="option.value"
                      type="button"
                      class="ocr-engine-option"
                      :class="{ selected: processingParams.ocr_engine === option.value }"
                      :disabled="chunkLoading"
                      @click="selectOcrEngine(option.value)"
                    >
                      <span class="ocr-engine-option-header">
                        <span class="ocr-engine-name">{{ option.label }}</span>
                        <span
                          class="ocr-engine-status"
                          :class="`status-${getOcrStatus(option.value)}`"
                        >
                          {{ getOcrStatusLabel(option.value) }}
                        </span>
                      </span>
                      <span class="ocr-engine-desc">{{ getOcrDescription(option.value) }}</span>
                    </button>

                    <div v-if="unavailableOcrOptions.length" class="unavailable-ocr-options">
                      <button
                        type="button"
                        class="unavailable-toggle"
                        @click="toggleUnavailableOcrOptions"
                      >
                        <span>不可用选项（{{ unavailableOcrOptions.length }}）</span>
                        <ChevronUp v-if="unavailableOcrExpanded" :size="14" />
                        <ChevronDown v-else :size="14" />
                      </button>

                      <div v-if="unavailableOcrExpanded" class="unavailable-ocr-list">
                        <button
                          v-for="option in unavailableOcrOptions"
                          :key="option.value"
                          type="button"
                          class="ocr-engine-option disabled"
                          disabled
                        >
                          <span class="ocr-engine-option-header">
                            <span class="ocr-engine-name">{{ option.label }}</span>
                            <span
                              class="ocr-engine-status"
                              :class="`status-${getOcrStatus(option.value)}`"
                            >
                              {{ getOcrStatusLabel(option.value) }}
                            </span>
                          </span>
                          <span class="ocr-engine-desc">{{ getOcrDescription(option.value) }}</span>
                        </button>
                      </div>
                    </div>
                  </div>
                </template>

                <a-button class="ocr-engine-trigger" block>
                  <span class="ocr-engine-trigger-main">
                    <ReloadOutlined v-if="ocrHealthChecking" class="ocr-engine-trigger-loading" />
                    <span class="ocr-engine-trigger-label">{{ selectedOcrEngineLabel }}</span>
                  </span>
                  <ChevronDown :size="14" />
                </a-button>
              </a-popover>
            </div>
          </div>
        </div>

        <!-- 第二行：自动入库配置 (仅在开启时显示) -->
        <div class="setting-row" v-if="autoIndex">
          <div class="col-item">
            <div class="setting-label">入库参数配置</div>
            <div class="setting-content">
              <ChunkParamsConfig
                :temp-chunk-params="indexParams"
                :show-qa-split="true"
                :show-chunk-size-overlap="true"
                :show-preset="true"
                :allow-preset-follow-default="true"
                :database-preset-id="
                  store.database?.additional_params?.chunk_preset_id || 'general'
                "
              />
            </div>
          </div>
        </div>
      </div>

      <!-- PDF/图片OCR提醒 (Alert样式优化) -->
      <div v-if="hasPdfOrImageFiles && !isOcrEnabled" class="inline-alert warning">
        <Info :size="16" />
        <span>检测到PDF或图片文件，建议启用 OCR 以提取文本内容</span>
      </div>

      <!-- 文件上传区域 -->
      <div class="upload-area" v-if="uploadMode === 'file' || uploadMode === 'folder'">
        <a-upload-dragger
          class="custom-dragger"
          v-model:fileList="fileList"
          name="file"
          :multiple="true"
          :directory="isFolderUpload"
          :disabled="chunkLoading"
          :show-upload-list="!showAggregateProgress"
          :accept="acceptedFileTypes"
          :before-upload="beforeUpload"
          :customRequest="customRequest"
          :action="'/api/knowledge/files/upload?kb_id=' + kbId"
          :headers="getAuthHeaders()"
          @change="handleFileUpload"
          @drop="handleDrop"
          @preview="handlePreviewUploaded"
        >
          <p class="ant-upload-text">点击或将文件拖拽到此处</p>
          <p class="ant-upload-hint">支持类型: {{ uploadHint }}</p>
          <div class="zip-tip" v-if="hasZipFiles">📦 ZIP包将自动解压提取 Markdown 与图片</div>
          <!-- 上传完成后在文件名旁提供显式的「查看」按钮，用户无需猜测文件名可点击预览。
               originNode 是 VNode，必须经 UploadItemWrap 用 h() 渲染，不能 {{ }} 文本插值（会被序列化成 JSON 文本） -->
          <template #itemRender="{ originNode, file, actions }">
            <UploadItemWrap :origin="originNode">
              <a-button
                v-if="file.status === 'done'"
                type="link"
                size="small"
                class="upload-item-preview-btn"
                @click="actions.preview()"
              >
                <Eye :size="13" />
                查看
              </a-button>
            </UploadItemWrap>
          </template>
        </a-upload-dragger>

        <div v-if="showAggregateProgress" class="upload-progress-card">
          <div class="progress-header">
            <div class="progress-header-left">
              <div class="progress-title">上传进度</div>
              <div class="progress-stats inline-in-header">
                <div class="stat-pill">总计 {{ totalUploadCount }}</div>
                <div class="stat-pill uploading" v-if="uploadingUploadCount > 0">
                  上传中 {{ uploadingUploadCount }}
                </div>
                <div class="stat-pill queued" v-if="queuedUploadCount > 0">
                  排队 {{ queuedUploadCount }}
                </div>
                <div class="stat-pill error" v-if="failedUploadCount > 0">
                  失败 {{ failedUploadCount }}
                </div>
              </div>
            </div>
            <div class="progress-header-right">
              <div class="progress-percent">{{ overallUploadProgress }}%</div>
              <a-button
                type="text"
                size="small"
                class="toggle-progress-btn"
                @click="progressExpanded = !progressExpanded"
              >
                <span>{{ progressExpanded ? '收起' : '展开' }}</span>
                <ChevronUp v-if="progressExpanded" :size="14" />
                <ChevronDown v-else :size="14" />
              </a-button>
            </div>
          </div>

          <div v-if="progressExpanded" class="progress-details">
            <div class="details-list" v-if="failedDetailItems.length > 0">
              <div v-for="item in failedDetailItems" :key="item.uid" class="detail-row">
                <span class="detail-name" :title="item.name">{{ item.name }}</span>
                <span class="detail-error" :title="item.errorText">{{ item.errorText }}</span>
              </div>
            </div>

            <div class="progress-tip" v-else>当前无失败文件。</div>

            <div class="progress-tip" v-if="hasPendingUploads">
              文件夹上传采用队列模式，最多同时上传 {{ MAX_UPLOAD_CONCURRENCY }} 个文件。
            </div>
            <div class="progress-tip" v-else>上传队列已完成，可点击“添加到知识库”继续下一步。</div>
          </div>
        </div>
      </div>

      <!-- AI 清洗排版预览 -->
      <div v-if="!HIDE_CLEAN && enableClean && uploadMode === 'file' && uploadedCleanFiles.length > 0 && !hasPendingUploads" class="clean-preview-panel">
        <div class="clean-preview-header">
          <Sparkles :size="15" />
          <span class="clean-preview-title">AI 清洗排版</span>
          <span class="clean-preview-sub">对上传的文档自动重排版为结构清晰的规范文本</span>
          <a-button size="small" class="clean-regenerate-btn" :loading="cleanAllLoading" @click="runCleanAll">
            <RefreshCw :size="13" />
            全部清洗
          </a-button>
        </div>

        <div v-for="file in uploadedCleanFiles" :key="file.uid" class="clean-file-card">
          <div class="clean-file-head">
            <span class="clean-file-name" :title="file.name || file.response?.filename">
              {{ file.name || file.response?.filename || '文档' }}
            </span>
            <span class="clean-file-status" :class="getFileCleanState(file)?.status">
              {{ cleanStatusLabel(getFileCleanState(file)?.status) }}
            </span>
            <a-button
              v-if="getFileCleanState(file)?.status === 'idle' || getFileCleanState(file)?.status === 'error'"
              size="small"
              class="clean-file-action"
              @click="runCleanForFile(file)"
            >
              <Sparkles :size="13" />
              清洗
            </a-button>
            <a-button
              v-else
              size="small"
              class="clean-file-action"
              :loading="getFileCleanState(file)?.status === 'loading'"
              @click="runCleanForFile(file)"
            >
              <RefreshCw :size="13" />
              重新生成
            </a-button>
          </div>

          <div v-if="getFileCleanState(file)?.status === 'loading'" class="clean-loading">
            <a-spin size="small" />
            <span>AI 正在清洗排版...</span>
          </div>

          <div v-else-if="getFileCleanState(file)?.status === 'error'" class="clean-error">
            <span>{{ getFileCleanState(file)?.error }}</span>
          </div>

          <div v-else-if="getFileCleanState(file)?.cleanedMarkdown" class="clean-preview-body">
            <div class="clean-preview-tabs">
              <a-radio-group v-model:value="getFileCleanState(file).viewMode" size="small">
                <a-radio-button value="edit">编辑</a-radio-button>
                <a-radio-button value="preview">预览</a-radio-button>
              </a-radio-group>
            </div>
            <a-textarea
              v-if="getFileCleanState(file)?.viewMode === 'edit'"
              v-model:value="getFileCleanState(file).cleanedMarkdown"
              class="clean-edit-area"
              :rows="12"
            />
            <MarkdownPreview
              v-else
              :content="getFileCleanState(file)?.cleanedMarkdown"
              class="clean-preview-render"
            />
          </div>
        </div>
      </div>

      <!-- Word/Excel 编辑面板（勾选"AI 清洗排版"时 docx 走清洗隐藏，xlsx 仍走编辑显示） -->
      <div
        v-if="(!enableClean || hasXlsxFiles) && uploadedOfficeFiles.length > 0 && !hasPendingUploads"
        class="clean-preview-panel"
      >
        <div class="clean-preview-header">
          <FileText :size="15" />
          <span class="clean-preview-title">Word / Excel 编辑</span>
          <span class="clean-preview-sub">上传后可直接编辑文字/单元格，确认后以原格式入库</span>
        </div>
        <div v-for="file in uploadedOfficeFiles" :key="file.uid" class="clean-file-card">
          <div class="clean-file-head">
            <span class="clean-file-name" :title="file.name">{{ file.name }}</span>
            <span class="clean-file-status" :class="getOfficeEditState(file)?.edited ? 'done' : 'idle'">
              {{ getOfficeEditState(file)?.edited ? '已编辑' : '待编辑' }}
            </span>
            <a-button size="small" class="clean-file-action" @click="openOfficeEdit(file)">
              <Edit3 :size="13" />
              编辑
            </a-button>
          </div>
        </div>
      </div>

      <!-- 工作区文件选择区域 -->
      <div class="workspace-area" v-if="uploadMode === 'workspace'">
        <div class="workspace-toolbar">
          <div class="workspace-summary">
            <FolderOpen :size="16" />
            <span class="workspace-current-path" :title="workspaceCurrentPath">
              {{ workspaceCurrentPath }}
            </span>
            <span
              >已选择
              {{ selectedWorkspacePaths.length }}
              个文件，注意上传会扁平化上传，不保留文件层级结构</span
            >
          </div>
          <div class="workspace-actions">
            <a-button
              size="small"
              class="lucide-icon-btn"
              :disabled="workspaceCurrentPath === '/' || workspaceLoading"
              @click="openWorkspaceParent"
            >
              <ArrowLeft :size="14" />
            </a-button>
            <a-button
              size="small"
              @click="loadWorkspaceFiles()"
              :loading="workspaceLoading"
              class="lucide-icon-btn"
            >
              <RotateCw :size="14" />
            </a-button>
          </div>
        </div>

        <div class="workspace-list" v-if="workspaceItems.length > 0">
          <button
            v-for="item in workspaceDirectoryItems"
            :key="item.path"
            type="button"
            class="workspace-item workspace-directory"
            :disabled="chunkLoading"
            @click="openWorkspaceDirectory(item.path)"
          >
            <a-checkbox disabled />
            <FileTypeIcon is-dir :size="16" class="workspace-file-icon" />
            <span class="workspace-file-name" :title="item.path">{{ item.name }}</span>
          </button>

          <label
            v-for="item in workspaceFileItems"
            :key="item.path"
            class="workspace-item"
            :class="{ disabled: !item.supported }"
          >
            <a-checkbox
              :checked="selectedWorkspacePathSet.has(item.path)"
              :disabled="!item.supported || chunkLoading"
              @change="toggleWorkspacePath(item.path, $event.target.checked)"
            />
            <FileTypeIcon :name="item.path" :size="16" class="workspace-file-icon" />
            <span class="workspace-file-name" :title="item.path">{{ item.path }}</span>
            <span class="workspace-file-size">{{ formatFileSize(item.size) }}</span>
          </label>
        </div>

        <div class="url-empty-tip" v-else>
          <Info :size="16" />
          <span>{{ workspaceLoading ? '正在加载工作区文件' : '当前目录暂无文件' }}</span>
        </div>
      </div>

      <!-- URL 输入区域 -->
      <div class="url-area" v-if="uploadMode === 'url'">
        <div class="url-input-wrapper">
          <a-textarea
            v-model:value="newUrl"
            placeholder="输入 URL，一行一个&#10;https://site1.com&#10;https://site2.com"
            :auto-size="{ minRows: 4, maxRows: 8 }"
            class="url-input"
            @keydown.enter.ctrl="handleFetchUrls"
          />
          <div class="url-actions">
            <span class="url-hint">
              支持批量粘贴，自动过滤空行。
              <span class="warning-text">需配置白名单，详见文档说明</span>
            </span>
            <a-button
              type="primary"
              @click="handleFetchUrls"
              class="add-url-btn"
              :loading="fetchingUrls"
              :disabled="!newUrl.trim()"
            >
              加载 URLs
            </a-button>
          </div>
        </div>
        <div class="url-list" v-if="urlList.length > 0">
          <div v-for="(item, index) in urlList" :key="index" class="url-item">
            <div class="url-icon-wrapper">
              <Link v-if="item.status === 'success'" :size="14" class="url-icon success" />
              <Info
                v-else-if="item.status === 'error'"
                :size="14"
                class="url-icon error"
                :title="item.error"
              />
              <RotateCw v-else :size="14" class="url-icon spinning" />
            </div>
            <div class="url-content">
              <span class="url-text" :title="item.url">{{ item.url }}</span>
              <span v-if="item.status === 'error'" class="url-error-msg">{{ item.error }}</span>
            </div>
            <a-button type="text" size="small" class="remove-url-btn" @click="removeUrl(index)">
              <X :size="14" />
            </a-button>
          </div>
        </div>
        <div class="url-empty-tip" v-else>
          <Info :size="16" />
          <span>输入 URL 后点击加载，系统将自动抓取网页内容</span>
        </div>
      </div>

      <!-- 文件处理方式 -->
      <div v-if="versionCandidates.length > 0 && props.canManage" class="conflict-files-panel">
        <div class="panel-header">
          <Info :size="14" class="icon-warning" />
          <span>请选择上传文件的处理方式</span>
        </div>
        <div class="file-list-scroll">
          <div v-for="item in versionCandidates" :key="item.uid" class="conflict-item version-candidate">
            <div class="version-candidate-main">
              <div class="file-meta">
                <span class="fname" :title="item.filename">{{ item.filename }}</span>
                <span class="ftime">作为新版本时，旧版将保留在版本历史中</span>
              </div>
              <a-radio-group v-model:value="item.action" size="small">
                <a-radio value="add">作为独立文档</a-radio>
                <a-radio value="version">作为新版本</a-radio>
              </a-radio-group>
            </div>
            <div v-if="item.action === 'version'" class="version-target-row">
              <a-select
                v-model:value="item.currentFileId"
                size="small"
                class="version-target-select"
                placeholder="选择同名文档，或搜索其他当前文档"
                @change="syncSameNameSelection(item)"
              >
                <a-select-option v-for="file in item.sameNameFiles" :key="file.file_id" :value="file.file_id">
                  {{ file.filename }} · {{ formatFileTime(file.created_at) }}
                </a-select-option>
                <a-select-option
                  v-if="item.selectedFile && !item.sameNameFiles.some((file) => file.file_id === item.selectedFile.file_id)"
                  :key="item.selectedFile.file_id"
                  :value="item.selectedFile.file_id"
                >
                  {{ item.selectedFile.filename }} · {{ formatFileTime(item.selectedFile.updated_at || item.selectedFile.created_at) }}
                </a-select-option>
              </a-select>
              <a-button size="small" @click="openDocumentSearch(item)">搜索文档</a-button>
            </div>
          </div>
        </div>
      </div>

      <div v-else-if="sameNameFiles.length > 0" class="conflict-files-panel">
        <div class="panel-header">
          <Info :size="14" class="icon-warning" />
          <span>已存在同名文件 ({{ sameNameFiles.length }})</span>
        </div>
        <div class="file-list-scroll">
          <div v-for="file in sameNameFiles" :key="file.file_id" class="conflict-item">
            <div class="file-meta">
              <span class="fname" :title="file.filename">{{ file.filename }}</span>
              <span class="ftime">{{ formatFileTime(file.created_at) }}</span>
            </div>
            <div class="file-actions">
              <a-button
                type="text"
                size="small"
                class="action-btn download"
                @click="downloadSameNameFile(file)"
              >
                <Download :size="14" />
              </a-button>
              <a-button
                type="text"
                size="small"
                danger
                class="action-btn delete"
                @click="deleteSameNameFile(file)"
              >
                <Trash2 :size="14" />
              </a-button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <OfficeEditModal
      v-model:visible="officeEditVisible"
      :kb-id="kbId"
      :file-path="officeEditTarget?.response?.file_path || ''"
      :filename="officeEditTarget?.name || ''"
      @writeback="handleOfficeWriteback"
    />

    <!-- 上传文件预览弹窗：点击文件名触发（上传后、入库前） -->
    <a-modal
      v-model:open="previewVisible"
      :footer="null"
      :width="880"
      :title="previewTarget?.name || ''"
      destroy-on-close
    >
      <AgentFilePreview
        v-if="previewData"
        :file="previewData"
        :file-path="previewTarget?.name || ''"
        :show-close="true"
        :show-fullscreen="true"
        :show-download="false"
        :full-height="true"
        @close="previewVisible = false"
      />
    </a-modal>

    <!-- 重复冲突弹窗（PR12 吸收） -->
    <a-modal
      :open="duplicateConflictOpen"
      :title="duplicateConflictIsExact ? '重复文件' : '同名文件冲突'"
      :closable="false"
      :mask-closable="false"
      :footer="null"
      width="520px"
    >
      <div v-if="duplicateConflictCurrent" class="duplicate-conflict-body">
        <p class="duplicate-conflict-message">
          {{ getDuplicateConflictMessage(duplicateConflictCurrent) }}
        </p>
        <div v-if="duplicateConflictIsExact" class="duplicate-conflict-actions">
          <a-button key="skip" @click="skipDuplicate">知道了，跳过</a-button>
        </div>
        <div v-else class="duplicate-conflict-actions">
          <a-button key="keep-both" @click="keepBothDuplicate">保留两份</a-button>
          <a-button key="replace" type="primary" @click="confirmReplacement">替换现有文件</a-button>
          <a-button key="cancel" @click="cancelDuplicateConflict">取消</a-button>
        </div>
      </div>
    </a-modal>
  </a-modal>
</template>

<script setup>
import { ref, computed, onMounted, watch, h } from 'vue'
import { message, Upload, Modal } from 'ant-design-vue'
import { useUserStore } from '@/stores/user'
import { useConfigStore } from '@/stores/config'
import { useDatabaseStore } from '@/stores/database'
import { ocrApi } from '@/apis/system_api'
import { fileApi, documentApi, databaseApi } from '@/apis/knowledge_api'
import { getWorkspaceTree, getUploadedFilePreview } from '@/apis/workspace_api'
import { normalizePreviewResponse } from '@/utils/file_preview'
import { ReloadOutlined } from '@ant-design/icons-vue'
import {
  FileUp,
  FolderUp,
  FolderOpen,
  ArrowLeft,
  RotateCw,
  CircleHelp,
  Info,
  Download,
  Trash2,
  Link,
  X,
  ChevronDown,
  ChevronUp,
  Sparkles,
  RefreshCw,
  FileText,
  Edit3,
  Eye
} from 'lucide-vue-next'
import { buildChunkParamsPayload } from '@/utils/chunkUtils'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import OfficeEditModal from '@/components/OfficeEditModal.vue'
import AgentFilePreview from '@/components/AgentFilePreview.vue'
import ChunkParamsConfig from '@/components/ChunkParamsConfig.vue'
import FileTypeIcon from '@/components/common/FileTypeIcon.vue'
import DocumentSearchModal from '@/components/DocumentSearchModal.vue'
import {
  buildVersionCandidate,
  findDuplicateVersionTarget,
  pruneVersionCandidates,
  selectVersionTarget
} from '@/components/fileUploadVersionHelpers'
import {
  DUPLICATE_STRATEGIES,
  buildDuplicateResolution,
  buildKnowledgeUploadUrl,
  getDuplicateConflictDetail,
  getDuplicateConflictMessage,
  getSafeUploadErrorMessage
} from '@/utils/document_duplicate_policy'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  folderTree: {
    type: Array,
    default: () => []
  },
  currentFolderId: {
    type: String,
    default: null
  },
  isFolderMode: {
    type: Boolean,
    default: false
  },
  mode: {
    type: String,
    default: 'file'
  },
  canUpload: {
    type: Boolean,
    default: true
  },
  canManage: {
    type: Boolean,
    default: false
  },
  deferProcessing: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['update:visible', 'success'])

const store = useDatabaseStore()
const configStore = useConfigStore()
const DEFAULT_OCR_ENGINE = 'rapid_ocr'

// 文件夹选择相关
const selectedFolderId = ref(null)
const folderTreeData = computed(() => {
  // 转换 folderTree 数据为 TreeSelect 需要的格式
  const transformData = (nodes) => {
    return nodes
      .map((node) => {
        if (!node.is_folder) return null
        return {
          title: node.filename,
          value: node.file_id,
          key: node.file_id,
          children: node.children ? transformData(node.children).filter(Boolean) : []
        }
      })
      .filter(Boolean)
  }
  return transformData(props.folderTree)
})

watch(
  () => props.visible,
  (newVal) => {
    if (newVal) {
      ocrEngineTouched.value = false
      applyDefaultOcrEngine()
      selectedFolderId.value = props.currentFolderId
      isFolderUpload.value = props.isFolderMode
      uploadMode.value = props.mode || (props.isFolderMode ? 'folder' : 'file')
      if (uploadMode.value === 'workspace') {
        loadWorkspaceFiles()
      }
    }
  }
)

const DEFAULT_SUPPORTED_TYPES = ['.txt', '.pdf', '.jpg', '.jpeg', '.md', '.docx']

const normalizeExtensions = (extensions) => {
  if (!Array.isArray(extensions)) {
    return []
  }
  const normalized = extensions
    .map((ext) => (typeof ext === 'string' ? ext.trim().toLowerCase() : ''))
    .filter((ext) => ext.length > 0)
    .map((ext) => (ext.startsWith('.') ? ext : `.${ext}`))

  return Array.from(new Set(normalized)).sort()
}

const supportedFileTypes = ref(normalizeExtensions(DEFAULT_SUPPORTED_TYPES))

const applySupportedFileTypes = (extensions) => {
  const normalized = normalizeExtensions(extensions)
  if (normalized.length > 0) {
    supportedFileTypes.value = normalized
  } else {
    supportedFileTypes.value = normalizeExtensions(DEFAULT_SUPPORTED_TYPES)
  }
}

const acceptedFileTypes = computed(() => {
  if (!supportedFileTypes.value.length) {
    return ''
  }
  const exts = new Set(supportedFileTypes.value)
  exts.add('.zip')
  return Array.from(exts).join(',')
})

const uploadHint = computed(() => {
  if (!supportedFileTypes.value.length) {
    return '加载中...'
  }
  const exts = new Set(supportedFileTypes.value)
  exts.add('.zip')
  return Array.from(exts).join(', ')
})

const isSupportedExtension = (fileName) => {
  if (!fileName) {
    return true
  }
  if (!supportedFileTypes.value.length) {
    return true
  }
  const lastDotIndex = fileName.lastIndexOf('.')
  if (lastDotIndex === -1) {
    return false
  }
  const ext = fileName.slice(lastDotIndex).toLowerCase()
  return supportedFileTypes.value.includes(ext) || ext === '.zip'
}

const loadSupportedFileTypes = async () => {
  try {
    const data = await fileApi.getSupportedFileTypes()
    applySupportedFileTypes(data?.file_types)
  } catch (error) {
    console.error('获取支持的文件类型失败:', error)
    message.warning('获取支持的文件类型失败，已使用默认配置')
    applySupportedFileTypes(DEFAULT_SUPPORTED_TYPES)
  }
}

onMounted(() => {
  loadSupportedFileTypes()
})

const visible = computed({
  get: () => props.visible,
  set: (value) => emit('update:visible', value)
})

const kbId = computed(() => store.kbId)
const chunkLoading = computed(() => store.state.chunkLoading)

// 上传模式
const uploadMode = ref('file')
const MAX_UPLOAD_CONCURRENCY = 10

// 文件列表
const fileList = ref([])

const uploadQueue = ref([])
const activeUploadCount = ref(0)
const uploadTaskStatus = ref({})
const uploadTaskProgress = ref({})
const progressExpanded = ref(false)

const totalUploadCount = computed(() => fileList.value.length)
const queuedUploadCount = computed(
  () => Object.values(uploadTaskStatus.value).filter((status) => status === 'queued').length
)
const uploadingUploadCount = computed(
  () => Object.values(uploadTaskStatus.value).filter((status) => status === 'uploading').length
)
const successUploadCount = computed(
  () => Object.values(uploadTaskStatus.value).filter((status) => status === 'done').length
)
const failedUploadCount = computed(
  () => Object.values(uploadTaskStatus.value).filter((status) => status === 'error').length
)
const hasPendingUploads = computed(() => queuedUploadCount.value + uploadingUploadCount.value > 0)

const overallUploadProgress = computed(() => {
  const total = totalUploadCount.value
  if (!total) {
    return 0
  }
  const validUidSet = new Set(fileList.value.map((file) => file.uid).filter(Boolean))
  let sum = 0
  for (const uid of validUidSet) {
    sum += uploadTaskProgress.value[uid] || 0
  }
  return Math.round(sum / total)
})

const showAggregateProgress = computed(() => totalUploadCount.value >= MAX_UPLOAD_CONCURRENCY)

const failedDetailItems = computed(() => {
  return fileList.value
    .map((file) => {
      const uid = file.uid
      const rawStatus = uploadTaskStatus.value[uid] || file.status || 'unknown'
      const detail = file?.response?.detail || file?.error?.message || ''
      return {
        uid,
        name: file.name || '未命名文件',
        status: rawStatus,
        errorText: detail || '上传失败'
      }
    })
    .filter((item) => item.status === 'error')
})

const canSubmit = computed(() => {
  if (uploadMode.value === 'url') {
    return urlList.value.some((item) => item.status === 'success')
  }
  if (uploadMode.value === 'workspace') {
    return selectedWorkspacePaths.value.length > 0 && !workspaceLoading.value
  }
  return (
    successUploadCount.value > 0 &&
    !hasPendingUploads.value &&
    versionCandidates.value.every((item) => item.action !== 'version' || item.currentFileId)
  )
})

const uploadModeOptions = computed(() => [
  {
    value: 'file',
    label: h('div', { class: 'segmented-option' }, [
      h(FileUp, { size: 16, class: 'option-icon' }),
      h('span', { class: 'option-text' }, '上传文件')
    ])
  },
  {
    value: 'folder',
    label: h('div', { class: 'segmented-option' }, [
      h(FolderUp, { size: 16, class: 'option-icon' }),
      h('span', { class: 'option-text' }, '上传文件夹')
    ])
  },
  {
    value: 'url',
    label: h('div', { class: 'segmented-option' }, [
      h(Link, { size: 16, class: 'option-icon' }),
      h('span', { class: 'option-text' }, '解析 URL')
    ])
  },
  {
    value: 'workspace',
    label: h('div', { class: 'segmented-option' }, [
      h(FolderOpen, { size: 16, class: 'option-icon' }),
      h('span', { class: 'option-text' }, '工作区')
    ])
  }
])

watch(uploadMode, (val) => {
  isFolderUpload.value = val === 'folder'
  // 切换模式时清空已选内容，避免混淆
  fileList.value = []
  sameNameFiles.value = []
  versionCandidates.value = []
  urlList.value = []
  newUrl.value = ''
  selectedWorkspacePaths.value = []
  workspaceCurrentPath.value = '/'
  workspaceItems.value = []
  for (const task of uploadQueue.value) {
    task.canceled = true
  }
  uploadQueue.value = []
  uploadTaskStatus.value = {}
  uploadTaskProgress.value = {}
  progressExpanded.value = false
  if (val === 'workspace') {
    loadWorkspaceFiles('/')
  }
})

watch(fileList, (newFileList) => {
  const validUidSet = new Set(newFileList.map((file) => file.uid).filter(Boolean))
  const nextStatus = {}
  const nextProgress = {}

  for (const [uid, status] of Object.entries(uploadTaskStatus.value)) {
    if (validUidSet.has(uid)) {
      nextStatus[uid] = status
    }
  }
  for (const [uid, progress] of Object.entries(uploadTaskProgress.value)) {
    if (validUidSet.has(uid)) {
      nextProgress[uid] = progress
    }
  }

  uploadTaskStatus.value = nextStatus
  uploadTaskProgress.value = nextProgress
  versionCandidates.value = pruneVersionCandidates(versionCandidates.value, newFileList)

  // 清洗状态按文件 uid 隔离：文件被移除时同步清理对应清洗状态，避免残留旧结果
  const nextCleanStates = new Map()
  for (const [uid, state] of cleanStates.value) {
    if (validUidSet.has(uid)) {
      nextCleanStates.set(uid, state)
    }
  }
  cleanStates.value = nextCleanStates
})

// URL 列表
// Item structure: { url: string, status: 'fetching'|'success'|'error', data: object|null, error: string }
const urlList = ref([])
const newUrl = ref('')
const fetchingUrls = ref(false)
const CONTENT_EXISTS_ERROR_TEXT = '内容已存在于知识库中'

// 文件版本处理
const sameNameFiles = ref([])
const versionCandidates = ref([])
const documentSearchVisible = ref(false)
const activeVersionCandidateUid = ref(null)
const activeVersionCandidate = computed(() => getVersionCandidate(activeVersionCandidateUid.value))

// 重复检测弹窗（PR12 吸收）：上传 409 时收集冲突，让用户选择策略
const duplicateConflictQueue = ref([])
const duplicateConflictPending = ref(false)
const duplicateConflictOpen = ref(false)
const duplicateConflictCurrent = ref(null)
const duplicateConflictIsExact = computed(() => duplicateConflictCurrent.value?.conflict_type === 'exact_content')

const updateVersionCandidate = (file, response) => {
  const candidate = buildVersionCandidate(file, response, props.canManage)
  versionCandidates.value = versionCandidates.value.filter((item) => item.uid !== file.uid)
  versionCandidates.value.push(candidate)
}

const getVersionCandidate = (uid) => versionCandidates.value.find((item) => item.uid === uid)

const openDocumentSearch = (candidate) => {
  activeVersionCandidateUid.value = candidate.uid
  documentSearchVisible.value = true
}

const applyDocumentSelection = (file) => {
  const index = versionCandidates.value.findIndex((item) => item.uid === activeVersionCandidateUid.value)
  if (index < 0) return
  versionCandidates.value[index] = selectVersionTarget(versionCandidates.value[index], file)
}

const syncSameNameSelection = (candidate) => {
  candidate.selectedFile = candidate.sameNameFiles.find((file) => file.file_id === candidate.currentFileId) || null
}

// 重复冲突弹窗处理（PR12 吸收）
const enqueueDuplicateConflict = (detail, task) => {
  duplicateConflictQueue.value.push({ detail, task })
  duplicateConflictPending.value = true
  if (!duplicateConflictOpen.value) {
    showNextDuplicateConflict()
  }
}

const showNextDuplicateConflict = () => {
  if (duplicateConflictQueue.value.length === 0) {
    duplicateConflictOpen.value = false
    duplicateConflictPending.value = false
    duplicateConflictCurrent.value = null
    return
  }
  duplicateConflictCurrent.value = duplicateConflictQueue.value[0].detail
  duplicateConflictOpen.value = true
}

const cancelDuplicateConflict = () => {
  duplicateConflictQueue.value.shift()
  if (duplicateConflictQueue.value.length === 0) {
    duplicateConflictPending.value = false
  }
  showNextDuplicateConflict()
}

const retryDuplicateUpload = (strategy, replaceFileId) => {
  const entry = duplicateConflictQueue.value.shift()
  if (!entry) {
    showNextDuplicateConflict()
    return
  }
  const conflictRetry = {
    options: entry.task.options,
    xhr: null,
    canceled: false,
    duplicateStrategy: strategy,
    replaceFileId: replaceFileId || null
  }
  uploadQueue.value.push(conflictRetry)
  processUploadQueue()
  showNextDuplicateConflict()
}

const resolveDuplicateConflict = (strategy) => {
  const detail = duplicateConflictCurrent.value
  if (!detail) return
  const resolution = buildDuplicateResolution(detail, strategy)
  if (!resolution) {
    cancelDuplicateConflict()
    return
  }
  retryDuplicateUpload(resolution.duplicateStrategy, resolution.replaceFileId)
}

const confirmReplacement = () => {
  resolveDuplicateConflict(DUPLICATE_STRATEGIES.REPLACE)
}

const keepBothDuplicate = () => {
  resolveDuplicateConflict(DUPLICATE_STRATEGIES.KEEP_BOTH)
}

const skipDuplicate = () => {
  resolveDuplicateConflict(DUPLICATE_STRATEGIES.SKIP)
}

// URL 相关功能
const isValidUrl = (string) => {
  try {
    const url = new URL(string)
    return url.protocol === 'http:' || url.protocol === 'https:'
  } catch {
    return false
  }
}

const mergeSameNameFiles = (sameNameList = []) => {
  if (!Array.isArray(sameNameList) || sameNameList.length === 0) {
    return
  }
  const existingIds = new Set(sameNameFiles.value.map((f) => f.file_id))
  const newConflicts = sameNameList.filter((f) => !existingIds.has(f.file_id))
  sameNameFiles.value.push(...newConflicts)
}

const fetchSingleUrlItem = async (item) => {
  item.status = 'fetching'
  try {
    const res = await fileApi.fetchUrl(item.url, kbId.value)
    item.status = 'success'
    item.data = res
    mergeSameNameFiles(res.same_name_files)
  } catch (error) {
    console.error('Failed to fetch URL:', error)
    item.status = 'error'

    const detailData = error.response?.data?.detail
    const detailMessage =
      (typeof detailData === 'string' ? detailData : detailData?.message) || error.message || ''
    if (detailMessage.includes('same content') || detailMessage.includes('相同内容')) {
      item.error = CONTENT_EXISTS_ERROR_TEXT
      mergeSameNameFiles(detailData?.same_name_files)
    } else {
      item.error = detailMessage || '加载失败'
    }
  }
}

const handleFetchUrls = async () => {
  const text = newUrl.value
  if (!text) return

  const lines = text
    .split(/[\r\n]+/)
    .map((l) => l.trim())
    .filter((l) => l)
  if (lines.length === 0) return

  // 1. 预处理：添加到列表
  const newItems = []
  for (const url of lines) {
    if (!isValidUrl(url)) {
      continue
    }
    if (urlList.value.some((u) => u.url === url)) continue

    const item = { url, status: 'pending', data: null, error: '' }
    urlList.value.push(item)
    newItems.push(item)
  }

  if (newItems.length === 0) {
    if (lines.length > 0) {
      message.warning('没有检测到有效的新 URL')
    }
    return
  }

  newUrl.value = '' // 清空输入框
  fetchingUrls.value = true

  await Promise.all(newItems.map(fetchSingleUrlItem))
  fetchingUrls.value = false
}

const removeUrl = (index) => {
  urlList.value.splice(index, 1)
}

// 工作区文件选择
const workspaceLoading = ref(false)
const workspaceItems = ref([])
const workspaceCurrentPath = ref('/')
const selectedWorkspacePaths = ref([])
const selectedWorkspacePathSet = computed(() => new Set(selectedWorkspacePaths.value))
const workspaceDirectoryItems = computed(() => workspaceItems.value.filter((entry) => entry.is_dir))
const workspaceFileItems = computed(() =>
  workspaceItems.value
    .filter((entry) => !entry.is_dir)
    .map((entry) => ({
      ...entry,
      supported: isSupportedExtension(entry.name || entry.path)
    }))
)

const formatFileSize = (size) => {
  if (!Number.isFinite(size)) return '-'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

const loadWorkspaceFiles = async (path = workspaceCurrentPath.value) => {
  if (workspaceLoading.value) return
  const targetPath = typeof path === 'string' ? path : workspaceCurrentPath.value

  workspaceLoading.value = true
  try {
    const data = await getWorkspaceTree(targetPath, false, false)
    const entries = Array.isArray(data?.entries) ? data.entries : []
    workspaceCurrentPath.value = targetPath
    workspaceItems.value = entries
  } catch (error) {
    console.error('加载工作区文件失败:', error)
    message.error('加载工作区文件失败: ' + (error.message || '未知错误'))
  } finally {
    workspaceLoading.value = false
  }
}

const openWorkspaceDirectory = (path) => {
  loadWorkspaceFiles(path)
}

const openWorkspaceParent = () => {
  if (workspaceCurrentPath.value === '/') return
  const normalized = workspaceCurrentPath.value.replace(/\/$/, '')
  const index = normalized.lastIndexOf('/')
  const parentPath = index <= 0 ? '/' : normalized.slice(0, index)
  loadWorkspaceFiles(parentPath)
}

const toggleWorkspacePath = (path, checked) => {
  if (checked) {
    if (!selectedWorkspacePaths.value.includes(path)) {
      selectedWorkspacePaths.value = [...selectedWorkspacePaths.value, path]
    }
    return
  }
  selectedWorkspacePaths.value = selectedWorkspacePaths.value.filter((item) => item !== path)
}

// OCR服务健康状态
const ocrHealthStatus = ref({
  rapid_ocr: { status: 'unknown', message: '' },
  mineru_ocr: { status: 'unknown', message: '' },
  mineru_official: { status: 'unknown', message: '' },
  pp_structure_v3_ocr: { status: 'unknown', message: '' },
  deepseek_ocr: { status: 'unknown', message: '' },
  paddleocr_vl_1_6: { status: 'unknown', message: '' },
  paddleocr_pp_ocrv6: { status: 'unknown', message: '' }
})

// OCR健康检查状态
const ocrHealthChecking = ref(false)
const ocrPanelOpen = ref(false)
const unavailableOcrExpanded = ref(false)
const ocrEngineTouched = ref(false)

// 解析参数
const processingParams = ref({
  ocr_engine: DEFAULT_OCR_ENGINE,
  ocr_engine_config: {}
})

// 自动入库相关
const autoIndex = ref(false)
const indexParams = ref({
  chunk_preset_id: '',
  chunk_parser_config: {}
})

const buildAutoIndexParams = () => {
  return buildChunkParamsPayload(indexParams.value, {
    includeSizeOverlap: true
  })
}

// AI 清洗排版相关
// 清洗支持纯文本与 Word/Excel（后端 Docling 解析 + LLM 增强）。HIDE_CLEAN 置为 true 可整体隐藏该功能。
const HIDE_CLEAN = false
const enableClean = ref(false)

// 清洗状态按文件 uid 隔离，避免再次上传新文档时残留旧结果。
// fileCleanState: { status: 'idle'|'loading'|'done'|'error', cleanedMarkdown: '', error: '', viewMode: 'edit' }
const cleanStates = ref(new Map())

const getFileCleanState = (file) => {
  if (!file?.uid) return null
  if (!cleanStates.value.has(file.uid)) {
    cleanStates.value.set(file.uid, {
      status: 'idle',
      cleanedMarkdown: '',
      error: '',
      viewMode: 'edit'
    })
  }
  return cleanStates.value.get(file.uid)
}

const OFFICE_EXTENSIONS = ['.docx', '.xlsx']
const isOfficeFile = (name) => {
  const ext = String(name || '').toLowerCase().split('.').pop()
  return OFFICE_EXTENSIONS.includes(`.${ext}`)
}

// xlsx 不参与清洗，始终走 Office 编辑
const isXlsxFile = (name) => {
  const ext = String(name || '').toLowerCase().split('.').pop()
  return ext === 'xlsx'
}

// 已上传完成且可清洗的文件（排除 xlsx，xlsx 不参与清洗）
const uploadedCleanFiles = computed(() =>
  fileList.value.filter(
    (file) => file.status === 'done' && file.response?.file_path && !isXlsxFile(file.name)
  )
)

// 已上传完成的 Word/Excel（未勾选清洗或 xlsx 时走 Office 编辑）
const uploadedOfficeFiles = computed(() =>
  fileList.value.filter(
    (file) => file.status === 'done' && file.response?.file_path && isOfficeFile(file.name)
  )
)

// 是否包含 xlsx（勾选清洗时 xlsx 仍走 Office 编辑面板）
const hasXlsxFiles = computed(() => uploadedOfficeFiles.value.some((file) => isXlsxFile(file.name)))

// Office 编辑状态：按文件 uid 记录写回后的 file_path
const officeEditStates = ref(new Map())
const getOfficeEditState = (file) => {
  if (!file?.uid) return null
  if (!officeEditStates.value.has(file.uid)) {
    officeEditStates.value.set(file.uid, { filePath: '', contentHash: '', size: 0, edited: false })
  }
  return officeEditStates.value.get(file.uid)
}
const officeEditTarget = ref(null)   // 当前打开编辑的文件
const officeEditVisible = ref(false)

// 上传文件预览：点击文件名触发（upload 后尚无 file_id，按 MinIO 路径预览）
const previewVisible = ref(false)
const previewTarget = ref(null) // { name }
const previewData = ref(null) // normalizePreviewResponse 结果

// itemRender 的默认列表项 originNode 是 VNode，这里经 h() 渲染并预留默认槽放「查看」按钮。
// 注意：prop 必须用单词形式（:origin），模板编译器对 kebab-case 不会转 camel，函数式组件拿不到 props.originNode
const UploadItemWrap = (props, { slots }) =>
  h('div', { class: 'upload-item-wrap' }, [props.origin, slots.default?.()])

const handlePreviewUploaded = async (file) => {
  const filePath = file?.response?.file_path
  const kbIdValue = file?.response?.kb_id
  if (!filePath || !kbIdValue) return
  try {
    previewTarget.value = { name: file.name || file.response?.filename || '文档' }
    const response = await getUploadedFilePreview(kbIdValue, filePath, file.name)
    previewData.value = await normalizePreviewResponse(response, {
      filename: file.name || file.response?.filename
    })
    previewVisible.value = true
  } catch (error) {
    console.error('上传文件预览失败:', error)
    message.error(`预览失败：${error?.message || '请稍后重试'}`)
  }
}

// 调用 AI 清洗排版接口，入参为已上传文件的 MinIO URL（file_path），由服务端读取解析后清洗
const runCleanForFile = async (file) => {
  const state = getFileCleanState(file)
  const filePath = file.response?.file_path
  if (!state || !filePath) {
    message.error('上传文件信息不完整，请重新上传')
    return
  }
  const fileName = file.name || file.response?.filename || 'document'
  state.status = 'loading'
  state.error = ''
  try {
    const response = await databaseApi.cleanDocument(kbId.value, null, fileName, filePath)
    state.cleanedMarkdown = response?.cleaned_markdown || ''
    state.status = state.cleanedMarkdown ? 'done' : 'error'
    if (!state.cleanedMarkdown) state.error = '清洗结果为空'
  } catch (error) {
    console.error('文档清洗失败:', error)
    state.status = 'error'
    state.error = error?.message || '文档清洗失败，请稍后重试'
  }
}

// 批量并行清洗全部已上传文件（后端一次并发）
const runCleanAll = async () => {
  const files = uploadedCleanFiles.value
  if (files.length === 0) {
    message.error('请先上传文件')
    return
  }
  const items = files.map((file) => ({
    file_path: file.response.file_path,
    filename: file.name || file.response?.filename || 'document'
  }))
  for (const file of files) {
    const state = getFileCleanState(file)
    state.status = 'loading'
    state.error = ''
  }
  try {
    const response = await databaseApi.cleanDocuments(kbId.value, items)
    const results = response?.results || []
    results.forEach((result, index) => {
      const file = files[index]
      const state = file && getFileCleanState(file)
      if (!state) return
      state.cleanedMarkdown = result?.cleaned_markdown || ''
      state.status = result?.error ? 'error' : state.cleanedMarkdown ? 'done' : 'error'
      state.error = result?.error || (state.cleanedMarkdown ? '' : '清洗结果为空')
    })
  } catch (error) {
    console.error('批量文档清洗失败:', error)
    for (const file of files) {
      const state = getFileCleanState(file)
      if (state) {
        state.status = 'error'
        state.error = error?.message || '文档清洗失败，请稍后重试'
      }
    }
  }
}

// 清洗后把 cleanedMarkdown 作为 .md 文件上传，返回 file 响应（含 file_path/content_hash）。
// 文件名用原文件名前缀 + _cleaned，避免固定 cleaned.md 与历史清洗文件同名触发版本候选。
// 注意 fileApi.uploadFile(file, kbId) 签名：传 File 对象，由 uploadFile 内部构造 FormData。
const uploadCleanedMarkdown = async (markdown, originalName = '') => {
  if (!markdown) return null
  const base = String(originalName || 'document').replace(/\.(md|txt|markdown)$/i, '') || 'document'
  const cleanName = `${base}_cleaned.md`
  const blob = new Blob([markdown], { type: 'text/markdown' })
  const cleanFile = new File([blob], cleanName, { type: 'text/markdown' })
  return await fileApi.uploadFile(cleanFile, kbId.value)
}

// Word/Excel 编辑：打开 OfficeEditModal（上传时 filePath 模式）
const openOfficeEdit = (file) => {
  officeEditTarget.value = file
  officeEditVisible.value = true
}

// OfficeEditModal 写回后的回调：记录新 file_path
const handleOfficeWriteback = (res) => {
  const file = officeEditTarget.value
  if (!file) return
  const state = getOfficeEditState(file)
  state.filePath = res?.file_path || ''
  state.contentHash = res?.content_hash || ''
  state.size = res?.size || 0
  state.edited = Boolean(state.filePath)
}

const cleanAllLoading = computed(() =>
  uploadedCleanFiles.value.some((file) => getFileCleanState(file)?.status === 'loading')
)

const cleanStatusLabel = (status) => {
  const labels = { idle: '待清洗', loading: '清洗中', done: '已清洗', error: '失败' }
  return labels[status] || status || '待清洗'
}

const isFolderUpload = ref(false)

// 计算属性：是否启用了OCR
const isOcrEnabled = computed(() => {
  return processingParams.value.ocr_engine !== 'disable'
})

// 上传模式切换相关逻辑已移除

// 计算属性：是否有PDF或图片文件
const hasPdfOrImageFiles = computed(() => {
  if (fileList.value.length === 0) {
    return false
  }

  const pdfExtensions = ['.pdf']
  const imageExtensions = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.gif', '.webp']
  const ocrExtensions = [...pdfExtensions, ...imageExtensions]

  return fileList.value.some((file) => {
    if (file.status !== 'done') {
      return false
    }

    const filePath = file.response?.file_path || file.name
    if (!filePath) {
      return false
    }

    const ext = filePath.substring(filePath.lastIndexOf('.')).toLowerCase()
    return ocrExtensions.includes(ext)
  })
})

// 计算属性：是否有ZIP文件
const hasZipFiles = computed(() => {
  if (fileList.value.length === 0) {
    return false
  }

  return fileList.value.some((file) => {
    if (file.status !== 'done') {
      return false
    }

    const filePath = file.response?.file_path || file.name
    if (!filePath) {
      return false
    }

    const ext = filePath.substring(filePath.lastIndexOf('.')).toLowerCase()
    return ext === '.zip'
  })
})

const ocrEngineOptions = [
  {
    value: 'disable',
    label: '不启用',
    description: '不启用 OCR，仅处理文本文件'
  },
  {
    value: 'rapid_ocr',
    label: 'RapidOCR (ONNX)',
    description: 'ONNX with RapidOCR'
  },
  {
    value: 'mineru_ocr',
    label: 'MinerU OCR',
    description: 'MinerU OCR'
  },
  {
    value: 'mineru_official',
    label: 'MinerU Official API',
    description: 'MinerU Official API'
  },
  {
    value: 'pp_structure_v3_ocr',
    label: 'PP-Structure-V3',
    description: 'PP-Structure-V3'
  },
  {
    value: 'deepseek_ocr',
    label: 'DeepSeek OCR',
    description: 'DeepSeek OCR (SiliconFlow)'
  },
  {
    value: 'paddleocr_vl_1_6',
    label: 'PaddleOCR-VL-1.6',
    description: 'PaddleOCR-VL-1.6 API'
  },
  {
    value: 'paddleocr_pp_ocrv6',
    label: 'PP-OCRv6',
    description: 'PaddleOCR PP-OCRv6 API'
  }
]

const resolveDefaultOcrEngine = () => {
  const configuredEngine = String(
    configStore.config?.default_ocr_engine || DEFAULT_OCR_ENGINE
  ).trim()
  return ocrEngineOptions.some((option) => option.value === configuredEngine)
    ? configuredEngine
    : DEFAULT_OCR_ENGINE
}

const applyDefaultOcrEngine = () => {
  processingParams.value.ocr_engine = resolveDefaultOcrEngine()
}

watch(
  () => configStore.config?.default_ocr_engine,
  () => {
    if (props.visible && !ocrEngineTouched.value) {
      applyDefaultOcrEngine()
    }
  }
)

const ocrStatusLabels = {
  local: '不启用',
  healthy: '可用',
  configured: '已配置',
  unavailable: '不可用',
  unhealthy: '异常',
  timeout: '超时',
  error: '异常',
  checking: '检查中',
  unknown: '状态未知'
}

const getOcrStatus = (engine) => {
  if (engine === 'disable') return 'local'
  const current = ocrHealthStatus.value?.[engine]
  if (ocrHealthChecking.value && (!current || current.status === 'unknown')) return 'checking'
  return current?.status || 'unknown'
}

const getOcrStatusLabel = (engine) => ocrStatusLabels[getOcrStatus(engine)] || '状态未知'

const getOcrDescription = (engine) => {
  const option = ocrEngineOptions.find((item) => item.value === engine)
  if (engine === 'disable') return option?.description || '不启用 OCR，仅处理文本文件'

  const messageText = ocrHealthStatus.value?.[engine]?.message
  if (messageText) return messageText

  const status = getOcrStatus(engine)
  const fallbackMap = {
    healthy: '服务正常',
    configured: 'Token 已配置，将在解析时验证',
    unavailable: '服务不可用',
    unhealthy: '服务异常',
    timeout: '服务检查超时',
    error: '服务异常',
    checking: '正在检查服务状态',
    unknown: option?.description || '服务状态未知'
  }
  return fallbackMap[status] || option?.description || '服务状态未知'
}

const isUnavailableOcrEngine = (engine) => ['unavailable', 'error'].includes(getOcrStatus(engine))

const availableOcrOptions = computed(() =>
  ocrEngineOptions.filter((option) => !isUnavailableOcrEngine(option.value))
)

const unavailableOcrOptions = computed(() =>
  ocrEngineOptions.filter((option) => isUnavailableOcrEngine(option.value))
)

const selectedOcrEngineLabel = computed(() => {
  return (
    ocrEngineOptions.find((option) => option.value === processingParams.value.ocr_engine)?.label ||
    '选择 OCR 引擎'
  )
})

const selectOcrEngine = (engine) => {
  if (isUnavailableOcrEngine(engine)) return
  ocrEngineTouched.value = true
  processingParams.value.ocr_engine = engine
  ocrPanelOpen.value = false
}

const toggleUnavailableOcrOptions = () => {
  unavailableOcrExpanded.value = !unavailableOcrExpanded.value
}

// 验证OCR服务可用性
const validateOcrService = () => {
  if (!isOcrEnabled.value) {
    return true
  }

  const engine = processingParams.value.ocr_engine
  if (isUnavailableOcrEngine(engine)) {
    message.error(`OCR服务不可用: ${getOcrDescription(engine)}`)
    return false
  }

  return true
}

const resetVersionSelection = () => {
  versionCandidates.value = []
  documentSearchVisible.value = false
  activeVersionCandidateUid.value = null
}

const handleCancel = () => {
  resetVersionSelection()
  emit('update:visible', false)
}

const beforeUpload = (file) => {
  if (!isSupportedExtension(file?.name)) {
    message.error(`不支持的文件类型：${file?.name || '未知文件'}`)
    return Upload.LIST_IGNORE
  }
  return true
}

const formatFileTime = (timestamp) => {
  if (!timestamp) return ''
  try {
    const date = new Date(timestamp)
    return date.toLocaleString()
  } catch {
    return timestamp
  }
}

const downloadSameNameFile = async (file) => {
  try {
    // 获取当前数据库ID
    const currentDbId = kbId.value
    if (!currentDbId) {
      message.error('知识库ID不存在')
      return
    }

    message.loading('正在下载文件...', 0)
    const response = await documentApi.downloadDocument(currentDbId, file.file_id)
    message.destroy()

    // 创建下载链接
    const blob = await response.blob() // 从 Response 对象中提取 Blob 数据
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = file.filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)

    message.success(`文件 ${file.filename} 下载成功`)
  } catch (error) {
    message.destroy()
    console.error('下载文件失败:', error)
    message.error(`下载文件失败: ${error.message || '未知错误'}`)
  }
}

const deleteSameNameFile = (file) => {
  Modal.confirm({
    title: '确认删除文件',
    content: `确定要删除文件 "${file.filename}" 吗？此操作不可恢复。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        // 获取当前数据库ID
        const currentDbId = kbId.value
        if (!currentDbId) {
          message.error('知识库ID不存在')
          return
        }

        message.loading('正在删除文件...', 0)
        await documentApi.deleteDocument(currentDbId, file.file_id)
        message.destroy()

        // 从同名文件列表中移除
        sameNameFiles.value = sameNameFiles.value.filter((f) => f.file_id !== file.file_id)

        message.success(`文件 ${file.filename} 删除成功`)
      } catch (error) {
        message.destroy()
        console.error('删除文件失败:', error)
        message.error(`删除文件失败: ${error.message || '未知错误'}`)
      }
    }
  })
}

const customRequest = async (options) => {
  const fileUid = options.file?.uid
  if (fileUid) {
    uploadTaskStatus.value[fileUid] = 'queued'
    uploadTaskProgress.value[fileUid] = 0
  }

  const task = {
    options,
    xhr: null,
    canceled: false
  }

  uploadQueue.value.push(task)
  processUploadQueue()

  return {
    abort: () => {
      task.canceled = true
      if (task.xhr) {
        task.xhr.abort()
      }
      const queueIndex = uploadQueue.value.indexOf(task)
      if (queueIndex !== -1) {
        uploadQueue.value.splice(queueIndex, 1)
      }
      if (fileUid) {
        uploadTaskStatus.value[fileUid] = 'error'
      }
    }
  }
}

const processUploadQueue = () => {
  while (activeUploadCount.value < MAX_UPLOAD_CONCURRENCY && uploadQueue.value.length > 0) {
    const task = uploadQueue.value.shift()
    if (!task || task.canceled) {
      continue
    }

    activeUploadCount.value += 1
    runUploadTask(task)
      .catch(() => {
        // 错误已经在 runUploadTask 内处理，这里只保证队列继续消费
      })
      .finally(() => {
        activeUploadCount.value -= 1
        processUploadQueue()
      })
  }
}

const runUploadTask = (task) => {
  const { file, onProgress, onSuccess, onError } = task.options
  const fileUid = file?.uid

  if (fileUid) {
    uploadTaskStatus.value[fileUid] = 'uploading'
  }

  return new Promise((resolve, reject) => {
    const formData = new FormData()
    const filename =
      isFolderUpload.value && file.webkitRelativePath ? file.webkitRelativePath : file.name
    formData.append('file', file, filename)

    const currentKbId = kbId.value
    if (!currentKbId) {
      const error = new Error('Database ID is missing')
      if (fileUid) {
        uploadTaskStatus.value[fileUid] = 'error'
      }
      onError(error)
      reject(error)
      return
    }

    const xhr = new XMLHttpRequest()
    task.xhr = xhr
    xhr.open(
      'POST',
      buildKnowledgeUploadUrl(currentKbId, task.duplicateStrategy || DUPLICATE_STRATEGIES.PROMPT, task.replaceFileId, task.parentId)
    )

    const headers = getAuthHeaders()
    for (const [key, value] of Object.entries(headers)) {
      xhr.setRequestHeader(key, value)
    }

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) {
        return
      }
      const percent = Math.min(100, (event.loaded / event.total) * 100)
      if (fileUid) {
        uploadTaskProgress.value[fileUid] = percent
      }
      onProgress({ percent })
    }

    xhr.onload = () => {
      if (task.canceled) {
        resolve()
        return
      }

      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const response = JSON.parse(xhr.responseText)
          if (fileUid) {
            uploadTaskStatus.value[fileUid] = 'done'
            uploadTaskProgress.value[fileUid] = 100
          }
          onSuccess(response, xhr)
          resolve()
        } catch (error) {
          if (fileUid) {
            uploadTaskStatus.value[fileUid] = 'error'
          }
          onError(error)
          reject(error)
        }
        return
      }

      let errorResp
      try {
        errorResp = JSON.parse(xhr.responseText || '{}')
      } catch {
        errorResp = {}
      }
      file.response = errorResp
      const duplicateDetail = getDuplicateConflictDetail(errorResp)
      if (xhr.status === 409 && duplicateDetail) {
        // 重复冲突：进入弹窗让用户选择策略，不算失败
        enqueueDuplicateConflict(duplicateDetail, task)
        onError(new Error(getDuplicateConflictMessage(duplicateDetail)), file)
        reject(new Error(getDuplicateConflictMessage(duplicateDetail)))
        return
      }
      const error = new Error(getSafeUploadErrorMessage(errorResp) || errorResp.detail || 'Upload failed')
      if (fileUid) {
        uploadTaskStatus.value[fileUid] = 'error'
      }
      onError(error, file)
      reject(error)
    }

    xhr.onerror = (errorEvent) => {
      if (fileUid) {
        uploadTaskStatus.value[fileUid] = 'error'
      }
      onError(errorEvent)
      reject(errorEvent)
    }

    xhr.onabort = () => {
      if (fileUid) {
        uploadTaskStatus.value[fileUid] = 'error'
      }
      const abortError = new Error('Upload aborted')
      onError(abortError)
      reject(abortError)
    }

    xhr.send(formData)
  })
}

const handleFileUpload = (info) => {
  if (info?.file?.status === 'error') {
    const file = info.file
    // 尝试多种方式获取错误信息
    const detail = file?.response?.detail || file?.error?.message || ''
    if (detail.includes('same content') || detail.includes('相同内容')) {
      message.error(`${file.name} 已是相同内容文件，无需重复上传`)
    } else {
      message.error(detail || `文件上传失败：${file.name}`)
    }
  }

  // 为每个成功上传的文件建立处理决策；同名文件继续自动推荐为新版本
  if (info?.file?.status === 'done' && info.file.response) {
    updateVersionCandidate(info.file, info.file.response)
  }

  fileList.value = info?.fileList ?? []
}

const handleDrop = () => {}

// 已移除文件夹上传逻辑

const checkOcrHealth = async () => {
  if (ocrHealthChecking.value) return

  ocrHealthChecking.value = true
  try {
    const healthData = await ocrApi.getHealth()
    ocrHealthStatus.value = healthData.services
  } catch (error) {
    console.error('OCR健康检查失败:', error)
    message.error('OCR服务健康检查失败')
  } finally {
    ocrHealthChecking.value = false
  }
}

const handleOcrPanelOpenChange = (open) => {
  ocrPanelOpen.value = open
  if (open) {
    checkOcrHealth()
  }
}

const getAuthHeaders = () => {
  const userStore = useUserStore()
  return userStore.getAuthHeaders()
}

const openDocLink = () => {
  message.info('文档解析说明请联系系统管理员获取')
}

const chunkData = async () => {
  if (!props.canUpload) {
    message.error('没有上传权限')
    return
  }
  if (props.deferProcessing) {
    autoIndex.value = false
  }

  if (!kbId.value) {
    message.error('请先选择知识库')
    return
  }

  // 验证OCR服务可用性（非 URL 模式下）
  if (uploadMode.value !== 'url' && !validateOcrService()) {
    return
  }

  if (uploadMode.value === 'workspace') {
    if (selectedWorkspacePaths.value.length === 0) {
      message.error('请先选择工作区文件')
      return
    }

    try {
      store.state.chunkLoading = true
      const res = await fileApi.importWorkspaceFiles(kbId.value, selectedWorkspacePaths.value)
      const importedItems = Array.isArray(res?.items) ? res.items : []
      if (importedItems.length === 0) {
        message.error('工作区文件导入失败')
        return
      }

      const imageExtensions = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif']
      const items = []
      const content_hashes = {}
      const file_sizes = {}
      for (const item of importedItems) {
        const filePath = item.file_path
        if (!filePath) continue
        items.push(filePath)
        if (item.content_hash) content_hashes[filePath] = item.content_hash
        if (Number.isFinite(item.size)) file_sizes[filePath] = item.size
        mergeSameNameFiles(item.same_name_files)

        const ext = filePath.substring(filePath.lastIndexOf('.')).toLowerCase()
        if (imageExtensions.includes(ext) && !isOcrEnabled.value) {
          message.error({
            content: '检测到图片文件，必须启用 OCR 才能提取文本内容。',
            duration: 5
          })
          return
        }
      }

      const params = { ...processingParams.value, content_hashes, file_sizes }
      if (autoIndex.value) {
        params.auto_index = true
        Object.assign(params, buildAutoIndexParams())
      }

      const addFiles = props.deferProcessing ? store.addUploadedFiles : store.addFiles
      await addFiles({
        items,
        contentType: 'file',
        params,
        parentId: selectedFolderId.value
      })

      emit('success')
      handleCancel()
      selectedWorkspacePaths.value = []
    } catch (error) {
      console.error('工作区文件导入失败:', error)
      message.error('工作区文件导入失败: ' + (error.message || '未知错误'))
    } finally {
      store.state.chunkLoading = false
    }
    return
  }

  // URL 模式处理
  if (uploadMode.value === 'url') {
    // 过滤出成功的项
    const successfulItems = urlList.value.filter((item) => item.status === 'success' && item.data)
    if (successfulItems.length === 0) {
      message.error('请添加并等待至少一个 URL 解析成功')
      return
    }

    // 批内按内容哈希去重，避免同一批次重复入库
    const deduplicatedItems = []
    const seenKeys = new Set()
    let skippedDuplicates = 0
    for (const item of successfulItems) {
      const dedupKey = item.data?.content_hash || item.data?.file_path || item.url
      if (seenKeys.has(dedupKey)) {
        skippedDuplicates += 1
        continue
      }
      seenKeys.add(dedupKey)
      deduplicatedItems.push(item)
    }

    if (deduplicatedItems.length === 0) {
      message.error('URL 内容均为重复项，请更换后重试')
      return
    }

    if (skippedDuplicates > 0) {
      message.warning(`检测到 ${skippedDuplicates} 个重复 URL 内容，已保留首个并跳过其余项`)
    }

    try {
      store.state.chunkLoading = true
      const params = { ...processingParams.value }
      if (autoIndex.value) {
        params.auto_index = true
        Object.assign(params, buildAutoIndexParams())
      }

      // 构造 _preprocessed_map 和 items (minio urls)
      const items = []
      const preprocessedMap = {}
      for (const item of deduplicatedItems) {
        // item.data = { file_path: "http://minio...", content_hash: "...", filename: "...", ... }
        // 注意：fetch-url 返回的 file_path 其实是 MinIO URL
        // 我们需要传递 MinIO URL 给 addDocuments
        const minioUrl = item.data.file_path
        items.push(minioUrl)
        preprocessedMap[minioUrl] = {
          path: minioUrl,
          content_hash: item.data.content_hash,
          filename: item.data.filename,
          file_size: item.data.size
        }
      }
      params._preprocessed_map = preprocessedMap

      const addFiles = props.deferProcessing ? store.addUploadedFiles : store.addFiles
      await addFiles({
        items: items,
        contentType: 'file',
        params,
        parentId: selectedFolderId.value
      })

      emit('success')
      handleCancel()
      urlList.value = []
      newUrl.value = ''
    } catch (error) {
      console.error('URL 提交失败:', error)
      message.error('URL 提交失败: ' + (error.message || '未知错误'))
    } finally {
      store.state.chunkLoading = false
    }
    return
  }

  // 文件模式处理
  const imageExtensions = ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif']

  // 提取已上传的文件信息
  const items = []
  const content_hashes = {}
  const file_sizes = {}
  const versionUploads = []
  if (findDuplicateVersionTarget(versionCandidates.value)) {
    message.error('同一批次不能为同一文档提交多个新版本')
    return
  }
  for (const file of fileList.value) {
    if (file.status !== 'done') continue
    const file_path = file.response?.file_path
    const content_hash = file.response?.content_hash
    if (!file_path) continue

    const versionCandidate = getVersionCandidate(file.uid)
    // 勾选清洗时 docx/文本版本上传走清洗版入库；xlsx 不参与清洗，版本上传照常
    const xlsxCleanExempt = isXlsxFile(file.name)
    if (versionCandidate?.action === 'version' && (!enableClean.value || xlsxCleanExempt)) {
      versionUploads.push({ file, candidate: versionCandidate })
    } else {
      // Word/Excel：若已编辑，用写回后的新 file_path 入库（不生成 .md）
      const officeState = getOfficeEditState(file)
      const effectivePath = officeState?.edited && officeState.filePath ? officeState.filePath : file_path
      const effectiveHash = officeState?.edited && officeState.contentHash ? officeState.contentHash : content_hash
      // 勾选"AI 清洗排版"时原始文件不入库，只入库清洗后的 md，避免产生双份记录；
      // xlsx 不参与清洗，始终走 Office 编辑路径入库
      if (!enableClean.value || xlsxCleanExempt) {
        items.push(effectivePath)
        if (effectiveHash) content_hashes[effectivePath] = effectiveHash
        if (officeState?.edited && Number.isFinite(officeState.size)) {
          file_sizes[effectivePath] = officeState.size
        } else if (Number.isFinite(file.response?.size)) {
          file_sizes[effectivePath] = file.response.size
        }
      }
    }

    // 检查是否需要OCR
    const ext = file_path.substring(file_path.lastIndexOf('.')).toLowerCase()
    if (imageExtensions.includes(ext) && !isOcrEnabled.value) {
      message.error({
        content: '检测到图片文件，必须启用 OCR 才能提取文本内容。',
        duration: 5
      })
      return
    }
  }

  // 勾选清洗时原始文件不入库，需以已上传文件判断是否为空，否则会提前拦截
  if (items.length === 0 && versionUploads.length === 0 && uploadedCleanFiles.value.length === 0) {
    message.error('请先上传文件')
    return
  }

  try {
    store.state.chunkLoading = true
    const params = { ...processingParams.value, content_hashes, file_sizes }
    if (autoIndex.value) {
      params.auto_index = true
      Object.assign(params, buildAutoIndexParams())
    }

    // AI 清洗排版：office 文件写回原格式（docx/xlsx），文本/md 作为新 .md 文件上传，再走统一入库
    if (enableClean.value) {
      // 勾选清洗后原始文件不入库，若存在未清洗完成的文件会直接丢失，需先全部清洗完成
      const unCleanedFiles = uploadedCleanFiles.value.filter(
        (file) => getFileCleanState(file)?.status !== 'done' || !getFileCleanState(file)?.cleanedMarkdown
      )
      if (unCleanedFiles.length > 0) {
        message.error('勾选了 AI 清洗排版，请先对全部文件执行清洗（全部清洗或逐个清洗）')
        return
      }
      for (const file of uploadedCleanFiles.value) {
        const state = getFileCleanState(file)
        if (!state || state.status !== 'done' || !state.cleanedMarkdown) continue
        const filename = file.name || file.response?.filename || 'document'
        let cleanRes
        if (isOfficeFile(file.name)) {
          // Word/Excel：清洗后写回原格式入库，保持 .docx/.xlsx
          cleanRes = await databaseApi.cleanWriteback(kbId.value, {
            cleaned_markdown: state.cleanedMarkdown,
            filename
          })
        } else {
          // 文本/md：清洗后作为 .md 文件上传
          cleanRes = await uploadCleanedMarkdown(state.cleanedMarkdown, filename)
        }
        const cleanPath = cleanRes?.file_path
        const cleanHash = cleanRes?.content_hash
        if (!cleanPath) {
          throw new Error('清洗后内容上传失败，请重试')
        }
        items.push(cleanPath)
        if (cleanHash) content_hashes[cleanPath] = cleanHash
        if (Number.isFinite(cleanRes?.size)) file_sizes[cleanPath] = cleanRes.size
      }
      if (items.length > 0) {
        params.content_hashes = content_hashes
        params.file_sizes = file_sizes
      }
    }

    // 重复检测策略逐文件映射（PR12 吸收）：上传阶段用户选的策略随入库一起提交
    if (items.length > 0) {
      const duplicate_strategies = {}
      const replace_file_ids = {}
      for (const file of fileList.value) {
        if (file.status !== 'done') continue
        const file_path = file.response?.file_path
        if (!file_path) continue
        if (file.response?.duplicate_strategy) {
          duplicate_strategies[file_path] = file.response.duplicate_strategy
        }
        if (file.response?.replace_file_id) {
          replace_file_ids[file_path] = file.response.replace_file_id
        }
      }
      if (Object.keys(duplicate_strategies).length > 0) params.duplicate_strategies = duplicate_strategies
      if (Object.keys(replace_file_ids).length > 0) params.replace_file_ids = replace_file_ids

      const addFiles = props.deferProcessing ? store.addUploadedFiles : store.addFiles
      const added = await addFiles({
        items,
        contentType: 'file',
        params,
        parentId: selectedFolderId.value
      })
      if (added === false) return
    }

    for (const { file, candidate } of versionUploads) {
      const result = await documentApi.createDocumentVersion(kbId.value, candidate.currentFileId, {
        file_path: file.response.file_path,
        content_hash: file.response.content_hash,
        filename: file.response.filename || file.name,
        original_filename: file.response.original_filename || file.name,
        file_size: file.response.size,
        processing_params: params
      })
      if (result?.status !== 'queued') {
        throw new Error(`版本更新任务提交失败：${file.name}`)
      }
    }

    if (versionUploads.length > 0) {
      message.success(`${versionUploads.length} 个文档版本更新任务已提交，请在任务中心查看处理进度`)
    }
    emit('success')
    handleCancel()
    fileList.value = []
    sameNameFiles.value = []
  } catch (error) {
    console.error('文件处理失败:', error)
    const detail = error.response?.data?.detail
    const errorText =
      (typeof detail === 'string' ? detail : detail?.message) || error.message || '未知错误'
    message.error(`文件或版本任务提交失败: ${errorText}`)
  } finally {
    store.state.chunkLoading = false
  }
}
</script>

<style lang="less" scoped>
.version-candidate {
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
}

.version-candidate-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;

  .file-meta {
    min-width: 0;
  }

  :deep(.ant-radio-group) {
    flex-shrink: 0;
  }
}

.version-target-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;

  > .ant-btn {
    flex-shrink: 0;
  }
}

.version-target-select {
  flex: 1;
  min-width: 0;
}

@media (max-width: 640px) {
  .version-candidate-main {
    align-items: flex-start;
    flex-direction: column;
  }
}

.footer-container {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
}

.footer-buttons {
  display: flex;
  gap: 8px;
}

.add-files-content {
  padding: 8px 0;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* Top Bar */
.top-action-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.auto-index-toggle {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-right: 4px;

  :deep(.ant-checkbox-wrapper) {
    font-size: 13px;
    color: var(--gray-600);
    font-weight: 500;
  }
}

.help-link-btn {
  color: var(--gray-600);
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0;

  &:hover {
    color: var(--main-color);
  }
}

.clean-preview-panel {
  margin-top: 14px;
  padding: 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-25);

  .clean-preview-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;

    .clean-preview-title {
      font-size: 14px;
      font-weight: 600;
      color: var(--gray-900);
    }

    .clean-preview-sub {
      flex: 1;
      font-size: 12px;
      color: var(--gray-500);
    }

    .clean-regenerate-btn {
      display: flex;
      align-items: center;
      gap: 4px;
    }
  }

  .clean-loading,
  .clean-empty,
  .clean-error {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 16px;
    color: var(--gray-600);
    font-size: 13px;
  }

  .clean-error {
    color: var(--color-error-700);
  }

  .clean-preview-body {
    .clean-preview-tabs {
      margin-bottom: 8px;
    }

    .clean-edit-area {
      font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
      font-size: 13px;
      line-height: 1.6;
    }

    .clean-preview-render {
      max-height: 360px;
      overflow: auto;
      padding: 12px;
      border: 1px solid var(--gray-150);
      border-radius: 6px;
      background: var(--gray-0);
    }
  }

  .clean-file-card {
    padding: 10px;
    margin-bottom: 10px;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);

    .clean-file-head {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;

      .clean-file-name {
        flex: 1;
        min-width: 0;
        font-size: 13px;
        font-weight: 600;
        color: var(--gray-900);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .clean-file-status {
        font-size: 12px;
        padding: 1px 8px;
        border-radius: 10px;

        &.idle {
          color: var(--gray-600);
          background: var(--gray-100);
        }
        &.loading {
          color: var(--color-info-700);
          background: var(--color-info-50);
        }
        &.done {
          color: var(--color-success-700);
          background: var(--color-success-50);
        }
        &.error {
          color: var(--color-error-700);
          background: var(--color-error-50);
        }
      }

      .clean-file-action {
        display: flex;
        align-items: center;
        gap: 4px;
      }
    }
  }
}

.custom-segmented {
  background-color: var(--gray-100);
  padding: 3px;

  .segmented-option {
    display: flex;
    justify-content: center;
    align-items: center;
    height: 32px;
    .option-text {
      margin-left: 6px;
    }
  }
}

/* Settings Panel */
.settings-panel {
  background-color: var(--gray-50);
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.setting-row {
  display: flex;
  flex-direction: column;
  gap: 8px;

  &.two-cols {
    flex-direction: row;
    gap: 20px;
  }

  .col-item {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0; // Fix flex overflow
  }
}

.setting-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--gray-700);
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-icon {
  color: var(--gray-400);
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    color: var(--main-color);
  }

  &.spinning {
    animation: spin 1s linear infinite;
    color: var(--main-color);
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.flex-row {
  display: flex;
  align-items: center;
  width: 100%;
}

.folder-select {
  flex: 1;
}

.folder-checkbox {
  margin-left: 12px;
  white-space: nowrap;
}

.ocr-engine-trigger {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  min-width: 0;
}

.ocr-engine-trigger-main {
  display: inline-flex;
  align-items: center;
  flex: 1 1 auto;
  min-width: 0;
  gap: 8px;
}

.ocr-engine-trigger-loading {
  flex: 0 0 auto;
  color: var(--main-color);
  animation: spin 1s linear infinite;
}

.ocr-engine-trigger-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ocr-engine-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 280px;
}

.ocr-engine-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--gray-100);
  border-radius: 8px;
  background: var(--gray-0);
  color: inherit;
  cursor: pointer;
  text-align: left;
}

.ocr-engine-option:hover:not(:disabled) {
  border-color: var(--main-color);
  background: color-mix(in srgb, var(--main-color) 6%, var(--gray-0));
}

.ocr-engine-option.selected {
  border-color: var(--main-color);
  background: color-mix(in srgb, var(--main-color) 8%, var(--gray-0));
}

.ocr-engine-option.disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.unavailable-ocr-options,
.unavailable-ocr-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.unavailable-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 4px 2px;
  border: none;
  background: transparent;
  color: var(--gray-500);
  cursor: pointer;
  font-size: 12px;
}

.unavailable-toggle:hover {
  color: var(--gray-800);
}

.ocr-engine-option-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.ocr-engine-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 500;
}

.ocr-engine-status {
  display: inline-flex;
  align-items: center;
  min-height: 18px;
  flex: none;
  font-size: 12px;
  line-height: 1;
}

.ocr-engine-status.status-local,
.ocr-engine-status.status-healthy,
.ocr-engine-status.status-configured {
  color: var(--color-success-700);
}

.ocr-engine-status.status-unavailable,
.ocr-engine-status.status-error {
  color: var(--color-error-700);
}

.ocr-engine-status.status-unhealthy,
.ocr-engine-status.status-timeout,
.ocr-engine-status.status-unknown,
.ocr-engine-status.status-checking {
  color: var(--color-warning-700);
}

.ocr-engine-desc {
  color: var(--gray-500);
  font-size: 12px;
  line-height: 1.4;
}

:global(.ocr-engine-popover .ant-popover-inner-content) {
  padding: 10px;
}

.param-description {
  font-size: 12px;
  color: var(--gray-400);
  margin: 4px 0 0 0;
  line-height: 1.4;
  display: flex;
  align-items: center;
  gap: 4px;

  .text-success {
    color: var(--color-success-500);
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .text-warning {
    color: var(--color-warning-500);
    display: flex;
    align-items: center;
    gap: 4px;
  }
}

/* Chunk Display Card */
.chunk-display-card {
  background: var(--gray-0);
  border: 1px solid var(--gray-300);
  border-radius: 6px;
  padding: 0 12px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  transition: all 0.2s;

  &:hover {
    border-color: var(--main-color);
    box-shadow: 0 0 0 2px var(--main-100);

    .edit-icon {
      color: var(--main-color);
    }
  }

  &.disabled {
    background: var(--gray-100);
    cursor: not-allowed;
    color: var(--gray-400);
    &:hover {
      border-color: var(--gray-300);
      box-shadow: none;
    }
  }
}

.chunk-info {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--gray-700);

  .divider {
    color: var(--gray-300);
    font-size: 10px;
  }

  b {
    font-weight: 600;
    color: var(--gray-900);
  }
}

.edit-icon {
  color: var(--gray-400);
  font-size: 14px;
}

/* Alerts */
.inline-alert {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 13px;

  &.warning {
    background: var(--color-warning-50);
    border: 1px solid var(--color-warning-200);
    color: var(--color-warning-700);
  }
}

/* Upload Area */
.upload-area {
  flex: 1;
}

.custom-dragger {
  :deep(.ant-upload-drag) {
    background: var(--gray-0);
    border-radius: 8px;
    border: 1px dashed var(--gray-300);
    transition: all 0.3s;

    &:hover {
      border-color: var(--main-color);
      background: var(--main-50);
    }
  }

  .ant-upload-drag-icon {
    font-size: 32px;
    color: var(--main-300);
    margin-bottom: 8px;
  }

  .ant-upload-text {
    font-size: 15px;
    color: var(--gray-800);
    margin-bottom: 4px;
  }

  .ant-upload-hint {
    font-size: 12px;
    color: var(--gray-500);
  }
}

.upload-item-wrap {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  :deep(.ant-upload-list-item) {
    flex: 1;
    min-width: 0;
  }
}

.upload-item-preview-btn {
  flex-shrink: 0;
  height: 24px;
  padding: 0 6px;
  font-size: 12px;
  color: var(--color-primary-700);

  &:hover {
    color: var(--color-primary-900) !important;
  }
}

.zip-tip {
  margin-top: 8px;
  font-size: 12px;
  color: var(--color-warning-600);
  background: var(--color-warning-50);
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
}

.upload-progress-card {
  margin-top: 8px;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  background: var(--gray-50);
  padding: 8px;
}

.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.progress-header-left {
  display: flex;
  flex-direction: row;
  gap: 6px;
  align-items: center;
  min-width: 0;
}

.progress-header-right {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.progress-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--gray-700);
  white-space: nowrap;
}

.progress-percent {
  font-size: 14px;
  font-weight: 700;
  color: var(--main-600);
}

.progress-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;

  &.inline-in-header {
    gap: 6px;
  }
}

.stat-pill {
  border-radius: 999px;
  padding: 1px 8px;
  font-size: 11px;
  line-height: 1.4;
  border: 1px solid var(--gray-300);
  background: var(--gray-100);
  color: var(--gray-600);

  &.uploading {
    background: var(--main-50);
    border-color: var(--main-200);
    color: var(--main-600);
  }

  &.queued {
    background: var(--gray-100);
    border-color: var(--gray-300);
    color: var(--gray-600);
  }

  &.success {
    background: var(--color-success-50);
    border-color: var(--color-success-200);
    color: var(--color-success-600);
  }

  &.error {
    background: var(--color-error-50);
    border-color: var(--color-error-200);
    color: var(--color-error-600);
  }
}

.progress-tip {
  margin-top: 6px;
  font-size: 11px;
  color: var(--gray-500);
}

.progress-details {
  border-top: 1px dashed var(--gray-200);
  padding-top: 6px;
}

.details-list {
  max-height: 160px;
  overflow-y: auto;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  background: var(--gray-0);
}

.detail-row {
  padding: 6px 8px;
  border-bottom: 1px solid var(--gray-100);

  &:last-child {
    border-bottom: none;
  }
}

.detail-name {
  font-size: 11px;
  color: var(--gray-700);
  font-weight: 500;
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.detail-error {
  margin-top: 2px;
  font-size: 11px;
  color: var(--color-error-600);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.toggle-progress-btn {
  color: var(--gray-500);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding-inline: 4px;

  &:hover {
    color: var(--main-600);
    background: var(--gray-100);
  }
}

/* Workspace Area */
.workspace-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.workspace-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.workspace-summary {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--gray-700);
  min-width: 0;
}

.workspace-current-path {
  max-width: 360px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
}

.workspace-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.workspace-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 320px;
  overflow-y: auto;
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  padding: 8px;
  background: var(--gray-0);
}

.workspace-item {
  display: flex;
  align-items: center;
  width: 100%;
  gap: 8px;
  min-height: 34px;
  padding: 6px 8px;
  border-radius: 6px;
  border: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background 0.2s;

  &:hover {
    background: var(--gray-50);
  }

  &.disabled {
    cursor: not-allowed;
    color: var(--gray-400);
  }
}

.workspace-directory {
  color: var(--gray-800);
}

.workspace-file-icon {
  flex-shrink: 0;
  color: var(--main-500);
}

.workspace-file-name {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: var(--gray-700);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-file-size {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--gray-500);
}

/* URL Area */
.url-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.url-input-wrapper {
  width: 100%;
}

.url-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 8px;
}

.url-hint {
  font-size: 12px;
  color: var(--gray-500);

  .warning-text {
    color: var(--color-warning-500);
    margin-left: 4px;
  }
}

.url-input {
  width: 100%;
  padding: 10px;
}

.add-url-btn {
  margin-left: 8px;
}

.url-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 300px;
  overflow-y: auto;
}

.url-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--gray-50);
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  transition: all 0.2s;

  &:hover {
    background: var(--gray-100);
    border-color: var(--main-300);
  }
}

.url-icon-wrapper {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.url-icon {
  color: var(--main-500);

  &.success {
    color: var(--color-success-500);
  }

  &.error {
    color: var(--color-error-500);
    cursor: help;
  }

  &.spinning {
    animation: spin 1s linear infinite;
    color: var(--main-500);
  }
}

.url-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.url-text {
  font-size: 13px;
  color: var(--gray-700);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.url-error-msg {
  font-size: 11px;
  color: var(--color-error-500);
  margin-top: 2px;
}

.remove-url-btn {
  color: var(--gray-400);
  flex-shrink: 0;

  &:hover {
    color: var(--color-error-500);
  }
}

.url-empty-tip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px;
  background: var(--gray-50);
  border: 1px dashed var(--gray-300);
  border-radius: 8px;
  color: var(--gray-500);
  font-size: 13px;
}

/* Conflict Files Panel */
.conflict-files-panel {
  border: 1px solid var(--gray-200);
  border-radius: 8px;
  overflow: hidden;
  background: var(--gray-0);
  margin-top: 4px;
}

.panel-header {
  background: var(--gray-50);
  padding: 8px 12px;
  font-size: 13px;
  font-weight: 500;
  color: var(--gray-700);
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid var(--gray-200);

  .icon-warning {
    color: var(--color-warning-500);
  }
}

.file-list-scroll {
  max-height: 280px;
  overflow-y: auto;
}

.conflict-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  border-bottom: 1px solid var(--gray-100);
  transition: background 0.2s;

  &:last-child {
    border-bottom: none;
  }

  &:hover {
    background: var(--gray-50);
  }
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 1;
  min-width: 0;
  font-size: 13px;

  .fname {
    font-weight: 500;
    color: var(--gray-800);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ftime {
    color: var(--gray-400);
    font-size: 12px;
    flex-shrink: 0;
  }
}

.file-actions {
  display: flex;
  gap: 4px;

  .action-btn {
    color: var(--gray-500);

    &:hover {
      color: var(--main-600);
      background: var(--main-50);
    }

    &.delete:hover {
      color: var(--color-error-500);
      background: var(--color-error-50);
    }
  }
}

.auto-index-params {
  margin-top: 8px;
  padding: 12px;
  background: var(--gray-0);
  border: 1px solid var(--gray-200);
  border-radius: 6px;
}

.setting-label .ant-checkbox {
  margin-right: 8px;
}

@media (max-width: 768px) {
  .top-action-bar {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
  }

  .auto-index-toggle {
    padding-right: 0;
  }

  .progress-header {
    flex-direction: column;
    gap: 8px;
  }

  .progress-header-right {
    width: 100%;
    justify-content: space-between;
  }
}
</style>
