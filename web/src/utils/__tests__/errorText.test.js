/**
 * 错误文案解析：对话链路 error_type、后端错误码、登录失败三态、状态码分支。
 *
 * 覆盖 docs/vibe/2026-09-22-error-message-usability.md 的 A3~A6、A8：
 * 用户看到的必须是可读文案，不能是后端内部枚举、Python 异常原文或后端写死的中文。
 */
import assert from 'node:assert/strict'

import { i18n } from '@/i18n'
import {
  ErrorHandler,
  resolveChatErrorText,
  resolveErrorCodeText,
  resolveLoginFailure
} from '../errorHandler.js'

const withLocale = (locale, run) => {
  const original = i18n.global.locale.value
  i18n.global.locale.value = locale
  try {
    run()
  } finally {
    i18n.global.locale.value = original
  }
}

// A3：内容被拦截、智能体不存在这两条后端已写好的原因，各自映射到对应文案
const testChatErrorType = () => {
  assert.equal(resolveChatErrorText('content_guard_blocked'), '检测到敏感内容，已中断输出')
  assert.equal(resolveChatErrorText('interrupted'), '回答生成已中断')
  assert.equal(resolveChatErrorText('resume_interrupted'), '回答生成已中断')
  assert.equal(resolveChatErrorText('invalid_agent'), '智能体获取失败')

  // 未预期异常：后端已把 Python 异常原文从 error_message 摘掉，这里也不该显示内部枚举
  assert.equal(resolveChatErrorText('unexpected_error'), '生成过程中出现异常')
  assert.equal(resolveChatErrorText('worker_error'), '生成过程中出现异常')
  assert.equal(resolveChatErrorText('resume_error'), '生成过程中出现异常')

  // 未知类型才退回后端说明，连说明都没有才兜底「未知错误」
  assert.equal(resolveChatErrorText('some_new_type', '后端说明'), '后端说明')
  assert.equal(resolveChatErrorText('some_new_type'), '未知错误')
  assert.equal(
    resolveChatErrorText('content_guard_blocked', '输入内容包含敏感词'),
    '检测到敏感内容，已中断输出'
  )

  // 英文界面不能漏出中文
  withLocale('en-US', () => {
    assert.equal(
      resolveChatErrorText('content_guard_blocked'),
      'Sensitive content detected, output interrupted'
    )
    assert.equal(resolveChatErrorText('some_new_type'), 'Unknown error')
  })
}

// A6：错误码取本地化文案，未知码返回 null 交给调用方兜底
const testErrorCode = () => {
  assert.equal(resolveErrorCodeText('token_expired'), '登录已过期，请重新登录')
  assert.equal(resolveErrorCodeText('account_deactivated'), '该账户已注销，请联系管理员')
  assert.equal(
    resolveErrorCodeText('user_not_bound_to_department'),
    '当前账号未绑定部门，请联系管理员'
  )
  assert.equal(resolveErrorCodeText('unknown_code'), null)
  assert.equal(resolveErrorCodeText(null), null)

  withLocale('en-US', () => {
    assert.equal(
      resolveErrorCodeText('account_deactivated'),
      'This account has been deactivated, please contact an administrator'
    )
  })
}

// 版本链路的 8 个业务码：先前 detail.message 就是码本身，界面上直接显示 UPDATE_IN_PROGRESS
const testVersionErrorCode = () => {
  const versionCodes = [
    'SAME_CONTENT',
    'VERSION_NOT_NEWER',
    'VERSION_CHANGED',
    'UPDATE_IN_PROGRESS',
    'CONFLICT_REVIEW_REQUIRED',
    'VERSION_NOT_FOUND',
    'CANNOT_DETACH_CURRENT_VERSION',
    'VERSION_FAMILY_HAS_NO_CURRENT'
  ]

  for (const code of versionCodes) {
    const zh = resolveErrorCodeText(code)
    // 取不到文案就会退回后端的 detail.message，那个值仍是裸码，用户看到的就是这串大写英文
    assert.ok(zh && zh !== code, `${code} 缺少可读文案`)
    withLocale('en-US', () => {
      const en = resolveErrorCodeText(code)
      assert.ok(en && en !== code, `${code} 缺少英文文案`)
    })
  }

  assert.equal(
    resolveErrorCodeText('UPDATE_IN_PROGRESS'),
    '该文档已有一次版本更新正在进行，请等它完成后再提交'
  )
}

// A4 / A5：凭证错、锁定、账户注销三种失败各取对应文案；锁定时长只认响应头
const testLoginFailure = () => {
  const lockHeaders = (seconds) => ({
    get: (name) => (name === 'X-Lock-Remaining' ? String(seconds) : null)
  })

  // 凭证错误：不带码，落地到本地化文案，不显示后端的中文 detail
  assert.deepEqual(resolveLoginFailure({ status: 401, message: '登录失败' }), {
    lockSeconds: 0,
    text: '登录失败，请检查用户名和密码'
  })

  // 账户已注销：403 + 错误码
  assert.deepEqual(resolveLoginFailure({ status: 403, code: 'account_deactivated' }), {
    lockSeconds: 0,
    text: '该账户已注销，请联系管理员'
  })

  // 锁定：秒数来自响应头，文案交给调用方按倒计时模板渲染
  assert.deepEqual(resolveLoginFailure({ status: 423, headers: lockHeaders(300) }), {
    lockSeconds: 300,
    text: null
  })

  // 423 但没有响应头时退回本地化的锁定文案（不再从中文 error.message 里抠秒数）
  assert.deepEqual(resolveLoginFailure({ status: 423, message: '账户已被锁定 300 秒' }), {
    lockSeconds: 0,
    text: '账户已被锁定'
  })

  withLocale('en-US', () => {
    assert.equal(
      resolveLoginFailure({ status: 401 }).text,
      'Login failed, check your username and password'
    )
    assert.equal(resolveLoginFailure({ status: 423, headers: lockHeaders(60) }).lockSeconds, 60)
  })
}

// A8：handleNetworkError 的状态码分支取 error.response.status
const testNetworkErrorStatus = () => {
  const captured = []
  const original = ErrorHandler.handleError
  ErrorHandler.handleError = (error, context, options) => {
    captured.push(options.customMessage)
    return error
  }

  try {
    ErrorHandler.handleNetworkError({ response: { status: 401 } })
    ErrorHandler.handleNetworkError({ response: { status: 403 } })
    ErrorHandler.handleNetworkError({ response: { status: 500 } })
    ErrorHandler.handleNetworkError({ code: 'NETWORK_ERROR' })
    ErrorHandler.handleNetworkError({ response: { status: 400 } })
  } finally {
    ErrorHandler.handleError = original
  }

  assert.deepEqual(captured, [
    '认证失败，请重新登录',
    '权限不足，无法执行此操作',
    '服务器错误，请稍后重试',
    '网络连接失败，请检查网络设置',
    null
  ])
}

// getErrorMessage：带 error_type 的错误走对话文案，不再笼统成「流式处理失败」
const testGetErrorMessage = () => {
  assert.equal(
    ErrorHandler.getErrorMessage({ error_type: 'invalid_agent' }, '流式处理'),
    '智能体获取失败'
  )
  assert.equal(
    ErrorHandler.getErrorMessage({ message: '具体原因' }, '流式处理'),
    '流式处理失败: 具体原因'
  )
  assert.equal(ErrorHandler.getErrorMessage({}, '流式处理'), '流式处理失败')
}

testChatErrorType()
testErrorCode()
testVersionErrorCode()
testLoginFailure()
testNetworkErrorStatus()
testGetErrorMessage()

console.log('✅ errorText: 对话错误文案 / 后端错误码 / 登录失败三态 / 状态码分支')
