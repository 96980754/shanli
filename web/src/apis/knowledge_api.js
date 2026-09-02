import {
  apiGet,
  apiPost,
  apiPut,
  apiDelete,
  apiAdminGet,
  apiAdminPost,
  apiAdminPut,
  apiAdminDelete,
  apiRequest
} from './base'

/**
 * 知识库管理API模块
 * 包含数据库管理、文档管理、查询接口等功能
 */

// =============================================================================
// === 数据库管理分组 ===
// =============================================================================

export const databaseApi = {
  /**
   * 获取所有知识库
   * @returns {Promise} - 知识库列表
   */
  getDatabases: async (categoryId = null) => {
    const query = categoryId ? `?category_id=${encodeURIComponent(categoryId)}` : ''
    return apiAdminGet(`/api/knowledge/databases${query}`)
  },

  /**
   * 创建知识库
   * @param {Object} databaseData - 知识库数据
   * @returns {Promise} - 创建结果
   */
  createDatabase: async (databaseData) => {
    return apiAdminPost('/api/knowledge/databases', databaseData)
  },

  /**
   * 获取知识库详细信息
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 知识库信息
   */
  getDatabaseInfo: async (kbId) => {
    return apiGet(`/api/knowledge/databases/${kbId}`)
  },

  getDatabaseAccess: async (kbId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/access`)
  },

  /**
   * 修复知识库文件统计
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 修复结果
   */
  repairDatabaseStats: async (kbId) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/stats/repair`, {})
  },

  /**
   * 更新知识库信息
   * @param {string} kbId - 知识库ID
   * @param {Object} updateData - 更新数据
   * @returns {Promise} - 更新结果
   */
  updateDatabase: async (kbId, updateData) => {
    return apiAdminPut(`/api/knowledge/databases/${kbId}`, updateData)
  },

  /**
   * 删除知识库
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 删除结果
   */
  deleteDatabase: async (kbId) => {
    return apiAdminDelete(`/api/knowledge/databases/${kbId}`)
  },

  /**
   * 使用 AI 生成或优化知识库描述
   * @param {string} name - 知识库名称
   * @param {string} currentDescription - 当前描述（可选）
   * @param {Array} fileList - 文件列表（可选）
   * @returns {Promise} - 生成结果
   */
  generateDescription: async (name, currentDescription = '', fileList = []) => {
    return apiAdminPost('/api/knowledge/generate-description', {
      name,
      current_description: currentDescription,
      file_list: fileList
    })
  },

  /**
   * AI 清洗排版：将排版混乱的文档重排版为结构清晰的规范 markdown
   * @param {string} kbId - 知识库 ID
   * @param {string} markdown - 原始 markdown（与 filePath 二选一）
   * @param {string} filename - 原文件名
   * @param {string} filePath - 已上传文件的 MinIO URL（与 markdown 二选一）
   * @returns {Promise} - { cleaned_markdown, filename }
   */
  cleanDocument: async (kbId, markdown = null, filename = null, filePath = null) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/documents/clean`, {
      markdown,
      filename,
      file_path: filePath
    })
  },

  /**
   * 批量 AI 清洗排版：对多个已上传文档并发重排版为规范 markdown
   * @param {string} kbId - 知识库 ID
   * @param {Array<{file_path: string, filename: string|null}>} items - 已上传文件项
   * @returns {Promise} - { results: [{file_path, cleaned_markdown, error}] }
   */
  cleanDocuments: async (kbId, items = []) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/documents/clean-batch`, { items })
  },

  /**
   * 清洗写回原格式：将清洗后的 markdown 按 filename 后缀写回 docx/xlsx 并上传
   * @param {string} kbId - 知识库 ID
   * @param {string} cleanedMarkdown - 清洗后（可能经用户编辑）的 markdown
   * @param {string} filename - 原文件名，后缀决定写回 docx/xlsx
   * @returns {Promise} - { file_path, content_hash, size }
   */
  cleanWriteback: async (kbId, { cleaned_markdown, filename } = {}) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/clean-writeback`, {
      cleaned_markdown,
      filename
    })
  },

  /**
   * 获取当前用户有权访问的知识库列表（用于智能体配置）
   * @returns {Promise} - 可访问的知识库列表
   */
  getAccessibleDatabases: async (categoryId = null) => {
    const query = categoryId ? `?category_id=${encodeURIComponent(categoryId)}` : ''
    return apiGet(`/api/knowledge/databases/accessible${query}`)
  },

  /**
   * 获取知识库权限矩阵
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 权限列表
   */
  getPermissions: async (kbId) => {
    return apiAdminGet(`/api/knowledge/databases/${kbId}/permissions`)
  },

  /**
   * 新增或更新知识库授权
   * @param {string} kbId - 知识库ID
   * @param {Object} permissionData - 授权数据
   * @returns {Promise} - 保存后的授权
   */
  upsertPermission: async (kbId, permissionData) => {
    return apiAdminPut(`/api/knowledge/databases/${kbId}/permissions`, permissionData)
  },

  /**
   * 删除知识库授权
   * @param {string} kbId - 知识库ID
   * @param {number} permissionId - 授权记录ID
   * @returns {Promise} - 删除结果
   */
  deletePermission: async (kbId, permissionId) => {
    return apiAdminDelete(`/api/knowledge/databases/${kbId}/permissions/${permissionId}`)
  }
}

export const categoryApi = {
  getCategories: async () => apiGet('/api/knowledge/categories'),
  createCategory: async (data) => apiAdminPost('/api/knowledge/categories', data),
  updateCategory: async (categoryId, data) =>
    apiAdminPut(`/api/knowledge/categories/${categoryId}`, data),
  deleteCategory: async (categoryId) => apiAdminDelete(`/api/knowledge/categories/${categoryId}`)
}

// =============================================================================
// === 文档管理分组 ===
// =============================================================================

const buildQuery = (params) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value))
    }
  })
  return query.toString()
}

export const documentApi = {
  /**
   * 分页获取知识库文档列表
   * @param {string} kbId - 知识库ID
   * @param {Object} params - 查询参数
   * @returns {Promise} - 文档列表
   */
  listDocuments: async (kbId, params = {}) => {
    const query = buildQuery(params)
    return apiGet(`/api/knowledge/databases/${kbId}/documents${query ? `?${query}` : ''}`)
  },

  searchDocuments: async (params = {}) => {
    const query = buildQuery(params)
    return apiGet(`/api/knowledge/documents/search${query ? `?${query}` : ''}`)
  },

  /**
   * 检查知识库中是否存在指定文件名或相对路径
   * @param {string} kbId - 知识库ID
   * @param {string} filename - 文件名或相对路径
   * @returns {Promise} - 存在性检查结果
   */
  documentExists: async (kbId, filename) => {
    const query = buildQuery({ filename })
    return apiGet(`/api/knowledge/databases/${kbId}/documents/exists?${query}`)
  },

  /**
   * 创建文件夹
   * @param {string} kbId - 知识库ID
   * @param {string} folderName - 文件夹名称
   * @param {string} parentId - 父文件夹ID
   * @returns {Promise} - 创建结果
   */
  createFolder: async (kbId, folderName, parentId = null) => {
    return apiPost(`/api/knowledge/databases/${kbId}/folders`, {
      folder_name: folderName,
      parent_id: parentId
    })
  },

  /**
   * 移动文件到其它文件夹（newParentId 为 null 表示移动到根目录）
   * @param {string} kbId - 知识库ID
   * @param {string} fileId - 文件ID
   * @param {string|null} newParentId - 目标文件夹ID
   * @returns {Promise} - 移动结果
   */
  moveFile: async (kbId, fileId, newParentId) => {
    return apiPut(`/api/knowledge/databases/${kbId}/documents/${fileId}/move`, {
      new_parent_id: newParentId
    })
  },

  /**
   * 重命名文件/文件夹（只传新的叶子名，不含路径分隔符；虚拟目录改名为级联重写前缀）
   * @param {string} kbId - 知识库ID
   * @param {string} fileId - 文件/文件夹ID
   * @param {string} filename - 新的文件名/文件夹名
   * @returns {Promise} - 重命名结果
   */
  renameDocument: async (kbId, fileId, filename) => {
    // fileId 可能是虚拟目录 id（形如 __virtual_folder__:root:poc资料/，含 / 和中文）。
    // 尾部的 / 在 URL 路径里无法作为 %2F 传递（服务器会提前解码成真实分隔符），
    // 因此去掉后再整体编码；后端会按规范补回尾部斜杠
    return apiPut(`/api/knowledge/databases/${kbId}/documents/${encodeURIComponent(fileId.replace(/\/$/, ''))}/rename`, { filename })
  },

  /**
   * 获取真实文件夹（is_folder）的祖先链（top-down，含目标自身），用于全库搜索深链进入文件夹目录
   * @param {string} kbId - 知识库ID
   * @param {string} folderId - 目标文件夹ID
   * @returns {Promise} - { folder_id, chain: [{file_id, filename}] }
   */
  getFolderChain: async (kbId, folderId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/folders/${folderId}/chain`)
  },

  /**
   * 添加文档到知识库
   * @param {string} kbId - 知识库ID
   * @param {Array} items - 文档列表
   * @param {Object} params - 处理参数
   * @returns {Promise} - 添加结果
   */
  addDocuments: async (kbId, items, params = {}) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents`, {
      items,
      params
    })
  },

  /**
   * 将已上传文件添加为知识库文档记录（不解析、不入库）
   * @param {string} kbId - 知识库ID
   * @param {Array} items - 已上传文件的 MinIO URL 列表
   * @param {Object} params - 添加参数
   * @returns {Promise} - 添加结果
   */
  addUploadedDocuments: async (kbId, items, params = {}) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/add`, {
      items,
      params
    })
  },

  createDocumentVersion: async (kbId, currentFileId, data) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/${currentFileId}/versions`, data)
  },

  getDocumentVersions: async (kbId, fileId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${fileId}/versions`)
  },

  getSourceVersions: async (kbId, fileIds) => {
    return apiPost(`/api/knowledge/databases/${kbId}/source-versions`, {
      file_ids: fileIds
    })
  },

  getDocumentValidationReport: async (kbId, candidateFileId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${candidateFileId}/validation-report`)
  },

  rejectDocumentValidationReport: async (kbId, reportId, data = {}) => {
    return apiPost(`/api/knowledge/databases/${kbId}/validation-reports/${reportId}/reject`, data)
  },

  getDocumentConflicts: async (kbId, candidateFileId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${candidateFileId}/conflicts`)
  },

  activateDocumentVersion: async (kbId, candidateFileId, data) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/${candidateFileId}/activate`, data)
  },

  /**
   * 获取文档信息
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - 文档信息
   */
  getDocumentInfo: async (kbId, docId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${docId}`)
  },

  /**
   * 获取文档基本信息
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - 文档基本信息
   */
  getDocumentBasicInfo: async (kbId, docId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${docId}/basic`)
  },

  /**
   * 获取文档解析内容和分块
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - 文档内容信息
   */
  getDocumentContent: async (kbId, docId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${docId}/content`)
  },

  /**
   * 获取 Word/Excel 的可编辑结构化内容
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - { type: 'docx'|'xlsx', blocks|sheets }
   */
  getOfficeContent: async (kbId, docId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${docId}/office-content`)
  },

  /**
   * 保存编辑后的 Word/Excel 并重新入库（删旧版）
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @param {Object} data - { content_type, blocks|sheets, filename }
   * @returns {Promise} - { message, file_id }
   */
  saveEditedDocument: async (kbId, docId, data) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/documents/${docId}/save-edited`, data)
  },

  /**
   * 从已上传的 MinIO file_path 提取 Word/Excel 可编辑结构（未入库）
   * @param {string} kbId - 知识库ID
   * @param {string} filePath - MinIO URL
   * @param {string} filename - 文件名
   * @returns {Promise} - { type: 'docx'|'xlsx', blocks|sheets }
   */
  getOfficeContentByPath: async (kbId, filePath, filename = '') => {
    return apiPost(`/api/knowledge/databases/${kbId}/office-extract`, {
      file_path: filePath,
      filename
    })
  },

  /**
   * 将编辑后的 Word/Excel 内容写回 .docx/.xlsx 并上传 MinIO
   * @param {string} kbId - 知识库ID
   * @param {Object} data - { content_type, blocks|sheets, filename }
   * @returns {Promise} - { file_path, content_hash, size }
   */
  officeWriteback: async (kbId, data) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/office-writeback`, data)
  },

  /**
   * 删除文档
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - 删除结果
   */
  deleteDocument: async (kbId, docId) => {
    return apiDelete(`/api/knowledge/databases/${kbId}/documents/${docId}`)
  },

  /**
   * 批量删除文档
   * @param {string} kbId - 知识库ID
   * @param {Array} fileIds - 文件ID列表
   * @returns {Promise} - 批量删除结果
   */
  batchDeleteDocuments: async (kbId, fileIds) => {
    return apiRequest(
      `/api/knowledge/databases/${kbId}/documents/batch`,
      {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(fileIds)
      },
      true,
      'json'
    )
  },

  /**
   * 下载文档
   * @param {string} kbId - 知识库ID
   * @param {string} docId - 文档ID
   * @returns {Promise} - Response对象
   */
  downloadDocument: async (kbId, docId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${docId}/download`, {}, true, 'blob')
  },

  /**
   * 手动触发文档解析
   * @param {string} kbId - 知识库ID
   * @param {Array} fileIds - 文件ID列表
   * @returns {Promise} - 解析任务结果
   */
  parseDocuments: async (kbId, fileIds) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/documents/parse`, fileIds)
  },

  /**
   * 手动触发全部待解析文档解析
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 解析任务结果
   */
  parsePendingDocuments: async (kbId) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/documents/parse-pending`, {})
  },

  /**
   * 手动触发文档入库
   * @param {string} kbId - 知识库ID
   * @param {Array} fileIds - 文件ID列表
   * @param {Object} params - 处理参数
   * @returns {Promise} - 入库任务结果
   */
  indexDocuments: async (kbId, fileIds, params = {}) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/documents/index`, {
      file_ids: fileIds,
      params
    })
  },

  /**
   * 手动触发全部待入库文档入库
   * @param {string} kbId - 知识库ID
   * @param {Object} params - 处理参数
   * @returns {Promise} - 入库任务结果
   */
  indexPendingDocuments: async (kbId, params = {}) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/documents/index-pending`, {
      params
    })
  },
  retryReplacementCleanup: async (kbId, fileId) => {
    return apiPost(
      `/api/knowledge/databases/${kbId}/documents/${fileId}/replacement-cleanup/retry`,
      {}
    )
  },
  getCleaningPreview: async (kbId, fileId) => {
    return apiAdminGet(`/api/knowledge/databases/${kbId}/documents/${fileId}/cleaning`)
  },
  saveCleaningDraft: async (kbId, fileId, content, version) => {
    return apiRequest(
      `/api/knowledge/databases/${kbId}/documents/${fileId}/cleaning/draft`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content, version })
      },
      true,
      'json'
    )
  },
  regenerateCleaningDraft: async (kbId, fileId, version, useAi = null) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/${fileId}/cleaning/regenerate`, {
      version,
      use_ai: useAi
    })
  },
  confirmCleaningDraft: async (kbId, fileId, version) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/${fileId}/cleaning/confirm`, {
      version
    })
  },
  cancelCleaningDraft: async (kbId, fileId, version) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/${fileId}/cleaning/cancel`, {
      version
    })
  },
  getEnrichment: async (kbId, fileId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${fileId}/enrichment`)
  },
  generateEnrichment: async (kbId, fileId, components, overwriteManual = false) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/${fileId}/enrichment/generate`, {
      components,
      overwrite_manual: overwriteManual
    })
  },
  batchGenerateEnrichment: async (kbId, fileIds, components, overwriteManual = false) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/enrichment/generate`, {
      file_ids: fileIds,
      components,
      overwrite_manual: overwriteManual
    })
  },
  updateSummary: async (kbId, fileId, text, version) => {
    return apiRequest(
      `/api/knowledge/databases/${kbId}/documents/${fileId}/enrichment/summary`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, version })
      },
      true,
      'json'
    )
  },
  updateKeywords: async (kbId, fileId, values, version) => {
    return apiRequest(
      `/api/knowledge/databases/${kbId}/documents/${fileId}/enrichment/keywords`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values, version })
      },
      true,
      'json'
    )
  },
  updateTags: async (kbId, fileId, values, version) => {
    return apiRequest(
      `/api/knowledge/databases/${kbId}/documents/${fileId}/enrichment/tags`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values, version })
      },
      true,
      'json'
    )
  },
  getDocumentQAs: async (kbId, fileId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${fileId}/qa`)
  },
  getDocumentQA: async (kbId, fileId, qaId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${fileId}/qa/${qaId}`)
  },
  generateDocumentQAs: async (kbId, fileId, sourceChunkIds = [], replaceGenerated = false) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/${fileId}/qa/generate`, {
      source_chunk_ids: sourceChunkIds,
      replace_generated: replaceGenerated
    })
  },
  getDocumentQAGenerationTask: async (kbId, fileId, taskId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/documents/${fileId}/qa/tasks/${taskId}`)
  },
  batchGenerateDocumentQAs: async (
    kbId,
    fileIds,
    sourceChunkIds = [],
    replaceGenerated = false
  ) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/qa/generate`, {
      file_ids: fileIds,
      source_chunk_ids: sourceChunkIds,
      replace_generated: replaceGenerated
    })
  },
  createDocumentQA: async (kbId, fileId, payload) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/${fileId}/qa`, payload)
  },
  updateDocumentQA: async (kbId, fileId, qaId, payload) => {
    return apiRequest(
      `/api/knowledge/databases/${kbId}/documents/${fileId}/qa/${qaId}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      },
      true,
      'json'
    )
  },
  confirmDocumentQA: async (kbId, fileId, qaId, version) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/${fileId}/qa/${qaId}/confirm`, {
      version
    })
  },
  batchConfirmDocumentQAs: async (kbId, fileId, items) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/${fileId}/qa/confirm`, {
      items
    })
  },
  rejectDocumentQA: async (kbId, fileId, qaId, version) => {
    return apiPost(`/api/knowledge/databases/${kbId}/documents/${fileId}/qa/${qaId}/reject`, {
      version
    })
  },
  deleteDocumentQA: async (kbId, fileId, qaId, version) => {
    return apiRequest(
      `/api/knowledge/databases/${kbId}/documents/${fileId}/qa/${qaId}?version=${encodeURIComponent(version)}`,
      { method: 'DELETE' },
      true,
      'json'
    )
  }
}

// =============================================================================
// === 图谱构建分组 ===
// =============================================================================

function graphBuildUrl(kbId, action) {
  return `/api/knowledge/databases/${kbId}/graph-build/${action}`
}

export const graphBuildApi = {
  getStatus: async (kbId) => {
    return apiAdminGet(graphBuildUrl(kbId, 'status'))
  },

  configure: async (kbId, data) => {
    return apiAdminPost(graphBuildUrl(kbId, 'config'), data)
  },

  startIndex: async (kbId, batchSize = 20) => {
    return apiAdminPost(graphBuildUrl(kbId, 'index'), {
      batch_size: batchSize
    })
  },

  reset: async (kbId, data) => {
    return apiAdminPost(graphBuildUrl(kbId, 'reset'), data)
  }
}

// =============================================================================
// === 思维导图分组 ===
// =============================================================================

export const mindmapApi = {
  getDatabases: async () => {
    return apiAdminGet('/api/knowledge/mindmap/databases')
  },

  getDatabaseFiles: async (kbId) => {
    return apiAdminGet(`/api/knowledge/databases/${kbId}/mindmap/files`)
  },

  generateMindmap: async (kbId, fileIds = [], userPrompt = '', incremental = false) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/mindmap/generate`, {
      file_ids: fileIds,
      user_prompt: userPrompt,
      incremental
    })
  },

  getByDatabase: async (kbId) => {
    return apiAdminGet(`/api/knowledge/databases/${kbId}/mindmap`)
  },

  getDiff: async (kbId) => {
    return apiAdminGet(`/api/knowledge/databases/${kbId}/mindmap/diff`)
  }
}

// =============================================================================
// === 查询分组 ===
// =============================================================================

export const queryApi = {
  globalSearch: async (query, limit = 10) => {
    return apiPost('/api/knowledge/search', { query, limit })
  },
  createHandoff: async (query, disposition = null) =>
    apiPost('/api/knowledge/handoffs', { query, disposition }),

  /**
   * 查询知识库
   * @param {string} kbId - 知识库ID
   * @param {string} query - 查询文本
   * @param {Object} meta - 查询参数
   * @returns {Promise} - 查询结果
   */
  queryKnowledgeBase: async (kbId, query, meta = {}) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/query`, {
      query,
      meta
    })
  },

  /**
   * 测试查询知识库
   * @param {string} kbId - 知识库ID
   * @param {string} query - 查询文本
   * @param {Object} meta - 查询参数
   * @returns {Promise} - 测试结果
   */
  queryTest: async (kbId, query, meta = {}) => {
    return apiPost(`/api/knowledge/databases/${kbId}/query-test`, {
      query,
      meta
    })
  },

  preview: async (kbId, query, meta = {}, generateAnswer = true) => {
    return apiPost(`/api/knowledge/databases/${kbId}/preview`, {
      query,
      meta,
      generate_answer: generateAnswer
    })
  },

  /**
   * 获取知识库查询参数
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 查询参数
   */
  getKnowledgeBaseQueryParams: async (kbId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/query-params`)
  },

  /**
   * 更新知识库查询参数
   * @param {string} kbId - 知识库ID
   * @param {Object} params - 查询参数
   * @returns {Promise} - 更新结果
   */
  updateKnowledgeBaseQueryParams: async (kbId, params) => {
    return apiPut(`/api/knowledge/databases/${kbId}/query-params`, params)
  },

  /**
   * 生成知识库的测试问题
   * @param {string} kbId - 知识库ID
   * @param {number} count - 生成问题数量，默认10
   * @returns {Promise} - 生成的问题列表
   */
  generateSampleQuestions: async (kbId, count = 10) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/sample-questions`, {
      count
    })
  },

  /**
   * 获取知识库的测试问题
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 问题列表
   */
  getSampleQuestions: async (kbId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/sample-questions`)
  }
}

export const documentBrowseApi = {
  search: async (params = {}) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') query.append(key, value)
    })
    return apiGet(`/api/knowledge/documents/search?${query}`)
  },
  hot: async (limit = 10) => apiGet(`/api/knowledge/documents/hot?limit=${limit}`)
}

// =============================================================================
// === 文件管理分组 ===
// =============================================================================

export const fileApi = {
  /**
   * 抓取 URL 内容
   * @param {string} url - 目标 URL
   * @param {string} kbId - 知识库 ID
   * @returns {Promise} - 抓取结果
   */
  fetchUrl: async (url, kbId = null) => {
    return apiAdminPost('/api/knowledge/files/fetch-url', {
      url,
      kb_id: kbId
    })
  },

  /**
   * 从工作区导入文件到知识库 MinIO 暂存区
   * @param {string} kbId - 知识库 ID
   * @param {Array<string>} paths - 工作区文件路径
   * @returns {Promise} - 导入结果
   */
  importWorkspaceFiles: async (kbId, paths) => {
    return apiPost(`/api/knowledge/files/import-workspace`, {
      kb_id: kbId,
      paths
    })
  },

  /**
   * 上传文件
   * @param {File} file - 文件对象
   * @param {string} kbId - 知识库ID（可选）
   * @returns {Promise} - 上传结果
   */
  uploadFile: async (file, kbId = null, options = {}) => {
    const formData = new FormData()
    formData.append('file', file)

    const query = new URLSearchParams()
    if (kbId) query.set('kb_id', kbId)
    if (options.parentId) query.set('parent_id', options.parentId)
    query.set('duplicate_strategy', options.duplicateStrategy || 'prompt')
    if (options.replaceFileId) query.set('replace_file_id', options.replaceFileId)
    const qs = query.toString()
    const url = qs ? `/api/knowledge/files/upload?${qs}` : '/api/knowledge/files/upload'

    return apiPost(url, formData)
  },

  /**
   * 重试替换版本清理任务
   * @param {string} kbId - 知识库 ID
   * @param {string} fileId - 文件 ID
   * @returns {Promise} - 重试结果
   */
  retryReplacementCleanup: async (kbId, fileId) => {
    return apiAdminPost(
      `/api/knowledge/databases/${kbId}/documents/${fileId}/replacement-cleanup/retry`,
      {}
    )
  },

  /**
   * 获取支持的文件类型
   * @returns {Promise} - 文件类型列表
   */
  getSupportedFileTypes: async () => {
    return apiGet('/api/knowledge/files/supported-types')
  },

  /**
   * 上传文件夹（zip格式）
   * @param {File} file - zip文件
   * @param {string} kbId - 知识库ID
   * @returns {Promise} - 上传结果
   */
  uploadFolder: async (file, kbId) => {
    const formData = new FormData()
    formData.append('file', file)

    // 使用 apiRequest 直接发送 FormData，但使用统一的错误处理
    return apiRequest(
      `/api/knowledge/files/upload-folder?kb_id=${kbId}`,
      {
        method: 'POST',
        body: formData
        // 不设置 Content-Type，让浏览器自动设置 boundary
      },
      true,
      'json'
    ) // 需要认证，期望JSON响应 // i18n-ignore
  },

  /**
   * 处理文件夹（异步处理zip文件）
   * @param {Object} data - 处理参数
   * @param {string} data.file_path - 已上传的zip文件路径
   * @param {string} data.kb_id - 知识库ID
   * @param {string} data.content_hash - 文件内容哈希
   * @returns {Promise} - 处理任务结果
   */
  processFolder: async ({ file_path, kb_id, content_hash }) => {
    return apiAdminPost('/api/knowledge/files/process-folder', {
      file_path,
      kb_id,
      content_hash
    })
  }
}

// =============================================================================
// === 产品参照图分组（图搜参数：MinIO public/{kb_id}/product-images/） ===
// =============================================================================

export const referenceImageApi = {
  /**
   * 列出知识库的产品参照图及索引状态
   * @param {string} kbId - 知识库 ID
   */
  list: async (kbId) => {
    return apiAdminGet(`/api/knowledge/databases/${kbId}/product-images`)
  },

  /**
   * 跨库聚合列出全部产品参照图（产品图库管理页使用）
   */
  listAll: async () => {
    return apiAdminGet('/api/knowledge/product-images')
  },

  /**
   * 上传产品参照图（每款产品一张，文件名即产品名）
   * @param {string} kbId - 知识库 ID
   * @param {Array<File>} files - 图片文件列表
   */
  upload: async (kbId, files) => {
    const formData = new FormData()
    files.forEach((file) => formData.append('files', file))
    return apiAdminPost(`/api/knowledge/databases/${kbId}/product-images`, formData)
  },

  /**
   * 删除单个产品参照图（含 Milvus 特征向量）
   * @param {string} kbId - 知识库 ID
   * @param {string} product - 产品名
   */
  remove: async (kbId, product) => {
    return apiAdminDelete(
      `/api/knowledge/databases/${kbId}/product-images/${encodeURIComponent(product)}`
    )
  },

  /**
   * 重建知识库产品参照图索引（调用视觉特征模型批量向量化）
   * @param {string} kbId - 知识库 ID
   */
  rebuild: async (kbId) => {
    return apiAdminPost(`/api/knowledge/databases/${kbId}/product-images/rebuild`, {})
  }
}

// =============================================================================
// === 知识库类型分组 ===
// =============================================================================

export const typeApi = {
  /**
   * 获取支持的知识库类型
   * @returns {Promise} - 知识库类型列表
   */
  getKnowledgeBaseTypes: async () => {
    return apiAdminGet('/api/knowledge/types')
  },

  /**
   * 获取支持的知识库分块策略
   * @returns {Promise} - 分块策略列表
   */
  getChunkPresets: async () => {
    return apiAdminGet('/api/knowledge/chunk-presets')
  },

  /**
   * 获取知识库统计信息
   * @returns {Promise} - 统计信息
   */
  getStatistics: async () => {
    return apiAdminGet('/api/knowledge/stats')
  }
}

// =============================================================================
// === RAG评估分组 ===
// =============================================================================

export const evaluationApi = {
  uploadDataset: async (kbId, file, metadata = {}) => {
    const formData = new FormData()
    formData.append('file', file)
    formData.append('name', metadata.name || '')
    formData.append('description', metadata.description || '')

    return apiAdminPost(`/api/evaluation/databases/${kbId}/datasets/upload`, formData)
  },

  listDatasets: async (kbId) => {
    return apiAdminGet(`/api/evaluation/databases/${kbId}/datasets`)
  },

  getDataset: async (kbId, datasetId, page = 1, pageSize = 50) => {
    const params = new URLSearchParams({
      page: page.toString(),
      page_size: pageSize.toString()
    })
    return apiAdminGet(`/api/evaluation/databases/${kbId}/datasets/${datasetId}?${params}`)
  },

  deleteDataset: async (datasetId) => {
    return apiAdminDelete(`/api/evaluation/datasets/${datasetId}`)
  },

  downloadDataset: async (datasetId) => {
    return apiAdminGet(`/api/evaluation/datasets/${datasetId}/download`, {}, 'blob')
  },

  generateDataset: async (kbId, params) => {
    return apiAdminPost(`/api/evaluation/databases/${kbId}/datasets/generate`, params)
  },

  runEvaluation: async (kbId, params) => {
    return apiAdminPost(`/api/evaluation/databases/${kbId}/runs`, params)
  },

  listRuns: async (kbId) => {
    return apiAdminGet(`/api/evaluation/databases/${kbId}/runs`)
  },

  getRunResults: async (kbId, runId, params = {}) => {
    const queryParams = new URLSearchParams()

    if (params.page) queryParams.append('page', params.page)
    if (params.pageSize) queryParams.append('page_size', params.pageSize)
    if (params.errorOnly !== undefined) queryParams.append('error_only', params.errorOnly)

    const url = `/api/evaluation/databases/${kbId}/runs/${runId}${queryParams.toString() ? '?' + queryParams.toString() : ''}`
    return apiAdminGet(url)
  },

  deleteRun: async (kbId, runId) => {
    return apiAdminDelete(`/api/evaluation/databases/${kbId}/runs/${runId}`)
  }
}

export const knowledgeConflictApi = {
  list: async (kbId, status = '') => {
    const query = status ? `?status=${encodeURIComponent(status)}` : ''
    return apiGet(`/api/knowledge/databases/${kbId}/conflicts${query}`)
  },
  get: async (kbId, conflictId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/conflicts/${conflictId}`)
  },
  evaluate: async (kbId, assertion) => {
    return apiPost(`/api/knowledge/databases/${kbId}/assertions/evaluate`, assertion)
  },
  resolve: async (kbId, conflictId, resolution) => {
    return apiPost(`/api/knowledge/databases/${kbId}/conflicts/${conflictId}/resolve`, resolution)
  },
  retryPublish: async (kbId, conflictId) => {
    return apiPost(`/api/knowledge/databases/${kbId}/conflicts/${conflictId}/publish/retry`, {})
  },
  batchResolve: async (kbId, items) => {
    return apiPost(`/api/knowledge/databases/${kbId}/conflicts/batch-resolve`, { items })
  },
  listEntityLinkCandidates: async (kbId) => {
    return apiGet(`/api/knowledge/databases/${kbId}/entity-link-candidates`)
  }
}
