let authProfile = null
let activeKbId = null
let knowledgeBases = []
let documents = []
let permissions = []
let issues = []
let pendingDeleteDocument = null
let pendingDeleteKnowledgeBase = null
let selectedDocumentId = null
let selectedDocumentDetail = null
let uploading = false

async function requestJson(path, options = {}) {
  const response = await fetch(path, options)
  if (!response.ok) {
    return null
  }
  return response.json()
}

function setAdminMessage(message) {
  const messageNode = document.getElementById('admin-message')
  if (messageNode) {
    messageNode.textContent = message
  }
}

function renderDocumentDetail(detail) {
  const titleNode = document.getElementById('document-detail-title')
  const typeNode = document.getElementById('document-detail-type')
  const statusNode = document.getElementById('document-detail-status')
  const blockNode = document.getElementById('document-detail-block-count')
  const chunkNode = document.getElementById('document-detail-chunk-count')
  const departmentNode = document.getElementById('document-detail-department')
  const productLineNode = document.getElementById('document-detail-product-line')
  const visibilityNode = document.getElementById('document-detail-visibility')
  const securityLevelNode = document.getElementById('document-detail-security-level')
  const tagsNode = document.getElementById('document-detail-tags')
  if (!titleNode || !typeNode || !statusNode || !blockNode || !chunkNode || !departmentNode || !productLineNode || !visibilityNode || !securityLevelNode || !tagsNode) {
    return
  }
  if (!detail) {
    titleNode.textContent = '未选择文档'
    typeNode.textContent = ''
    statusNode.textContent = ''
    blockNode.textContent = ''
    chunkNode.textContent = ''
    departmentNode.textContent = ''
    productLineNode.textContent = ''
    visibilityNode.textContent = ''
    securityLevelNode.textContent = ''
    tagsNode.textContent = ''
    return
  }
  titleNode.textContent = detail.title
  typeNode.textContent = `类型：${detail.file_type}`
  statusNode.textContent = `状态：${detail.status}`
  blockNode.textContent = `块数：${detail.block_count}`
  chunkNode.textContent = `切片数：${detail.chunk_count}`
  departmentNode.textContent = `部门：${detail.department || ''}`
  productLineNode.textContent = `产品线：${detail.product_line || ''}`
  visibilityNode.textContent = `可见范围：${detail.visibility || ''}`
  securityLevelNode.textContent = `密级：${detail.security_level ?? ''}`
  tagsNode.textContent = `标签：${detail.tags || ''}`
}

async function handleLoginSubmit(event) {
  event.preventDefault()
  const form = event.currentTarget
  const username = form.username.value
  const password = form.password.value
  const result = await requestJson('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!result?.token) {
    return
  }
  localStorage.setItem('session_token', result.token)
  window.location.href = '/admin'
}

function showDeletePanel(targetType, targetId, targetTitle) {
  const panel = document.getElementById('delete-panel')
  const target = document.getElementById('delete-target')
  if (!panel || !target) {
    return
  }
  pendingDeleteDocument = targetType === 'document' ? targetId : null
  pendingDeleteKnowledgeBase = targetType === 'knowledge-base' ? targetId : null
  target.textContent = `即将删除：${targetTitle}`
  panel.hidden = false
}

function hideDeletePanel() {
  pendingDeleteDocument = null
  pendingDeleteKnowledgeBase = null
  const panel = document.getElementById('delete-panel')
  if (panel) {
    panel.hidden = true
  }
}

function renderKnowledgeBases(items) {
  const container = document.getElementById('kb-list')
  if (!container) {
    return
  }
  container.innerHTML = ''
  for (const item of items) {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = item.name
    button.addEventListener('click', () => {
      activeKbId = item.id
      selectedDocumentId = null
      selectedDocumentDetail = null
      renderDocumentDetail(null)
      void refreshKnowledgeBaseView()
    })
    container.appendChild(button)
  }
}

function renderDocuments(items) {
  const container = document.getElementById('document-list')
  if (!container) {
    return
  }
  container.innerHTML = ''
  for (const item of items) {
    const row = document.createElement('div')
    const titleButton = document.createElement('button')
    titleButton.type = 'button'
    titleButton.textContent = item.title
    titleButton.addEventListener('click', () => {
      void loadDocumentDetail(item.id)
    })
    row.appendChild(titleButton)

    const deleteButton = document.createElement('button')
    deleteButton.type = 'button'
    deleteButton.textContent = '删除文档'
    deleteButton.addEventListener('click', () => {
      showDeletePanel('document', item.id, item.title)
    })
    row.appendChild(deleteButton)
    container.appendChild(row)
  }
}

function renderPermissions(items) {
  const container = document.getElementById('permission-list')
  if (!container) {
    return
  }
  container.innerHTML = ''
  for (const item of items) {
    const row = document.createElement('div')
    row.textContent = item.username || String(item.user_id)
    container.appendChild(row)
  }
}

function renderIssues(items) {
  const container = document.getElementById('issue-list')
  if (!container) {
    return
  }
  container.innerHTML = ''
  for (const item of items) {
    const row = document.createElement('div')

    const question = document.createElement('p')
    question.textContent = item.question
    row.appendChild(question)

    const feedback = document.createElement('p')
    feedback.textContent = item.feedback_text || item.reason
    row.appendChild(feedback)

    const resolveButton = document.createElement('button')
    resolveButton.type = 'button'
    resolveButton.textContent = '标记已处理'
    resolveButton.addEventListener('click', () => {
      void updateIssueStatus(item.id, 'resolved')
    })
    row.appendChild(resolveButton)

    const ignoreButton = document.createElement('button')
    ignoreButton.type = 'button'
    ignoreButton.textContent = '忽略'
    ignoreButton.addEventListener('click', () => {
      void updateIssueStatus(item.id, 'ignored')
    })
    row.appendChild(ignoreButton)

    container.appendChild(row)
  }
}

async function loadIssues() {
  if (!activeKbId) {
    issues = []
    renderIssues(issues)
    return
  }
  const result = await authorizedJson(`/api/issues?kb_id=${activeKbId}&status=open`)
  issues = result?.items || []
  renderIssues(issues)
}

async function updateIssueStatus(issueId, status) {
  const result = await authorizedJson(`/api/issues/${issueId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  if (!result?.id) {
    setAdminMessage('更新知识缺口失败。')
    return
  }
  setAdminMessage('知识缺口状态已更新。')
  await loadIssues()
}

async function authorizedJson(path, options = {}) {
  const token = localStorage.getItem('session_token')
  const headers = { ...(options.headers || {}) }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  return requestJson(path, { ...options, headers })
}

async function loadDocumentDetail(docId) {
  if (!activeKbId || !docId) {
    return
  }
  const detail = await authorizedJson(`/api/kb/${activeKbId}/documents/${docId}`)
  if (!detail) {
    setAdminMessage('加载文档详情失败。')
    return
  }
  selectedDocumentId = docId
  selectedDocumentDetail = detail
  renderDocumentDetail(detail)
}

async function handleKnowledgeBaseCreate(event) {
  event.preventDefault()
  const form = event.currentTarget
  const name = form.querySelector('#kb-create-name')?.value?.trim() || ''
  const description = form.querySelector('#kb-create-description')?.value?.trim() || ''
  const visibility = form.querySelector('#kb-create-visibility')?.value || 'department'
  if (!name) {
    setAdminMessage('请输入知识库名称。')
    return
  }
  const result = await authorizedJson('/api/kb', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, description, visibility }),
  })
  if (!result?.id) {
    setAdminMessage('创建知识库失败。')
    return
  }
  await refreshKnowledgeBases()
  activeKbId = result.id
  await refreshKnowledgeBaseView()
  form.reset()
  setAdminMessage('知识库创建成功。')
}

async function handleDocumentUpload(event) {
  event.preventDefault()
  if (!activeKbId || uploading) {
    return
  }
  const input = document.getElementById('document-file')
  if (!input?.files?.length) {
    setAdminMessage('请先选择文件。')
    return
  }
  uploading = true
  const formData = new FormData()
  formData.append('file', input.files[0])
  const result = await authorizedJson(`/api/kb/${activeKbId}/documents/upload`, {
    method: 'POST',
    body: formData,
  })
  uploading = false
  if (!result?.id) {
    setAdminMessage('上传失败。')
    return
  }
  setAdminMessage('上传成功。')
  await refreshKnowledgeBaseView()
  await loadDocumentDetail(result.id)
  input.value = ''
}

async function savePermission() {
  if (!activeKbId) {
    return
  }
  const userIdInput = document.getElementById('permission-user-id')
  if (!userIdInput || !userIdInput.value) {
    setAdminMessage('请先输入用户 ID。')
    return
  }
  const result = await authorizedJson(`/api/kb/${activeKbId}/permissions/${userIdInput.value}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      can_view: document.getElementById('perm-view')?.checked || false,
      can_upload: document.getElementById('perm-upload')?.checked || false,
      can_delete: document.getElementById('perm-delete')?.checked || false,
      can_grant: document.getElementById('perm-grant')?.checked || false,
    }),
  })
  if (!result) {
    setAdminMessage('保存权限失败。')
    return
  }
  setAdminMessage('权限保存成功。')
  await refreshKnowledgeBaseView()
}

async function refreshKnowledgeBases() {
  const kbResult = await authorizedJson('/api/kb')
  knowledgeBases = kbResult?.items || []
  renderKnowledgeBases(knowledgeBases)
  if (!knowledgeBases.length) {
    activeKbId = null
    documents = []
    permissions = []
    issues = []
    selectedDocumentId = null
    selectedDocumentDetail = null
    renderDocuments(documents)
    renderPermissions(permissions)
    renderIssues(issues)
    renderDocumentDetail(null)
  }
}

async function confirmDelete() {
  if (pendingDeleteDocument && activeKbId) {
    const deletedId = pendingDeleteDocument
    const result = await authorizedJson(`/api/kb/${activeKbId}/documents/${deletedId}`, {
      method: 'DELETE',
    })
    if (!result?.deleted) {
      setAdminMessage('删除文档失败。')
      return
    }
    setAdminMessage('文档删除成功。')
    hideDeletePanel()
    await refreshKnowledgeBaseView()
    if (selectedDocumentId === deletedId) {
      selectedDocumentId = null
      selectedDocumentDetail = null
      renderDocumentDetail(null)
    }
    return
  }
  if (pendingDeleteKnowledgeBase) {
    const result = await authorizedJson(`/api/kb/${pendingDeleteKnowledgeBase}`, {
      method: 'DELETE',
    })
    if (!result?.deleted) {
      setAdminMessage('删除知识库失败。')
      return
    }
    setAdminMessage('知识库删除成功。')
    hideDeletePanel()
    await refreshKnowledgeBases()
    activeKbId = knowledgeBases[0]?.id || null
    if (activeKbId) {
      await refreshKnowledgeBaseView()
    }
  }
}

async function refreshKnowledgeBaseView() {
  if (!activeKbId) {
    documents = []
    permissions = []
    issues = []
    renderDocuments(documents)
    renderPermissions(permissions)
    renderIssues(issues)
    renderDocumentDetail(null)
    return
  }
  const documentResult = await authorizedJson(`/api/kb/${activeKbId}/documents`)
  documents = documentResult?.items || []
  renderDocuments(documents)
  if (!documents.some((item) => item.id === selectedDocumentId)) {
    selectedDocumentId = null
    selectedDocumentDetail = null
    renderDocumentDetail(null)
  }
  const permissionResult = await authorizedJson(`/api/kb/${activeKbId}/permissions`)
  permissions = permissionResult?.items || []
  renderPermissions(permissions)
  await loadIssues()
}

async function loadAdminShell() {
  authProfile = await authorizedJson('/api/auth/me')
  await refreshKnowledgeBases()
  activeKbId = knowledgeBases[0]?.id || null
  documents = []
  permissions = []
  issues = []
  selectedDocumentId = null
  selectedDocumentDetail = null
  renderDocuments(documents)
  renderPermissions(permissions)
  renderIssues(issues)
  renderDocumentDetail(null)
  if (activeKbId) {
    await refreshKnowledgeBaseView()
  }
}

window.addEventListener('DOMContentLoaded', () => {
  const loginForm = document.getElementById('login-form')
  if (loginForm) {
    loginForm.addEventListener('submit', handleLoginSubmit)
  }
  const uploadForm = document.getElementById('document-upload-form')
  if (uploadForm) {
    uploadForm.addEventListener('submit', (event) => {
      void handleDocumentUpload(event)
    })
  }
  const kbCreateForm = document.getElementById('kb-create-form')
  if (kbCreateForm) {
    kbCreateForm.addEventListener('submit', (event) => {
      void handleKnowledgeBaseCreate(event)
    })
  }
  const savePermissionButton = document.getElementById('save-permission')
  if (savePermissionButton) {
    savePermissionButton.addEventListener('click', () => {
      void savePermission()
    })
  }
  const deleteKbButton = document.getElementById('delete-kb-button')
  if (deleteKbButton) {
    deleteKbButton.addEventListener('click', () => {
      if (!activeKbId) {
        return
      }
      const kb = knowledgeBases.find((item) => item.id === activeKbId)
      showDeletePanel('knowledge-base', activeKbId, kb?.name || '当前知识库')
    })
  }
  const confirmDeleteButton = document.getElementById('confirm-delete')
  if (confirmDeleteButton) {
    confirmDeleteButton.addEventListener('click', () => {
      void confirmDelete()
    })
  }
  const cancelDeleteButton = document.getElementById('cancel-delete')
  if (cancelDeleteButton) {
    cancelDeleteButton.addEventListener('click', hideDeletePanel)
  }
  if (document.getElementById('kb-list')) {
    void loadAdminShell()
  }
})
