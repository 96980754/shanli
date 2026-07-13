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
  const scopeNode = document.getElementById('document-detail-scope')
  const documentTypeNode = document.getElementById('document-detail-document-type')
  const productNode = document.getElementById('document-detail-product')
  const priorityNode = document.getElementById('document-detail-priority')
  if (!titleNode || !typeNode || !statusNode || !blockNode || !chunkNode || !departmentNode || !productLineNode || !visibilityNode || !securityLevelNode || !tagsNode || !scopeNode || !documentTypeNode || !productNode || !priorityNode) {
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
    scopeNode.textContent = ''
    documentTypeNode.textContent = ''
    productNode.textContent = ''
    priorityNode.textContent = ''
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
  scopeNode.textContent = `范围：${detail.scope || 'I'}`
  documentTypeNode.textContent = `分类：${detail.document_type || 'OTH'}`
  productNode.textContent = `规范产品：${detail.product || 'GEN'}`
  priorityNode.textContent = `优先级：${detail.priority || 'P2'}`
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
  try {
    const formData = new FormData()
    formData.append('file', input.files[0])
    formData.append("scope", document.getElementById('document-scope')?.value || 'I')
    formData.append("document_type", document.getElementById('document-type')?.value || 'OTH')
    formData.append("product", document.getElementById('document-product')?.value || 'GEN')
    formData.append("priority", document.getElementById('document-priority')?.value || 'P2')
    const result = await authorizedJson(`/api/kb/${activeKbId}/documents/upload`, {
      method: 'POST',
      body: formData,
    })
    if (!result?.id) {
      setAdminMessage('上传失败。')
      return
    }
    setAdminMessage('上传成功。')
    await refreshKnowledgeBaseView()
    await loadDocumentDetail(result.id)
    input.value = ''
  } finally {
    uploading = false
  }
}

function parseCommaList(value) {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

function clearViewRuleForm() {
  const departments = document.getElementById('view-rule-departments')
  const productLines = document.getElementById('view-rule-product-lines')
  const maxLevel = document.getElementById('view-rule-max-security-level')
  if (departments) departments.value = ''
  if (productLines) productLines.value = ''
  if (maxLevel) maxLevel.value = ''
  for (const id of ['view-rule-public', 'view-rule-internal', 'view-rule-restricted']) {
    const checkbox = document.getElementById(id)
    if (checkbox) checkbox.checked = false
  }
}

async function loadViewRule() {
  if (!activeKbId) return
  const userId = document.getElementById('permission-user-id')?.value
  if (!userId) {
    setAdminMessage('请先输入用户 ID。')
    return
  }
  const result = await authorizedJson(`/api/kb/${activeKbId}/view-rules/${userId}`)
  if (!result) {
    setAdminMessage('加载知识视图失败。')
    return
  }
  clearViewRuleForm()
  if (result.rule === null) {
    setAdminMessage('该用户当前可查看全部文档。')
    return
  }
  document.getElementById('view-rule-departments').value = (result.allowed_departments || []).join(',')
  document.getElementById('view-rule-product-lines').value = (result.allowed_product_lines || []).join(',')
  document.getElementById('view-rule-public').checked = (result.allowed_visibilities || []).includes('public')
  document.getElementById('view-rule-internal').checked = (result.allowed_visibilities || []).includes('internal')
  document.getElementById('view-rule-restricted').checked = (result.allowed_visibilities || []).includes('restricted')
  document.getElementById('view-rule-max-security-level').value = result.max_security_level ?? ''
  setAdminMessage('知识视图已加载。')
}

async function saveViewRule() {
  if (!activeKbId) return
  const userId = document.getElementById('permission-user-id')?.value
  if (!userId) {
    setAdminMessage('请先输入用户 ID。')
    return
  }
  const allowedVisibilities = []
  if (document.getElementById('view-rule-public')?.checked) allowedVisibilities.push('public')
  if (document.getElementById('view-rule-internal')?.checked) allowedVisibilities.push('internal')
  if (document.getElementById('view-rule-restricted')?.checked) allowedVisibilities.push('restricted')
  const maxLevelValue = document.getElementById('view-rule-max-security-level')?.value || ''
  const result = await authorizedJson(`/api/kb/${activeKbId}/view-rules/${userId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      allowed_departments: parseCommaList(document.getElementById('view-rule-departments')?.value || ''),
      allowed_product_lines: parseCommaList(document.getElementById('view-rule-product-lines')?.value || ''),
      allowed_visibilities: allowedVisibilities,
      max_security_level: maxLevelValue ? Number(maxLevelValue) : null,
    }),
  })
  setAdminMessage(result ? '知识视图保存成功。' : '知识视图保存失败。')
}

async function deleteViewRule() {
  if (!activeKbId) return
  const userId = document.getElementById('permission-user-id')?.value
  if (!userId) {
    setAdminMessage('请先输入用户 ID。')
    return
  }
  const result = await authorizedJson(`/api/kb/${activeKbId}/view-rules/${userId}`, { method: 'DELETE' })
  if (!result) {
    setAdminMessage('删除知识视图失败。')
    return
  }
  clearViewRuleForm()
  setAdminMessage('知识视图已删除，恢复查看全部文档。')
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

async function loadRetrievalPolicy() {
  const result = await authorizedJson('/api/retrieval-policy')
  const node = document.getElementById('retrieval-policy-content')
  if (!node) return
  if (!result?.formula || !result?.top_k) {
    node.textContent = '检索策略加载失败。'
    return
  }
  const formula = result.formula
  const topK = result.top_k
  node.textContent = `相似度 ${formula.similarity_ratio} / 类型 ${formula.type_ratio} / 产品 ${formula.product_ratio} / 优先级 ${formula.priority_ratio}；Top-K：${topK.initial} → ${topK.after_rerank} → ${topK.final}`
}

async function loadAdminShell() {
  authProfile = await authorizedJson('/api/auth/me')
  await loadRetrievalPolicy()
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
  const loadViewRuleButton = document.getElementById('load-view-rule')
  if (loadViewRuleButton) {
    loadViewRuleButton.addEventListener('click', () => {
      void loadViewRule()
    })
  }
  const saveViewRuleButton = document.getElementById('save-view-rule')
  if (saveViewRuleButton) {
    saveViewRuleButton.addEventListener('click', () => {
      void saveViewRule()
    })
  }
  const deleteViewRuleButton = document.getElementById('delete-view-rule')
  if (deleteViewRuleButton) {
    deleteViewRuleButton.addEventListener('click', () => {
      void deleteViewRule()
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
