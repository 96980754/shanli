import { message } from 'ant-design-vue'
import { i18n } from '@/i18n'

/**
 * 对话链路错误类型（后端 error_type）到文案键的映射。
 * error_type 是后端内部枚举，直接显示给用户看不懂，一律换成可读文案。
 */
const CHAT_ERROR_TEXT_KEYS = {
  interrupted: 'chat.errorInterrupted',
  resume_interrupted: 'chat.errorInterrupted',
  content_guard_blocked: 'chat.errorContentBlocked',
  invalid_agent: 'chat.agentFetchFailed',
  unexpected_error: 'chat.errorUnexpected',
  resume_error: 'chat.errorUnexpected',
  worker_error: 'chat.errorUnexpected',
  retryable_worker_error: 'chat.errorUnexpected'
}

/**
 * 把对话链路的错误转成给用户看的文案。
 * 已知 error_type 一律用本地化文案：后端的 error_message 多是中文写死的，英文界面下不能直出；
 * 只有遇到本地化不了的未知类型时，才退回 error_message，最后兜底「未知错误」。
 */
export const resolveChatErrorText = (errorType, errorMessage) => {
  const key = CHAT_ERROR_TEXT_KEYS[errorType]
  if (key) return i18n.global.t(key)
  return errorMessage || i18n.global.t('chat.errorUnknown')
}

/**
 * 后端错误码（detail={"code": ...}）到本地化文案，没有对应文案时返回 null 由调用方兜底。
 * 码表见 backend/server/utils/auth_middleware.py 与 auth_router.py。
 */
export const resolveErrorCodeText = (code) => {
  const key = `errorCodes.${code}`
  return code && i18n.global.te(key) ? i18n.global.t(key) : null
}

/**
 * 登录失败 → 给用户看的文案。登录接口有三种失败形状：423 锁定、带错误码的 403（账户已注销）、
 * 其余（凭证错误等）。后端 detail 是中文写死的，一律不直出。
 *
 * @returns {{ lockSeconds: number, text: string|null }} lockSeconds > 0 时 text 为 null，
 *          表示调用方要用 login.locked 模板带上剩余时间；否则直接用 text。
 */
export const resolveLoginFailure = (error) => {
  // 剩余秒数只认响应头 X-Lock-Remaining（后端两个 423 抛出点都带），
  // 不再从中文错误文案里正则抠数字——英文界面下抠不到
  const lockSeconds =
    error?.status === 423 ? parseInt(error.headers?.get?.('X-Lock-Remaining')) || 0 : 0

  if (lockSeconds > 0) return { lockSeconds, text: null }
  if (error?.status === 423) return { lockSeconds: 0, text: i18n.global.t('login.errors.locked') }
  return {
    lockSeconds: 0,
    text: resolveErrorCodeText(error?.code) || i18n.global.t('login.errors.badCredentials')
  }
}

/**
 * 统一错误处理工具类
 */
export class ErrorHandler {
  /**
   * 处理通用错误
   * @param {Error} error - 错误对象
   * @param {string} context - 错误上下文
   * @param {Object} options - 配置选项
   */
  static handleError(error, context = i18n.global.t('errors.operation'), options = {}) {
    const {
      showMessage = true,
      logToConsole = true,
      customMessage = null,
      severity = 'error'
    } = options

    // 控制台日志
    if (logToConsole) {
      console.error(`${context}失败:`, error) // i18n-ignore
    }

    // 用户提示
    if (showMessage) {
      const displayMessage = customMessage || this.getErrorMessage(error, context)

      switch (severity) {
        case 'warning':
          message.warning(displayMessage)
          break
        case 'info':
          message.info(displayMessage)
          break
        case 'error':
        default:
          message.error(displayMessage)
          break
      }
    }

    return error
  }

  /**
   * 获取错误消息
   * @param {Error} error - 错误对象
   * @param {string} context - 错误上下文
   * @returns {string} 错误消息
   */
  static getErrorMessage(error, context) {
    // 对话链路的错误带 error_type，用它取可读文案，不要退回泛化的「XX失败」
    if (error?.error_type) {
      return resolveChatErrorText(error.error_type, error.error_message)
    }
    if (error?.message) {
      return i18n.global.t('errors.contextFailedWithDetail', { context, message: error.message })
    }
    return i18n.global.t('errors.contextFailed', { context })
  }

  /**
   * 处理网络请求错误
   * @param {Error} error - 错误对象
   * @param {string} context - 错误上下文
   */
  static handleNetworkError(error, context = i18n.global.t('errors.ops.networkRequest')) {
    let customMessage = null

    // 状态码在 error.response.status 上（apis/base.js 构造的错误对象），不在 error.status
    const status = error?.response?.status

    if (error?.code === 'NETWORK_ERROR') {
      customMessage = i18n.global.t('errors.networkConnectionFailed')
    } else if (status === 401) {
      customMessage = i18n.global.t('common.authFailed')
    } else if (status === 403) {
      customMessage = i18n.global.t('errors.insufficientPermission')
    } else if (status === 404) {
      customMessage = i18n.global.t('errors.resourceNotFound')
    } else if (status >= 500) {
      customMessage = i18n.global.t('errors.serverErrorRetry')
    }

    return this.handleError(error, context, { customMessage })
  }

  /**
   * 处理聊天相关错误
   * @param {Error} error - 错误对象
   * @param {string} operation - 操作类型
   */
  static handleChatError(error, operation) {
    const contextMap = {
      send: i18n.global.t('errors.ops.send'),
      create: i18n.global.t('errors.ops.create'),
      delete: i18n.global.t('errors.ops.delete'),
      rename: i18n.global.t('errors.ops.rename'),
      load: i18n.global.t('errors.ops.load'),
      export: i18n.global.t('errors.ops.export'),
      stream: i18n.global.t('errors.ops.stream')
    }

    const context = contextMap[operation] || operation
    return this.handleError(error, context)
  }

  /**
   * 处理验证错误
   * @param {string} message - 验证错误消息
   */
  static handleValidationError(message) {
    return this.handleError(new Error(message), i18n.global.t('errors.ops.inputValidation'), {
      severity: 'warning',
      customMessage: message
    })
  }

  /**
   * 处理异步操作错误
   * @param {Function} asyncFn - 异步函数
   * @param {string} context - 错误上下文
   * @param {Object} options - 配置选项
   */
  static async handleAsync(asyncFn, context, options = {}) {
    try {
      return await asyncFn()
    } catch (error) {
      this.handleError(error, context, options)
      throw error
    }
  }

  /**
   * 创建错误处理装饰器
   * @param {string} context - 错误上下文
   * @param {Object} options - 配置选项
   */
  static createHandler(context, options = {}) {
    return (error) => this.handleError(error, context, options)
  }
}

/**
 * 快捷方法
 */
export const handleChatError = ErrorHandler.handleChatError.bind(ErrorHandler)
export const handleNetworkError = ErrorHandler.handleNetworkError.bind(ErrorHandler)
export const handleValidationError = ErrorHandler.handleValidationError.bind(ErrorHandler)
export const handleAsync = ErrorHandler.handleAsync.bind(ErrorHandler)

export default ErrorHandler
