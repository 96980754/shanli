function setMessage(text) {
  document.getElementById('login-message').textContent = text
}

async function request(path, payload) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(body.detail || '请求失败')
  return body
}

function showRegistration(show) {
  document.getElementById('login-form').hidden = show
  document.getElementById('registration-form').hidden = !show
  document.getElementById('show-registration').parentElement.hidden = show
  setMessage(show ? '注册后需要由管理员授予知识库权限。' : '登录后访问已获授权的知识库。')
}

document.getElementById('login-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  const form = event.currentTarget
  try {
    const result = await request('/api/auth/login', {
      username: form.username.value,
      password: form.password.value,
    })
    localStorage.setItem('session_token', result.token)
    window.location.href = '/documents'
  } catch (error) {
    setMessage(error.message)
  }
})

document.getElementById('registration-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  const form = event.currentTarget
  try {
    await request('/api/auth/register', {
      username: form.username.value,
      password: form.password.value,
      password_confirmation: form.password_confirmation.value,
    })
    document.querySelector('#login-form [name="username"]').value = form.username.value
    form.reset()
    showRegistration(false)
    setMessage('账号已创建，请登录；管理员授权后才能访问知识库。')
  } catch (error) {
    setMessage(error.message)
  }
})

document.getElementById('show-registration').addEventListener('click', () => showRegistration(true))
document.getElementById('show-login').addEventListener('click', () => showRegistration(false))
