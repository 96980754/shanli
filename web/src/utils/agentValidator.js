/**
 * AgentID验证工具类
 * 统一处理AgentID相关的验证逻辑
 */
import { i18n } from '@/i18n'

export class AgentValidator {
  /**
   * 验证AgentID是否存在
   * @param {string} agentId - 要验证的AgentID
   * @param {string} operation - 操作名称，用于错误提示
   * @returns {boolean} 验证是否通过
   */
  static validateAgentId(agentId, operation = i18n.global.t('errors.operation')) {
    if (!agentId) {
      console.warn(`未指定AgentID，无法${operation}`) // i18n-ignore
      return false
    }
    return true
  }

  /**
   * 验证AgentID并显示错误提示
   * @param {string} agentId - 要验证的AgentID
   * @param {string} operation - 操作名称
   * @param {Function} errorHandler - 错误处理函数
   * @returns {boolean} 验证是否通过
   */
  static validateAgentIdWithError(agentId, operation, errorHandler) {
    if (!agentId) {
      const message = i18n.global.t('validation.missingAgentIdCannot', { operation })
      if (errorHandler) {
        errorHandler(message)
      }
      return false
    }
    return true
  }

  /**
   * 验证对话相关操作的前置条件
   * @param {string} agentId - AgentID
   * @param {string} chatId - 对话ID（可选）
   * @param {string} operation - 操作名称
   * @param {Function} errorHandler - 错误处理函数
   * @returns {boolean} 验证是否通过
   */
  static validateChatOperation(agentId, chatId, operation, errorHandler) {
    // 验证AgentID
    if (!this.validateAgentIdWithError(agentId, operation, errorHandler)) {
      return false
    }

    // 如果需要验证chatId
    if (chatId !== undefined && !chatId) {
      const message = i18n.global.t('validation.selectChatFirst')
      if (errorHandler) {
        errorHandler(message)
      }
      return false
    }

    return true
  }

  /**
   * 验证重命名操作的参数
   * @param {string} chatId - 对话ID
   * @param {string} title - 新标题
   * @param {string} agentId - AgentID
   * @param {Function} errorHandler - 错误处理函数
   * @returns {boolean} 验证是否通过
   */
  static validateRenameOperation(chatId, title, agentId, errorHandler) {
    // 验证基本参数
    if (!chatId || !title) {
      const message = i18n.global.t('validation.missingChatInfoCannotRename')
      if (errorHandler) {
        errorHandler(message)
      }
      return false
    }

    // 验证标题不为空
    if (!title.trim()) {
      const message = i18n.global.t('conversation.modal.emptyTitle')
      if (errorHandler) {
        errorHandler(message)
      }
      return false
    }

    // 验证AgentID
    return this.validateAgentIdWithError(agentId, i18n.global.t('errors.ops.rename'), errorHandler)
  }

  /**
   * 验证分享操作的前置条件
   * @param {string} chatId - 对话ID
   * @param {Object} agent - 当前智能体对象
   * @param {Function} errorHandler - 错误处理函数
   * @returns {boolean} 验证是否通过
   */
  static validateShareOperation(chatId, agent, errorHandler) {
    if (!chatId || !agent) {
      const message = i18n.global.t('validation.selectChatFirst')
      if (errorHandler) {
        errorHandler(message)
      }
      return false
    }
    return true
  }

  /**
   * 验证加载操作的前置条件
   * @param {string} agentId - AgentID
   * @param {string} operation - 操作名称
   * @returns {boolean} 验证是否通过
   */
  static validateLoadOperation(agentId, operation = i18n.global.t('errors.ops.loadState')) {
    if (!agentId) {
      console.warn(`未指定AgentID，无法${operation}`) // i18n-ignore
      return false
    }
    return true
  }
}
