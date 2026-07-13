let activeKbId = null
let selectedDocument = null

function authorizationHeaders() {
  const token = localStorage.getItem('session_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function message(text) {
  document.getElementById('documents-message').textContent = text
}

async function responseJson(response) {
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    message(body.detail || '请求失败')
    return null
  }
  return body
}

async function loadKnowledgeBases() {
  const response = await fetch('/api/kb', { headers: authorizationHeaders() })
  const body = await responseJson(response)
  if (!body) return
  const container = document.getElementById('documents-kb-list')
  container.replaceChildren()
  document.getElementById('documents-kb-empty-state').hidden = body.items.length > 0
  body.items.forEach((kb) => {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = kb.name
    button.addEventListener('click', () => {
      activeKbId = kb.id
      loadDocuments()
    })
    container.append(button)
  })
}

async function loadDocuments() {
  if (activeKbId === null) return
  const response = await fetch(`/api/kb/${activeKbId}/documents`, { headers: authorizationHeaders() })
  const body = await responseJson(response)
  if (!body) return
  const container = document.getElementById('documents-list')
  container.replaceChildren()
  document.getElementById('documents-empty-state').hidden = body.items.length > 0
  body.items.forEach((documentItem) => {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = documentItem.title
    button.addEventListener('click', () => loadDocumentDetail(documentItem.id))
    container.append(button)
  })
}

async function loadDocumentDetail(docId) {
  const response = await fetch(`/api/kb/${activeKbId}/documents/${docId}`, { headers: authorizationHeaders() })
  const body = await responseJson(response)
  if (!body) return
  selectedDocument = body
  document.getElementById('documents-detail-title').textContent = body.title
  document.getElementById('documents-detail-metadata').textContent = `${body.original_filename || body.title} · ${body.file_size || 0} bytes`
  document.getElementById('download-document').disabled = body.download_available !== true
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

document.getElementById('download-document').addEventListener('click', downloadSelectedDocument)
loadKnowledgeBases()
