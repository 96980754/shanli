import { message } from 'ant-design-vue'
import { i18n } from '@/i18n'

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

    if (error?.code === 'NETWORK_ERROR') {
      customMessage = i18n.global.t('errors.networkConnectionFailed')
    } else if (error?.status === 401) {
      customMessage = i18n.global.t('common.authFailed')
    } else if (error?.status === 403) {
      customMessage = i18n.global.t('errors.insufficientPermission')
    } else if (error?.status === 404) {
      customMessage = i18n.global.t('errors.resourceNotFound')
    } else if (error?.status >= 500) {
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
