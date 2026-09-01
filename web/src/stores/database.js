import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { i18n } from '@/i18n'
import { databaseApi, documentApi, queryApi } from '@/apis/knowledge_api'
import { useTaskerStore } from '@/stores/tasker'
import { useUserStore } from '@/stores/user'
import { useRouter } from 'vue-router'
import { parseToShanghai } from '@/utils/time'
import { canSelectFile, isProcessingFile } from '@/utils/knowledge_file_policy'
import { resolveDocumentParentId } from '@/utils/documentFileNavigation'

export const useDatabaseStore = defineStore('database', () => {
  const router = useRouter()
  const taskerStore = useTaskerStore()
  const userStore = useUserStore()

  // State
  const databases = ref([])
  const database = ref({})
  const kbId = ref(null)
  const fileDetailFileId = ref(null)
  const documentFiles = ref([])
  const folderBreadcrumbs = ref([{ file_id: null, filename: i18n.global.t('db.folderAllFiles'), path_prefix: '' }])

  const queryParams = ref([])
  const meta = reactive({})
  const selectedRowKeys = ref([])
  const fileBrowser = reactive({
    loading: false,
    parentId: null,
    page: 1,
    pageSize: 100,
    total: 0,
    hasMore: false,
    pathPrefix: '',
    status: 'all',
    recursive: false
  })

  const state = reactive({
    listLoading: false,
    creating: false,
    databaseLoading: false,
    lock: false,
    fileDetailModalVisible: false,
    batchDeleting: false,
    chunkLoading: false,
    autoRefresh: false,
    queryParamsLoading: false,
    rightPanelVisible: true
  })

  let refreshInterval = null
  let autoRefreshSource = null // Tracks whether auto-refresh was user-triggered or automatic
  let autoRefreshManualOverride = false // Indicates user explicitly disabled auto-refresh
  let fileBrowserContextId = 0

  function setCurrentFileMap(items = []) {
    database.value = {
      ...database.value,
      files: Object.fromEntries(items.map((item) => [item.file_id, item]))
    }
  }

  function resetFileBrowser() {
    fileBrowserContextId += 1
    documentFiles.value = []
    folderBreadcrumbs.value = [{ file_id: null, filename: i18n.global.t('db.folderAllFiles'), path_prefix: '' }]
    selectedRowKeys.value = []
    Object.assign(fileBrowser, {
      loading: false,
      parentId: null,
      page: 1,
      pageSize: 100,
      total: 0,
      hasMore: false,
      pathPrefix: '',
      status: 'all',
      recursive: false
    })
    setCurrentFileMap([])
  }

  // Actions
  // 管理员获取所有知识库，普通用户获取有权限访问的知识库
  async function loadDatabases(categoryId = null) {
    state.listLoading = true
    try {
      const data = userStore.isAdmin
        ? await databaseApi.getDatabases(categoryId)
        : await databaseApi.getAccessibleDatabases(categoryId)
      const list = data?.databases || []
      databases.value = list.sort((a, b) => {
        const timeA = parseToShanghai(a.created_at)
        const timeB = parseToShanghai(b.created_at)
        if (!timeA && !timeB) return 0
        if (!timeA) return 1
        if (!timeB) return -1
        return timeB.valueOf() - timeA.valueOf() // 降序排列，最新的在前面 // i18n-ignore
      })
    } catch (error) {
      console.error('加载数据库列表失败:', error) // i18n-ignore
      if (error.message.includes('权限')) { // i18n-ignore
        message.error(i18n.global.t('db.messages.noPermissionToAccess'))
      }
      throw error
    } finally {
      state.listLoading = false
    }
  }

  async function createDatabase(formData) {
    // 验证
    if (!formData.database_name?.trim()) {
      message.error(i18n.global.t('db.messages.nameRequired'))
      return false
    }

    if (!formData.kb_type) {
      message.error(i18n.global.t('db.messages.selectType'))
      return false
    }

    if (!formData.category_id) {
      message.error(i18n.global.t('db.messages.selectCategory'))
      return false
    }

    state.creating = true
    try {
      const data = await databaseApi.createDatabase(formData)
      message.success(i18n.global.t('db.messages.created'))
      await loadDatabases() // 刷新列表 // i18n-ignore
      return data
    } catch (error) {
      console.error('创建数据库失败:', error) // i18n-ignore
      message.error(error.message || i18n.global.t('db.messages.createFailed'))
      throw error
    } finally {
      state.creating = false
    }
  }

  async function getDatabaseInfo(id, skipQueryParams = false, isBackground = false) {
    const kbIdValue = id || kbId.value
    if (!kbIdValue) return

    if (!isBackground) {
      state.lock = true
      state.databaseLoading = true
    }
    try {
      const data = await databaseApi.getDatabaseInfo(kbIdValue)
      const currentFiles = database.value.files || {}
      database.value = { ...data, files: data?.files || currentFiles }
      ensureAutoRefreshForProcessing(data?.files, data?.stats)

      // Only load query parameters if explicitly requested or if not loaded yet
      if (!skipQueryParams && queryParams.value.length === 0) {
        await loadQueryParams(kbIdValue)
      }
    } catch (error) {
      console.error(error)
      message.error(error.message || i18n.global.t('db.messages.getInfoFailed'))
    } finally {
      if (!isBackground) {
        state.lock = false
        state.databaseLoading = false
      }
    }
  }

  async function updateDatabaseInfo(formData) {
    try {
      state.lock = true
      await databaseApi.updateDatabase(kbId.value, formData)
      message.success(i18n.global.t('db.messages.updateSuccess'))
      await getDatabaseInfo() // Load query params after updating database info
    } catch (error) {
      console.error(error)
      message.error(error.message || i18n.global.t('db.messages.updateFailed'))
    } finally {
      state.lock = false
    }
  }

  function deleteDatabase() {
    Modal.confirm({
      title: i18n.global.t('db.deleteConfirm.title'),
      content: i18n.global.t('db.deleteConfirm.contentThis'),
      okText: i18n.global.t('common.confirm'),
      cancelText: i18n.global.t('common.cancel'),
      onOk: async () => {
        state.lock = true
        try {
          const data = await databaseApi.deleteDatabase(kbId.value)
          message.success(data.message || i18n.global.t('common.deleteSuccess'))
          router.push({ path: '/extensions', query: { tab: 'knowledge' } })
        } catch (error) {
          console.error(error)
          message.error(error.message || i18n.global.t('common.deleteFailed'))
        } finally {
          state.lock = false
        }
      }
    })
  }

  async function deleteFile(fileId) {
    state.lock = true
    try {
      await documentApi.deleteDocument(kbId.value, fileId)
      await getDatabaseInfo(undefined, true) // Skip query params for file deletion
      await loadDocumentFiles({ isBackground: true })
    } catch (error) {
      console.error(error)
      message.error(error.message || i18n.global.t('common.deleteFailed'))
      throw error
    } finally {
      state.lock = false
    }
  }

  function handleDeleteFile(fileId) {
    Modal.confirm({
      title: i18n.global.t('db.deleteFileTitle'),
      content: i18n.global.t('db.deleteFileConfirm'),
      okText: i18n.global.t('common.confirm'),
      cancelText: i18n.global.t('common.cancel'),
      onOk: () => deleteFile(fileId)
    })
  }

  async function moveFile(fileId, newParentId) {
    state.lock = true
    try {
      await documentApi.moveFile(kbId.value, fileId, newParentId)
      await loadDocumentFiles({ isBackground: true })
    } catch (error) {
      console.error(error)
      message.error(error.message || i18n.global.t('db.messages.moveFailed'))
      throw error
    } finally {
      state.lock = false
    }
  }

  async function renameFile(fileId, newName) {
    state.lock = true
    try {
      await documentApi.renameDocument(kbId.value, fileId, newName)
      await loadDocumentFiles({ isBackground: true })
    } catch (error) {
      console.error(error)
      message.error(error.message || i18n.global.t('db.messages.renameFailed'))
      throw error
    } finally {
      state.lock = false
    }
  }

  function handleBatchDelete() {
    const files = database.value.files || {}
    const validFileIds = selectedRowKeys.value.filter((fileId) => {
      const file = files[fileId]
      return canSelectFile(file)
    })

    if (validFileIds.length === 0) {
      message.info(i18n.global.t('db.messages.noDeletableFiles'))
      return
    }

    Modal.confirm({
      title: i18n.global.t('db.batchDeleteTitle'),
      content: i18n.global.t('db.batchDeleteConfirm', { count: validFileIds.length }),
      okText: i18n.global.t('common.confirm'),
      cancelText: i18n.global.t('common.cancel'),
      onOk: async () => {
        state.batchDeleting = true
        let successCount = 0
        let failureCount = 0
        let processedCount = 0
        const totalCount = validFileIds.length
        const progressKey = `batch-delete-${Date.now()}`
        message.loading({
          content: i18n.global.t('db.deletingFilesProgress', { current: 0, total: totalCount }),
          key: progressKey,
          duration: 0
        })

        try {
          const CHUNK_SIZE = 50
          for (let i = 0; i < totalCount; i += CHUNK_SIZE) {
            const chunk = validFileIds.slice(i, i + CHUNK_SIZE)

            try {
              const res = await documentApi.batchDeleteDocuments(kbId.value, chunk)
              successCount += res.deleted_count || 0
              if (res.failed_items) {
                failureCount += res.failed_items.length
              }
            } catch (err) {
              console.error(`删除批次 ${i / CHUNK_SIZE + 1} 失败:`, err) // i18n-ignore
              failureCount += chunk.length
            } finally {
              processedCount += chunk.length
              message.loading({
                content: i18n.global.t('db.deletingFilesProgress', {
                  current: processedCount,
                  total: totalCount
                }),
                key: progressKey,
                duration: 0
              })
            }
          }

          message.destroy(progressKey)
          if (successCount > 0 && failureCount === 0) {
            message.success(i18n.global.t('db.messages.deleteCountSuccess', { count: successCount }))
          } else if (successCount > 0 && failureCount > 0) {
            message.warning(
              i18n.global.t('db.messages.deletePartialFailed', {
                success: successCount,
                failed: failureCount
              })
            )
          } else if (failureCount > 0) {
            message.error(i18n.global.t('db.messages.deleteFailedCount', { count: failureCount }))
          }

          selectedRowKeys.value = []
          await getDatabaseInfo(undefined, true) // Skip query params for batch deletion
          await loadDocumentFiles({ isBackground: true })
        } catch (error) {
          message.destroy(progressKey)
          console.error('批量删除出错:', error) // i18n-ignore
          message.error(error.message || i18n.global.t('db.messages.batchDeleteError'))
        } finally {
          state.batchDeleting = false
        }
      }
    })
  }

  function enableAutoRefresh(source = 'auto') {
    if (autoRefreshManualOverride && source === 'auto') {
      return
    }

    if (!state.autoRefresh) {
      state.autoRefresh = true
      autoRefreshSource = source
      autoRefreshManualOverride = false
      startAutoRefresh()
      return
    }

    if (source === 'auto' && autoRefreshSource !== 'manual') {
      autoRefreshSource = 'auto'
    }
  }

  function ensureAutoRefreshForProcessing(filesMap, stats = null) {
    if (Number(stats?.processing_count || 0) > 0) {
      enableAutoRefresh('auto')
      return true
    }

    const files = Array.isArray(filesMap) ? filesMap : Object.values(filesMap || {})
    const hasPending = files.some((file) => isProcessingFile(file))
    if (hasPending) {
      enableAutoRefresh('auto')
    } else if (autoRefreshSource === 'auto' && state.autoRefresh) {
      state.autoRefresh = false
      autoRefreshSource = null
      autoRefreshManualOverride = false
      stopAutoRefresh()
    }
    return hasPending
  }

  async function loadDocumentFiles(options = {}) {
    const kbIdValue = options.kbId || kbId.value
    if (!kbIdValue) return

    const nextStatus = options.status ?? fileBrowser.status
    const nextRecursive = options.recursive ?? nextStatus !== 'all'
    const nextParentId = resolveDocumentParentId(options.parentId, fileBrowser.parentId, nextRecursive)
    const nextPathPrefix = nextRecursive ? '' : (options.pathPrefix ?? fileBrowser.pathPrefix)
    const nextPage = Number(options.page ?? fileBrowser.page) || 1
    const nextPageSize = Number(options.pageSize ?? fileBrowser.pageSize) || 100
    const contextChanged =
      fileBrowser.parentId !== nextParentId ||
      fileBrowser.page !== nextPage ||
      fileBrowser.pageSize !== nextPageSize ||
      fileBrowser.pathPrefix !== nextPathPrefix ||
      fileBrowser.status !== nextStatus ||
      fileBrowser.recursive !== nextRecursive
    if (contextChanged) fileBrowserContextId += 1
    const contextId = fileBrowserContextId

    Object.assign(fileBrowser, {
      parentId: nextParentId,
      page: nextPage,
      pageSize: nextPageSize,
      pathPrefix: nextPathPrefix,
      status: nextStatus,
      recursive: nextRecursive
    })

    if (!options.isBackground) {
      fileBrowser.loading = true
    }

    try {
      const params = {
        page: nextPage,
        page_size: nextPageSize,
        status: nextStatus,
        recursive: nextRecursive
      }
      if (!nextRecursive && nextParentId) {
        params.parent_id = nextParentId
      }
      if (!nextRecursive && nextPathPrefix) {
        params.path_prefix = nextPathPrefix
      }

      const data = await documentApi.listDocuments(kbIdValue, params)
      if (contextId !== fileBrowserContextId) return

      const items = data?.items || []
      documentFiles.value = items
      setCurrentFileMap(items)
      Object.assign(fileBrowser, {
        parentId: nextParentId,
        page: data?.page || nextPage,
        pageSize: data?.page_size || nextPageSize,
        total: data?.total || 0,
        hasMore: Boolean(data?.has_more),
        pathPrefix: data?.path_prefix || nextPathPrefix,
        status: nextStatus,
        recursive: nextRecursive
      })

      if (data?.stats) {
        database.value = {
          ...database.value,
          stats: data.stats,
          row_count: data.stats.row_count
        }
      }
      ensureAutoRefreshForProcessing(items, data?.stats)
    } catch (error) {
      console.error(error)
      if (!options.isBackground) {
        message.error(error.message || i18n.global.t('db.messages.loadFileListFailed'))
      }
    } finally {
      if (!options.isBackground && contextId === fileBrowserContextId) {
        fileBrowser.loading = false
      }
    }
  }

  async function enterFolder(folder) {
    if (!folder?.is_folder) return
    const isVirtualFolder = Boolean(folder.is_virtual_folder)
    const currentParentId = fileBrowser.parentId
    folderBreadcrumbs.value = [
      ...folderBreadcrumbs.value,
      {
        file_id: folder.file_id,
        filename: folder.filename,
        is_virtual_folder: isVirtualFolder,
        parent_id: isVirtualFolder ? currentParentId : folder.file_id,
        path_prefix: isVirtualFolder ? folder.path_prefix || '' : ''
      }
    ]
    selectedRowKeys.value = []
    await loadDocumentFiles({
      parentId: isVirtualFolder ? currentParentId : folder.file_id,
      pathPrefix: isVirtualFolder ? folder.path_prefix || '' : '',
      page: 1,
      status: 'all',
      recursive: false
    })
  }

  async function goToFolder(index) {
    const nextBreadcrumbs = folderBreadcrumbs.value.slice(0, index + 1)
    const target = nextBreadcrumbs[nextBreadcrumbs.length - 1]
    folderBreadcrumbs.value = nextBreadcrumbs
    selectedRowKeys.value = []
    const isVirtualFolder = Boolean(target?.is_virtual_folder)
    await loadDocumentFiles({
      parentId: isVirtualFolder ? target?.parent_id || null : target?.file_id || null,
      pathPrefix: isVirtualFolder ? target?.path_prefix || '' : '',
      page: 1,
      status: 'all',
      recursive: false
    })
  }

  // 全库搜索等入口通过目录路径直达：重建路径型虚拟目录的面包屑链，并加载目标目录下的文件
  async function navigateToFolder(folderPath = '') {
    let prefix = ''
    const crumbs = [
      { file_id: null, filename: i18n.global.t('db.folderAllFiles'), path_prefix: '' },
      ...folderPath
        .split('/')
        .filter(Boolean)
        .map((segment) => {
          prefix = prefix ? `${prefix}/${segment}` : segment
          return {
            file_id: `__virtual_folder__:root:${prefix}/`,
            filename: segment,
            is_virtual_folder: true,
            parent_id: null,
            path_prefix: `${prefix}/`
          }
        })
    ]
    folderBreadcrumbs.value = crumbs
    selectedRowKeys.value = []
    return loadDocumentFiles({
      parentId: null,
      pathPrefix: crumbs[crumbs.length - 1].path_prefix,
      page: 1,
      status: 'all',
      recursive: false
    })
  }

  // 全库搜索等入口通过文件夹 ID 直达真实文件夹（parent_id 树，与 navigateToFolder 的路径型虚拟目录正交）：
  // 拉取祖先链重建面包屑，再加载该文件夹下的文件
  async function navigateToFolderById(folderId) {
    if (!folderId) return
    try {
      const chain = (await documentApi.getFolderChain(kbId.value, folderId)).chain || []
      folderBreadcrumbs.value = [
        { file_id: null, filename: i18n.global.t('db.folderAllFiles'), path_prefix: '' },
        ...chain.map((item) => ({
          file_id: item.file_id,
          filename: item.filename,
          path_prefix: ''
        }))
      ]
      selectedRowKeys.value = []
      await loadDocumentFiles({
        parentId: folderId,
        page: 1,
        status: 'all',
        recursive: false
      })
    } catch (error) {
      console.error('进入文件夹失败:', error) // i18n-ignore
      message.error(error.message || i18n.global.t('db.messages.enterFolderFailed'))
      folderBreadcrumbs.value = [{ file_id: null, filename: i18n.global.t('db.folderAllFiles'), path_prefix: '' }]
      await loadDocumentFiles({ page: 1 })
    }
  }

  async function addUploadedFiles({ items, params, parentId }) {
    if (items.length === 0) {
      message.error(i18n.global.t('db.messages.uploadFileFirst'))
      return false
    }

    state.chunkLoading = true
    try {
      const requestParams = { ...params, content_type: 'file' }
      if (parentId) requestParams.parent_id = parentId
      const data = await documentApi.addUploadedDocuments(kbId.value, items, requestParams)
      if (data.status === 'success' || data.status === 'partial_failed') {
        message.success(data.message || i18n.global.t('db.messages.fileUploadedAwaitingAdmin'))
        await delayedRefresh()
        return true
      }
      message.error(data.message || i18n.global.t('db.messages.addFileFailed'))
      return false
    } catch (error) {
      console.error(error)
      message.error(error.message || i18n.global.t('db.messages.addFileFailed'))
      return false
    } finally {
      state.chunkLoading = false
    }
  }

  async function addFiles({ items, contentType, params, parentId }) {
    if (items.length === 0) {
      message.error(
        contentType === 'file'
          ? i18n.global.t('db.messages.uploadFileFirst')
          : i18n.global.t('db.messages.enterValidUrl')
      )
      return
    }

    state.chunkLoading = true
    try {
      const requestParams = { ...params, content_type: contentType }
      if (parentId) {
        requestParams.parent_id = parentId
      }
      const data = await documentApi.addDocuments(kbId.value, items, requestParams)
      if (data.status === 'success' || data.status === 'queued') {
        const itemType = contentType === 'file' ? i18n.global.t('db.fileLabel') : 'URL'
        enableAutoRefresh('auto')
        message.success(
          data.message || i18n.global.t('db.messages.submittedForProcessing', { type: itemType })
        )
        if (data.task_id) {
          taskerStore.registerQueuedTask({
            task_id: data.task_id,
            name: i18n.global.t('db.tasks.kbImport', { id: kbId.value || '' }),
            task_type: 'knowledge_ingest',
            message: data.message,
            payload: {
              kb_id: kbId.value,
              count: items.length,
              content_type: contentType
            }
          })
        }
        await delayedRefresh() // 延迟1秒后刷新 // i18n-ignore
        return true // Indicate success
      } else {
        message.error(data.message || i18n.global.t('db.messages.processFailed'))
        return false
      }
    } catch (error) {
      console.error(error)
      message.error(error.message || i18n.global.t('db.messages.processRequestFailed'))
      return false
    } finally {
      state.chunkLoading = false
    }
  }

  async function parseFiles(fileIds) {
    if (fileIds.length === 0) return
    state.chunkLoading = true
    try {
      const data = await documentApi.parseDocuments(kbId.value, fileIds)
      if (data.status === 'success' || data.status === 'queued') {
        enableAutoRefresh('auto')
        message.success(data.message || i18n.global.t('db.messages.parseSubmitted'))
        if (data.task_id) {
          taskerStore.registerQueuedTask({
            task_id: data.task_id,
            name: i18n.global.t('db.tasks.documentParse', { id: kbId.value }),
            task_type: 'knowledge_parse',
            message: data.message,
            payload: { kb_id: kbId.value, count: fileIds.length }
          })
        }
        await delayedRefresh() // 延迟1秒后刷新 // i18n-ignore
        return true
      } else {
        message.error(data.message || i18n.global.t('db.messages.submitFailed'))
        return false
      }
    } catch (error) {
      console.error(error)
      message.error(error.message || i18n.global.t('db.messages.requestFailed'))
      return false
    } finally {
      state.chunkLoading = false
    }
  }

  async function parsePendingFiles(count = 0) {
    state.chunkLoading = true
    try {
      const data = await documentApi.parsePendingDocuments(kbId.value)
      if (data.status === 'success' || data.status === 'queued') {
        enableAutoRefresh('auto')
        message.success(data.message || i18n.global.t('db.messages.parseSubmitted'))
        if (data.task_id) {
          taskerStore.registerQueuedTask({
            task_id: data.task_id,
            name: i18n.global.t('db.tasks.documentParse', { id: kbId.value }),
            task_type: 'knowledge_parse',
            message: data.message,
            payload: { kb_id: kbId.value, count: data.queued_count || count, scope: 'pending' }
          })
        }
        await delayedRefresh()
        return true
      } else {
        message.error(data.message || i18n.global.t('db.messages.submitFailed'))
        return false
      }
    } catch (error) {
      console.error(error)
      message.error(error.message || i18n.global.t('db.messages.requestFailed'))
      return false
    } finally {
      state.chunkLoading = false
    }
  }

  async function indexFiles(fileIds, params = {}) {
    if (fileIds.length === 0) return
    state.chunkLoading = true
    try {
      const data = await documentApi.indexDocuments(kbId.value, fileIds, params)
      if (data.status === 'success' || data.status === 'queued') {
        enableAutoRefresh('auto')
        message.success(data.message || i18n.global.t('db.messages.indexSubmitted'))
        if (data.task_id) {
          taskerStore.registerQueuedTask({
            task_id: data.task_id,
            name: i18n.global.t('db.tasks.documentIndex', { id: kbId.value }),
            task_type: 'knowledge_index',
            message: data.message,
            payload: { kb_id: kbId.value, count: fileIds.length }
          })
        }
        await delayedRefresh() // 延迟1秒后刷新 // i18n-ignore
        return true
      } else {
        message.error(data.message || i18n.global.t('db.messages.submitFailed'))
        return false
      }
    } catch (error) {
      console.error(error)
      message.error(error.message || i18n.global.t('db.messages.requestFailed'))
      return false
    } finally {
      state.chunkLoading = false
    }
  }

  async function indexPendingFiles(params = {}, count = 0) {
    state.chunkLoading = true
    try {
      const data = await documentApi.indexPendingDocuments(kbId.value, params)
      if (data.status === 'success' || data.status === 'queued') {
        enableAutoRefresh('auto')
        message.success(data.message || i18n.global.t('db.messages.indexSubmitted'))
        if (data.task_id) {
          taskerStore.registerQueuedTask({
            task_id: data.task_id,
            name: i18n.global.t('db.tasks.documentIndex', { id: kbId.value }),
            task_type: 'knowledge_index',
            message: data.message,
            payload: { kb_id: kbId.value, count: data.queued_count || count, scope: 'pending' }
          })
        }
        await delayedRefresh()
        return true
      } else {
        message.error(data.message || i18n.global.t('db.messages.submitFailed'))
        return false
      }
    } catch (error) {
      console.error(error)
      message.error(error.message || i18n.global.t('db.messages.requestFailed'))
      return false
    } finally {
      state.chunkLoading = false
    }
  }

  function openFileDetail(fileId) {
    const nextFileId = typeof fileId === 'object' ? fileId?.file_id : fileId
    if (!nextFileId) {
      message.error(i18n.global.t('db.messages.fileInfoIncomplete'))
      return
    }
    fileDetailFileId.value = nextFileId
    state.fileDetailModalVisible = true
  }

  function closeFileDetail() {
    state.fileDetailModalVisible = false
    fileDetailFileId.value = null
  }

  async function loadQueryParams(id) {
    const kbIdValue = id || kbId.value
    if (!kbIdValue) return

    state.queryParamsLoading = true
    try {
      const response = await queryApi.getKnowledgeBaseQueryParams(kbIdValue)
      queryParams.value = response.params?.options || []

      // Create a set of currently supported parameter keys
      const supportedParamKeys = new Set(queryParams.value.map((param) => param.key))

      // Remove unsupported parameters from meta
      for (const key in meta) {
        if (key !== 'kb_id' && !supportedParamKeys.has(key)) {
          delete meta[key]
        }
      }

      // Add default values for supported parameters that are not in meta
      queryParams.value.forEach((param) => {
        if (!(param.key in meta)) {
          meta[param.key] = param.default
        }
      })
    } catch (error) {
      console.error('Failed to load query params:', error)
      message.error(i18n.global.t('db.messages.loadQueryParamsFailed'))
    } finally {
      state.queryParamsLoading = false
    }
  }

  function startAutoRefresh() {
    if (state.autoRefresh && !refreshInterval) {
      refreshInterval = setInterval(() => {
        getDatabaseInfo(undefined, true, true) // Skip loading query params during auto-refresh
        loadDocumentFiles({ isBackground: true })
      }, 1000)
    }
  }

  function stopAutoRefresh() {
    if (refreshInterval) {
      clearInterval(refreshInterval)
      refreshInterval = null
    }
  }

  // 延时刷新文件理解（延迟1秒后刷新）
  async function delayedRefresh() {
    await new Promise((resolve) => setTimeout(resolve, 1000))
    await getDatabaseInfo(undefined, true)
    await loadDocumentFiles({ isBackground: true })
  }

  function toggleAutoRefresh() {
    const nextState = !state.autoRefresh
    state.autoRefresh = nextState
    if (nextState) {
      autoRefreshSource = 'manual'
      autoRefreshManualOverride = false
      startAutoRefresh()
    } else {
      autoRefreshManualOverride = true
      autoRefreshSource = null
      stopAutoRefresh()
    }
  }

  function selectAllFailedFiles() {
    const files = Object.values(database.value.files || {})
    const failedFiles = files.filter((file) => file.status === 'failed').map((file) => file.file_id)

    const newSelectedKeys = [...new Set([...selectedRowKeys.value, ...failedFiles])]
    selectedRowKeys.value = newSelectedKeys

    if (failedFiles.length > 0) {
      message.success(i18n.global.t('db.messages.selectedFailedFiles', { count: failedFiles.length }))
    } else {
      message.info(i18n.global.t('db.messages.noFailedFiles'))
    }
  }

  function getDatabaseNameById(id) {
    const normalizedId = String(id || '').trim()
    if (!normalizedId) return ''

    const matchedDatabase = databases.value.find(
      (item) => String(item.kb_id || '').trim() === normalizedId
    )
    if (matchedDatabase?.name) return matchedDatabase.name

    if (String(database.value?.kb_id || '').trim() === normalizedId) {
      return database.value?.name || ''
    }

    return ''
  }

  return {
    databases,
    database,
    kbId,
    fileDetailFileId,
    documentFiles,
    folderBreadcrumbs,
    queryParams,
    meta,
    selectedRowKeys,
    fileBrowser,
    state,
    loadDatabases,
    createDatabase,
    getDatabaseInfo,
    updateDatabaseInfo,
    deleteDatabase,
    deleteFile,
    handleDeleteFile,
    moveFile,
    renameFile,
    handleBatchDelete,
    addFiles,
    addUploadedFiles,
    parseFiles,
    parsePendingFiles,
    indexFiles,
    indexPendingFiles,
    openFileDetail,
    closeFileDetail,
    loadQueryParams,
    loadDocumentFiles,
    enterFolder,
    goToFolder,
    navigateToFolder,
    navigateToFolderById,
    resetFileBrowser,

    startAutoRefresh,
    stopAutoRefresh,
    toggleAutoRefresh,
    selectAllFailedFiles,
    getDatabaseNameById
  }
})
