let authProfile = null
let knowledgeBases = []
let conversations = []
let messages = []
let activeKbId = null
let activeConversationId = null
let activeMessageId = null
let asking = false

async function requestJson(path, options = {}) {
  const response = await fetch(path, options)
  if (!response.ok) {
    return null
  }
  return response.json()
}

async function authorizedJson(path, options = {}) {
  const token = localStorage.getItem('session_token')
  const headers = { ...(options.headers || {}) }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  return requestJson(path, { ...options, headers })
}

function setQaMessage(message) {
  const node = document.getElementById('qa-message')
  if (node) {
    node.textContent = message
  }
}

function renderUser(profile) {
  const node = document.getElementById('qa-user')
  if (node) {
    node.textContent = profile?.username ? `当前用户：${profile.username}` : '未登录'
  }
}

function renderKnowledgeBases(items) {
  const select = document.getElementById('qa-kb-select')
  if (!select) {
    return
  }
  select.innerHTML = ''
  for (const item of items) {
    const option = document.createElement('option')
    option.value = item.id
    option.textContent = item.name
    select.appendChild(option)
  }
  activeKbId = items[0]?.id || null
  if (!activeKbId) {
    setQaMessage('暂无可用知识库。')
  }
}

function renderConversations(items) {
  const container = document.getElementById('qa-conversation-list')
  if (!container) {
    return
  }
  container.innerHTML = ''
  if (!items.length) {
    container.textContent = '暂无历史会话。'
    return
  }
  for (const item of items) {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = item.title
    button.addEventListener('click', () => {
      void loadConversationMessages(item.id)
    })
    container.appendChild(button)
  }
}

function renderMessages(items) {
  const container = document.getElementById('qa-message-list')
  if (!container) {
    return
  }
  container.innerHTML = ''
  if (!items.length) {
    container.textContent = '暂无历史消息。'
    return
  }
  for (const item of items) {
    const row = document.createElement('div')
    const question = document.createElement('p')
    question.textContent = `问：${item.question}`
    row.appendChild(question)
    const answer = document.createElement('p')
    answer.textContent = `答：${item.answer}`
    row.appendChild(answer)
    container.appendChild(row)
  }
}

async function loadConversations() {
  if (!activeKbId) {
    conversations = []
    renderConversations(conversations)
    return
  }
  const result = await authorizedJson(`/api/qa/conversations?kb_id=${activeKbId}`)
  conversations = result?.items || []
  renderConversations(conversations)
}

async function loadConversationMessages(conversationId) {
  const result = await authorizedJson(`/api/qa/conversations/${conversationId}/messages`)
  messages = result?.items || []
  activeConversationId = conversationId
  renderMessages(messages)
  const lastMessage = messages[messages.length - 1]
  activeMessageId = lastMessage?.id || null
  renderAnswer(lastMessage?.answer || '')
  renderSources(lastMessage?.sources || [])
}

function renderAnswer(answer) {
  const node = document.getElementById('qa-answer-content')
  if (node) {
    node.textContent = answer || ''
  }
}

function renderSources(items) {
  const container = document.getElementById('qa-sources')
  if (!container) {
    return
  }
  container.innerHTML = ''
  for (const item of items || []) {
    const row = document.createElement('div')

    const title = document.createElement('p')
    title.textContent = item.doc_title || '未知来源'
    row.appendChild(title)

    const content = document.createElement('p')
    content.textContent = item.content || ''
    row.appendChild(content)

    const score = document.createElement('p')
    score.textContent = item.score === undefined ? '' : `分数：${item.score}`
    row.appendChild(score)

    container.appendChild(row)
  }
}

async function submitQuestion() {
  if (!activeKbId || asking) {
    return
  }
  const input = document.getElementById('qa-question')
  const question = input?.value?.trim()
  if (!question) {
    setQaMessage('请输入问题。')
    return
  }
  asking = true
  const result = await authorizedJson('/api/qa/ask/sync', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      kb_id: String(activeKbId),
      question,
      conversation_id: activeConversationId,
    }),
  })
  asking = false
  if (!result?.answer) {
    setQaMessage('提问失败。')
    return
  }
  activeConversationId = result.conversation_id || activeConversationId
  activeMessageId = result.message_id || null
  renderAnswer(result.answer)
  renderSources(result.sources || [])
  const feedbackText = document.getElementById('qa-feedback-text')
  if (feedbackText) {
    feedbackText.value = ''
  }
  await loadConversations()
  if (activeConversationId) {
    await loadConversationMessages(activeConversationId)
  }
  setQaMessage('回答已生成。')
}

async function submitFeedback(isHelpful) {
  if (!activeMessageId) {
    setQaMessage('请先提问。')
    return
  }
  const feedbackText = document.getElementById('qa-feedback-text')
  const result = await authorizedJson('/api/qa/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message_id: activeMessageId,
      is_helpful: isHelpful,
      feedback_text: feedbackText?.value || '',
    }),
  })
  if (!result?.saved) {
    setQaMessage('反馈提交失败。')
    return
  }
  setQaMessage(isHelpful ? '感谢反馈。' : '已记录问题，管理员会处理。')
}

async function loadQaShell() {
  authProfile = await authorizedJson('/api/auth/me')
  renderUser(authProfile)
  const result = await authorizedJson('/api/kb')
  knowledgeBases = result?.items || []
  renderKnowledgeBases(knowledgeBases)
  await loadConversations()
}

window.addEventListener('DOMContentLoaded', () => {
  const kbSelect = document.getElementById('qa-kb-select')
  if (kbSelect) {
    kbSelect.addEventListener('change', (event) => {
      activeKbId = event.target.value
      activeConversationId = null
      activeMessageId = null
      messages = []
      renderMessages(messages)
      renderAnswer('')
      renderSources([])
      void loadConversations()
    })
  }

  const submitButton = document.getElementById('qa-submit')
  if (submitButton) {
    submitButton.addEventListener('click', () => {
      void submitQuestion()
    })
  }

  const helpfulButton = document.getElementById('qa-feedback-helpful')
  if (helpfulButton) {
    helpfulButton.addEventListener('click', () => {
      void submitFeedback(true)
    })
  }

  const unhelpfulButton = document.getElementById('qa-feedback-unhelpful')
  if (unhelpfulButton) {
    unhelpfulButton.addEventListener('click', () => {
      void submitFeedback(false)
    })
  }

  if (document.getElementById('qa-kb-select')) {
    void loadQaShell()
  }
})
