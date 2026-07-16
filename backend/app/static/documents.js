let authProfile = null
let activeKbId = null
let selectedDocument = null
let knowledgeBases = []
let allDocuments = []
let permissions = []
let registeredUsers = []

function token() {
  return localStorage.getItem('session_token')
}

function authorizationHeaders(extra = {}) {
  const currentToken = token()
  return currentToken ? { ...extra, Authorization: `Bearer ${currentToken}` } : extra
}

function message(text) {
  const node = document.getElementById('documents-message')
  if (node) node.textContent = text
}

function currentUserId() {
  return authProfile?.user_id || authProfile?.id || null
}

async function responseJson(response) {
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401) {
      window.location.href = '/login'
      return null
    }
    message(body.detail || '请求失败')
    return null
  }
  return body
}

async function authorizedJson(path, options = {}) {
  const headers = authorizationHeaders(options.headers || {})
  const response = await fetch(path, { ...options, headers })
  return responseJson(response)
}

function setOptions(selectId, values, firstLabel, firstValue = '') {
  const select = document.getElementById(selectId)
  if (!select) return
  const selectedValue = select.value
  select.replaceChildren()
  const first = document.createElement('option')
  first.value = firstValue
  first.textContent = firstLabel
  select.append(first)
  for (const value of values) {
    if (value === null || value === undefined || value === '') continue
    const option = document.createElement('option')
    option.value = String(value)
    option.textContent = String(value)
    select.append(option)
  }
  if ([...select.options].some((option) => option.value === selectedValue)) {
    select.value = selectedValue
  }
}

function uniqueSorted(values) {
  return [...new Set(values.filter(Boolean).map(String))].sort((left, right) => left.localeCompare(right, 'zh-CN'))
}

function applyDocumentFilters() {
  const status = document.getElementById('documents-status-filter')?.value || 'all'
  const visibility = document.getElementById('documents-visibility-filter')?.value || ''
  const product = document.getElementById('documents-product-filter')?.value || ''
  return allDocuments.filter((documentItem) =>
    matchesDocumentSearch(documentItem) &&
    (status === 'all' || documentItem.status === status) &&
    (!visibility || documentItem.visibility === visibility) &&
    (!product || documentItem.product === product || documentItem.product_line === product),
  )
}

function matchesDocumentSearch(documentItem) {
  const keyword = document.getElementById('documents-search')?.value?.trim().toLowerCase() || ''
  if (!keyword) return true
  return [documentItem.title, documentItem.product, documentItem.product_line, documentItem.file_type, documentItem.document_type]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(keyword))
}

function statusBadgeClass(item) {
  if (item.status === 'parsed') return 'status-badge status-badge--success'
  if (item.status === 'stored_unsupported') return 'status-badge status-badge--warning'
  return 'status-badge'
}

function refreshFilterOptions() {
  setOptions('documents-status-filter', uniqueSorted(allDocuments.map((item) => item.status)), '全部状态', 'all')
  setOptions('documents-visibility-filter', uniqueSorted(allDocuments.map((item) => item.visibility)), '全部可见范围')
  setOptions('documents-product-filter', uniqueSorted(allDocuments.flatMap((item) => [item.product, item.product_line])), '全部产品')
}

function renderKnowledgeBases() {
  const container = document.getElementById('documents-kb-list')
  const empty = document.getElementById('documents-kb-empty-state')
  const noAccess = document.getElementById('documents-no-access')
  if (!container) return
  container.replaceChildren()
  const hasItems = knowledgeBases.length > 0
  if (empty) empty.hidden = hasItems
  if (noAccess) noAccess.hidden = hasItems
  for (const kb of knowledgeBases) {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = `${kb.name}（${kb.doc_count || 0}）`
    button.className = kb.id === activeKbId ? 'button--primary' : ''
    button.addEventListener('click', () => {
      activeKbId = kb.id
      selectedDocument = null
      void refreshKnowledgeBaseView()
    })
    container.append(button)
  }
}

function statusLabel(item) {
  return item.parse_status_label || (item.status === 'parsed' ? '已解析，可用于问答' : item.status || '未知状态')
}

function renderDocuments() {
  const container = document.getElementById('documents-list')
  const empty = document.getElementById('documents-empty-state')
  if (!container) return
  const filtered = applyDocumentFilters()
  container.replaceChildren()
  if (empty) empty.hidden = filtered.length > 0
  const countNode = document.getElementById('documents-total-count')
  if (countNode) countNode.textContent = `${filtered.length} / ${allDocuments.length} 份文档`
  for (const item of filtered) {
    const card = document.createElement('article')
    card.className = 'document-card'

    const title = document.createElement('button')
    title.type = 'button'
    title.textContent = item.title
    title.addEventListener('click', () => loadDocumentDetail(item.id))
    card.append(title)

    const meta = document.createElement('p')
    meta.className = 'message'
    meta.textContent = `${item.file_type || 'file'} · ${item.product || item.product_line || 'GEN'} · ${item.visibility || 'internal'}`
    card.append(meta)

    const badge = document.createElement('span')
    badge.className = statusBadgeClass(item)
    badge.textContent = statusLabel(item)
    card.append(badge)

    container.append(card)
  }
}

function renderDocumentDetail(detail) {
  const title = document.getElementById('documents-detail-title')
  const status = document.getElementById('documents-detail-status')
  const metadata = document.getElementById('documents-detail-metadata')
  const parse = document.getElementById('documents-detail-parse')
  const counts = document.getElementById('documents-detail-counts')
  const access = document.getElementById('documents-detail-access')
  const hint = document.getElementById('documents-download-hint')
  const download = document.getElementById('download-document')
  if (!title || !metadata || !download) return
  if (!detail) {
    title.textContent = '未选择文档'
    if (status) status.textContent = '请选择文档'
    metadata.textContent = ''
    if (parse) parse.textContent = ''
    if (counts) counts.textContent = ''
    if (access) access.textContent = ''
    if (hint) hint.textContent = '请先从中间列表选择一份文档。'
    download.disabled = true
    return
  }
  title.textContent = detail.title
  if (status) {
    status.className = statusBadgeClass(detail)
    status.textContent = statusLabel(detail)
  }
  metadata.textContent = `原文件：${detail.original_filename || detail.title} · ${detail.file_size || 0} bytes · 类型：${detail.file_type || ''}`
  if (parse) parse.textContent = `部门：${detail.department || ''} · 产品：${detail.product || detail.product_line || 'GEN'} · 可见性：${detail.visibility || ''} · 密级：${detail.security_level ?? ''}`
  if (counts) counts.textContent = `内容块：${detail.block_count || 0} · 切片：${detail.chunk_count || 0} · 范围：${detail.scope || 'I'} · 分类：${detail.document_type || 'OTH'} · 优先级：${detail.priority || 'P2'}`
  if (access) access.textContent = detail.download_available ? '下载：可用' : '下载：不可用'
  if (hint) {
    hint.textContent = detail.status === 'stored_unsupported'
      ? '仅可下载，不进入问答索引。'
      : detail.download_available ? '可下载原始文件，可解析文档可参与问答。' : '原文件当前不可下载，请联系管理员。'
  }
  download.disabled = detail.download_available !== true
}

function selectedPermissionUserId() {
  return document.getElementById('documents-permission-user')?.value || ''
}

function currentKbPermission() {
  const userId = currentUserId()
  if (!userId) return null
  return permissions.find((item) => String(item.user_id) === String(userId)) || null
}

function renderAdminPanel() {
  const panel = document.getElementById('documents-admin-panel')
  if (!panel) return
  const canGrant = currentKbPermission()?.can_grant === true
  panel.hidden = !canGrant
  if (canGrant) {
    void loadRegisteredUsers()
  }
}

function applySelectedPermissionToForm() {
  const userId = selectedPermissionUserId()
  const item = permissions.find((permission) => String(permission.user_id) === String(userId))
  document.getElementById('documents-perm-view').checked = item?.can_view === true
  document.getElementById('documents-perm-upload').checked = item?.can_upload === true
  document.getElementById('documents-perm-delete').checked = item?.can_delete === true
  document.getElementById('documents-perm-grant').checked = item?.can_grant === true
}

async function loadRegisteredUsers() {
  const body = await authorizedJson('/api/users')
  if (!body) return
  registeredUsers = body.items || []
  const select = document.getElementById('documents-permission-user')
  if (!select) return
  const previous = select.value
  select.replaceChildren()
  for (const user of registeredUsers) {
    const option = document.createElement('option')
    option.value = user.id
    option.textContent = `${user.username}（${user.role || '用户'}）`
    select.append(option)
  }
  if ([...select.options].some((option) => option.value === previous)) {
    select.value = previous
  }
  applySelectedPermissionToForm()
}

function parseCommaList(value) {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}

function selectedVisibilities() {
  const values = []
  if (document.getElementById('documents-view-rule-public')?.checked) values.push('public')
  if (document.getElementById('documents-view-rule-internal')?.checked) values.push('internal')
  if (document.getElementById('documents-view-rule-restricted')?.checked) values.push('restricted')
  return values
}

function clearWorkbenchViewRuleForm() {
  document.getElementById('documents-view-rule-departments').value = ''
  document.getElementById('documents-view-rule-products').value = ''
  document.getElementById('documents-view-rule-public').checked = false
  document.getElementById('documents-view-rule-internal').checked = false
  document.getElementById('documents-view-rule-restricted').checked = false
  document.getElementById('documents-view-rule-max-security-level').value = ''
}

async function loadWorkbenchViewRule() {
  const userId = selectedPermissionUserId()
  if (!activeKbId || !userId) return
  const body = await authorizedJson(`/api/kb/${activeKbId}/view-rules/${userId}`)
  if (!body) return
  clearWorkbenchViewRuleForm()
  const rule = body.rule === null ? null : body
  if (!rule) {
    message('该用户当前未设置知识视图规则。')
    return
  }
  document.getElementById('documents-view-rule-departments').value = (rule.allowed_departments || []).join(',')
  document.getElementById('documents-view-rule-products').value = (rule.allowed_product_lines || []).join(',')
  document.getElementById('documents-view-rule-public').checked = (rule.allowed_visibilities || []).includes('public')
  document.getElementById('documents-view-rule-internal').checked = (rule.allowed_visibilities || []).includes('internal')
  document.getElementById('documents-view-rule-restricted').checked = (rule.allowed_visibilities || []).includes('restricted')
  document.getElementById('documents-view-rule-max-security-level').value = rule.max_security_level ?? ''
  message('知识视图已加载。')
}

async function saveWorkbenchViewRule() {
  const userId = selectedPermissionUserId()
  if (!activeKbId || !userId) return
  const maxLevelValue = document.getElementById('documents-view-rule-max-security-level')?.value || ''
  const body = await authorizedJson(`/api/kb/${activeKbId}/view-rules/${userId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      allowed_departments: parseCommaList(document.getElementById('documents-view-rule-departments')?.value || ''),
      allowed_product_lines: parseCommaList(document.getElementById('documents-view-rule-products')?.value || ''),
      allowed_visibilities: selectedVisibilities(),
      max_security_level: maxLevelValue ? Number(maxLevelValue) : null,
    }),
  })
  if (body) message('知识视图保存成功。')
}

async function deleteWorkbenchViewRule() {
  const userId = selectedPermissionUserId()
  if (!activeKbId || !userId) return
  const body = await authorizedJson(`/api/kb/${activeKbId}/view-rules/${userId}`, { method: 'DELETE' })
  if (!body) return
  clearWorkbenchViewRuleForm()
  message('知识视图已删除。')
}

async function saveWorkbenchPermission() {
  const userId = selectedPermissionUserId()
  if (!activeKbId || !userId) return
  const canView = document.getElementById('documents-perm-view')?.checked || false
  const body = await authorizedJson(`/api/kb/${activeKbId}/permissions/${userId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      can_view: canView,
      can_upload: document.getElementById('documents-perm-upload')?.checked || false,
      can_delete: document.getElementById('documents-perm-delete')?.checked || false,
      can_grant: document.getElementById('documents-perm-grant')?.checked || false,
    }),
  })
  if (!body) return
  if (!canView) {
    await authorizedJson(`/api/kb/${activeKbId}/view-rules/${userId}`, { method: 'DELETE' })
    clearWorkbenchViewRuleForm()
  }
  message('权限保存成功。')
  await refreshKnowledgeBaseView()
  await loadRegisteredUsers()
}

async function loadDocumentDetail(docId) {
  if (activeKbId === null) return
  const body = await authorizedJson(`/api/kb/${activeKbId}/documents/${docId}`)
  if (!body) return
  selectedDocument = body
  renderDocumentDetail(body)
}

async function downloadSelectedDocument() {
  if (!selectedDocument || activeKbId === null) return
  const response = await fetch(`/api/kb/${activeKbId}/documents/${selectedDocument.id}/download`, {
    headers: authorizationHeaders(),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    message(body.detail || '下载失败')
    return
  }
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = selectedDocument.original_filename || selectedDocument.title || 'download'
  link.click()
  URL.revokeObjectURL(objectUrl)
}

async function refreshKnowledgeBases() {
  const body = await authorizedJson('/api/kb')
  if (!body) return
  knowledgeBases = body.items || []
  if (!knowledgeBases.some((item) => item.id === activeKbId)) {
    activeKbId = knowledgeBases[0]?.id || null
  }
  renderKnowledgeBases()
  if (activeKbId) {
    await refreshKnowledgeBaseView()
  } else {
    allDocuments = []
    permissions = []
    selectedDocument = null
    refreshFilterOptions()
    renderDocuments()
    renderDocumentDetail(null)
    renderAdminPanel()
  }
}

async function refreshKnowledgeBaseView() {
  if (!activeKbId) return
  const documentBody = await authorizedJson(`/api/kb/${activeKbId}/documents`)
  allDocuments = documentBody?.items || []
  refreshFilterOptions()
  renderDocuments()
  if (!allDocuments.some((item) => item.id === selectedDocument?.id)) {
    selectedDocument = null
    renderDocumentDetail(null)
  }

  const permissionBody = await authorizedJson(`/api/kb/${activeKbId}/permissions`)
  permissions = permissionBody?.items || []
  renderAdminPanel()
}

async function loadDocumentsShell() {
  if (!token()) {
    window.location.href = '/login'
    return
  }
  authProfile = await authorizedJson('/api/auth/me')
  if (!authProfile) return
  document.getElementById('documents-current-user').textContent = `当前账号：${authProfile.username}`
  await refreshKnowledgeBases()
}

function logout() {
  localStorage.removeItem('session_token')
  window.location.href = '/login'
}

for (const id of ['documents-status-filter', 'documents-visibility-filter', 'documents-product-filter', 'documents-search']) {
  document.getElementById(id)?.addEventListener('change', renderDocuments)
}

document.getElementById('documents-permission-user')?.addEventListener('change', () => {
  applySelectedPermissionToForm()
  clearWorkbenchViewRuleForm()
})
document.getElementById('documents-save-permission')?.addEventListener('click', () => void saveWorkbenchPermission())
document.getElementById('documents-load-view-rule')?.addEventListener('click', () => void loadWorkbenchViewRule())
document.getElementById('documents-save-view-rule')?.addEventListener('click', () => void saveWorkbenchViewRule())
document.getElementById('documents-delete-view-rule')?.addEventListener('click', () => void deleteWorkbenchViewRule())
document.getElementById('download-document')?.addEventListener('click', downloadSelectedDocument)
document.getElementById('documents-logout')?.addEventListener('click', logout)

void loadDocumentsShell()
