import {
  apiAdminGet,
  apiSuperAdminDelete,
  apiSuperAdminGet,
  apiSuperAdminPatch,
  apiSuperAdminPost,
  apiSuperAdminPut
} from './base'

/**
 * Dashboard API模块
 * 用于管理员查看所有用户的对话记录
 */

export const dashboardApi = {
  /**
   * 获取所有对话记录
   * @param {Object} params - 查询参数
   * @param {string} params.uid - 用户 UID 过滤
   * @param {string} params.agent_id - 智能体ID过滤
   * @param {string} params.status - 状态过滤 (active/deleted/all)
   * @param {number} params.limit - 每页数量
   * @param {number} params.offset - 偏移量
   * @returns {Promise<Array>} - 对话列表
   */
  getConversations: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.uid) queryParams.append('uid', params.uid)
    if (params.agent_id) queryParams.append('agent_id', params.agent_id)
    if (params.status) queryParams.append('status', params.status)
    if (params.limit) queryParams.append('limit', params.limit)
    if (params.offset) queryParams.append('offset', params.offset)

    return apiAdminGet(`/api/dashboard/conversations?${queryParams.toString()}`)
  },

  /**
   * 获取对话详情
   * @param {string} threadId - 对话线程ID
   * @returns {Promise<Object>} - 对话详情
   */
  getConversationDetail: (threadId) => {
    return apiAdminGet(`/api/dashboard/conversations/${threadId}`)
  },

  /**
   * 获取Dashboard统计信息
   * @param {Object} params - 查询参数
   * @param {string} params.start_date - 起始日（北京日期 YYYY-MM-DD，含当日）
   * @param {string} params.end_date - 结束日（北京日期 YYYY-MM-DD，含当日）
   * @returns {Promise<Object>} - 统计信息
   */
  getStats: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.start_date) queryParams.append('start_date', params.start_date)
    if (params.end_date) queryParams.append('end_date', params.end_date)
    const query = queryParams.toString()
    return apiAdminGet(`/api/dashboard/stats${query ? `?${query}` : ''}`)
  },

  /**
   * 获取用户反馈列表（分页）
   * @param {Object} params - 查询参数
   * @param {string} params.rating - 反馈类型过滤 (like/dislike)
   * @param {string} params.status - 处理状态过滤 (pending/processed/ignored)
   * @param {string} params.keyword - 关键词（匹配消息原文/会话标题/用户）
   * @param {string} params.agent_id - 智能体ID过滤
   * @param {number} params.limit - 每页条数
   * @param {number} params.offset - 偏移量
   * @param {string} params.order_by - 排序 (created_desc/created_asc)
   * @returns {Promise<Object>} - { total, items } 反馈列表
   */
  getFeedbacks: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.rating && params.rating !== 'all') queryParams.append('rating', params.rating)
    if (params.status && params.status !== 'all') queryParams.append('status', params.status)
    if (params.keyword) queryParams.append('keyword', params.keyword)
    if (params.agent_id) queryParams.append('agent_id', params.agent_id)
    if (params.limit) queryParams.append('limit', String(params.limit))
    if (params.offset) queryParams.append('offset', String(params.offset))
    if (params.order_by) queryParams.append('order_by', params.order_by)

    return apiAdminGet(`/api/dashboard/feedbacks?${queryParams.toString()}`)
  },

  /**
   * 更新反馈处理状态
   * @param {number} feedbackId - 反馈ID
   * @param {string} status - pending/processed/ignored
   * @returns {Promise<Object>} - { id, status }
   */
  updateFeedbackStatus: (feedbackId, status) =>
    apiSuperAdminPatch(`/api/dashboard/feedbacks/${feedbackId}/status`, { status }),

  getFeedbackTuningContext: (feedbackId) =>
    apiSuperAdminGet(`/api/dashboard/feedbacks/${feedbackId}/tuning-context`),

  saveFeedbackQaPair: (feedbackId, data) =>
    apiSuperAdminPut(`/api/dashboard/feedbacks/${feedbackId}/qa-pair`, data),

  /**
   * 获取人工问答对列表（分页）
   * @param {Object} params - 查询参数
   * @param {string} params.agent_slug - 智能体过滤
   * @param {string} params.source_type - 来源过滤 (feedback/udesk)
   * @param {boolean|string} params.enabled - 启用状态过滤
   * @param {string} params.keyword - 关键词（匹配问题/答案）
   * @param {number} params.limit - 每页条数
   * @param {number} params.offset - 偏移量
   * @returns {Promise<Object>} - { total, items }
   */
  getQaPairs: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.agent_slug) queryParams.append('agent_slug', params.agent_slug)
    if (params.source_type) queryParams.append('source_type', params.source_type)
    if (params.enabled !== '' && params.enabled !== undefined && params.enabled !== null) {
      queryParams.append('enabled', params.enabled)
    }
    if (params.keyword) queryParams.append('keyword', params.keyword)
    if (params.limit) queryParams.append('limit', params.limit)
    if (params.offset) queryParams.append('offset', params.offset)
    return apiSuperAdminGet(`/api/dashboard/qa-pairs?${queryParams.toString()}`)
  },

  /**
   * 批量启用/停用问答对（停用后不再参与命中与召回）
   * @param {number[]} ids - 问答对 ID 列表
   * @param {boolean} enabled - 目标状态
   * @returns {Promise<Object>} - { updated }
   */
  updateQaPairsEnabled: (ids, enabled) =>
    apiSuperAdminPatch('/api/dashboard/qa-pairs/enabled', { ids, enabled }),

  /**
   * 修改单条问答对内容（问题变更会重算精确匹配键，语义向量按需重建）
   * @param {number} id - 问答对 ID
   * @param {Object} data - { question, answer }
   * @returns {Promise<Object>} - { item }
   */
  updateQaPair: (id, data) => apiSuperAdminPatch(`/api/dashboard/qa-pairs/${id}`, data),

  /**
   * 批量删除问答对（不可恢复）
   * @param {number[]} ids - 问答对 ID 列表
   * @returns {Promise<Object>} - { deleted }
   */
  deleteQaPairs: (ids) => apiSuperAdminPost('/api/dashboard/qa-pairs/batch-delete', { ids }),

  /**
   * 客服记录候选知识分页查询（审核页）
   * @param {Object} params - { review_status, dedup_status, domain, keyword, limit, offset }
   * @returns {Promise<Object>} - { total, items }
   */
  getQaCandidates: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.review_status) queryParams.append('review_status', params.review_status)
    if (params.dedup_status) queryParams.append('dedup_status', params.dedup_status)
    if (params.domain) queryParams.append('domain', params.domain)
    if (params.keyword) queryParams.append('keyword', params.keyword)
    if (params.limit) queryParams.append('limit', params.limit)
    if (params.offset) queryParams.append('offset', params.offset)
    return apiSuperAdminGet(`/api/dashboard/qa-candidates?${queryParams.toString()}`)
  },

  /**
   * 候选来源会话的脱敏消息（审核核对 evidence_quote 用）
   * @param {number} candidateId - 候选 ID
   * @returns {Promise<Object>} - { conversation, messages }
   */
  getQaCandidateContext: (candidateId) =>
    apiSuperAdminGet(`/api/dashboard/qa-candidates/${candidateId}/context`),

  /**
   * 采纳入库（enabled=false 落库，需在问答对管理页启用后生效）
   * @param {number} candidateId - 候选 ID
   * @param {Object} data - { agent_slug, question?, answer? }
   * @returns {Promise<Object>} - { item }
   */
  acceptQaCandidate: (candidateId, data) =>
    apiSuperAdminPost(`/api/dashboard/qa-candidates/${candidateId}/accept`, data),

  /**
   * 删除候选（不可恢复，前端须二次确认）
   * @param {number} candidateId - 候选 ID
   * @returns {Promise<Object>} - { deleted }
   */
  deleteQaCandidate: (candidateId) =>
    apiSuperAdminDelete(`/api/dashboard/qa-candidates/${candidateId}`),

  /**
   * 客服会话只读列表（含被确定性筛掉、从未产出候选的会话）
   * @param {Object} params - { keyword, has_candidates, limit, offset }
   * @returns {Promise<Object>} - { total, items: [{ conversation_id, started_at, ended_at,
   *   summarized_at, message_count, customer_message_count, eligible, candidate_count }] }
   *   eligible 为是否通过「实质问答」确定性筛选（与总结链路同一规则）；
   *   message_count 受日志接口一个月窗口限制，早期会话可能只留到尾巴。
   */
  getUdeskConversations: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.keyword) queryParams.append('keyword', params.keyword)
    if (params.has_candidates !== '' && params.has_candidates !== undefined && params.has_candidates !== null) {
      queryParams.append('has_candidates', params.has_candidates)
    }
    if (params.limit) queryParams.append('limit', params.limit)
    if (params.offset) queryParams.append('offset', params.offset)
    return apiSuperAdminGet(`/api/dashboard/udesk-conversations?${queryParams.toString()}`)
  },

  /**
   * 单个客服会话的脱敏消息
   * @param {string} conversationId - 会话 ID
   * @returns {Promise<Object>} - { conversation, messages }
   */
  getUdeskConversation: (conversationId) =>
    apiSuperAdminGet(`/api/dashboard/udesk-conversations/${encodeURIComponent(conversationId)}`),

  /**
   * 获取 Udesk 配置生效概览（含每项来源；不含令牌值）+ 拉取/总结运行状态 + 累计量
   * @returns {Promise<Object>} - 配置项（enabled/subdomain/.../token_configured/ready/sources/missing_fields）
   *   + { counts: { conversations, messages, candidates, candidates_pending },
   *       pull: { running, done, total, last_run_at, last_run_status, last_error,
   *               last_run_conversations, last_run_messages },
   *       summarize: { running, done, total, pending, last_run_at, last_run_status, last_error } }
   *   pull.done/total 是本轮进度（total 随翻页增长，为处理次数口径）；
   *   pull.last_run_conversations/last_run_messages 是本轮**真实新增**行数（与累计量同口径，
   *   首次拉取时两者应相等），不是处理次数——同一会话会因「按创建」「按结束」两遍遍历各处理一次；
   *   两个 running 由各自租约是否仍有效判定，彼此独立。
   */
  getUdeskStatus: () => apiSuperAdminGet('/api/dashboard/udesk-status'),

  /**
   * 手动触发 Udesk 增量拉取（异步执行，返回排队结果）
   * @returns {Promise<Object>} - { queued, job_id }
   */
  triggerUdeskPull: () => apiSuperAdminPost('/api/dashboard/udesk-pull', {}),

  /**
   * 手动触发已拉取会话的 LLM 总结（两步流程第二步；异步执行）
   * @returns {Promise<Object>} - { queued, job_id, pending }
   */
  triggerUdeskSummarize: () => apiSuperAdminPost('/api/dashboard/udesk-summarize', {}),

  /**
   * 获取反馈汇总和点踩原因分布
   * @param {Object} params - 查询参数
   * @param {string} params.agent_id - 智能体ID过滤
   * @returns {Promise<Object>} - 反馈统计信息
   */
  getFeedbackSummary: (params = {}) => {
    const queryParams = new URLSearchParams()
    if (params.agent_id) queryParams.append('agent_id', params.agent_id)
    const query = queryParams.toString()
    return apiAdminGet(`/api/dashboard/feedback-summary${query ? `?${query}` : ''}`)
  },

  // ========== 新增并行API接口 ==========

  /**
   * 获取用户活跃度统计
   * @returns {Promise<Object>} - 用户活跃度统计信息
   */
  getUserStats: () => {
    return apiAdminGet('/api/dashboard/stats/users')
  },

  /**
   * 获取工具调用统计
   * @returns {Promise<Object>} - 工具调用统计信息
   */
  getToolStats: () => {
    return apiAdminGet('/api/dashboard/stats/tools')
  },

  /**
   * 获取知识库统计
   * @returns {Promise<Object>} - 知识库统计信息
   */
  getKnowledgeStats: () => {
    return apiAdminGet('/api/dashboard/stats/knowledge')
  },

  /**
   * 获取AI智能体分析数据
   * @returns {Promise<Object>} - AI智能体分析信息
   */
  getAgentStats: () => {
    return apiAdminGet('/api/dashboard/stats/agents')
  },

  /**
   * 批量获取所有统计数据（并行请求）
   * @returns {Promise<Object>} - 所有统计数据
   */
  getAllStats: async () => {
    try {
      const [basicStats, userStats, toolStats, knowledgeStats, agentStats] = await Promise.all([
        apiAdminGet('/api/dashboard/stats'),
        apiAdminGet('/api/dashboard/stats/users'),
        apiAdminGet('/api/dashboard/stats/tools'),
        apiAdminGet('/api/dashboard/stats/knowledge'),
        apiAdminGet('/api/dashboard/stats/agents')
      ])

      return {
        basic: basicStats,
        users: userStats,
        tools: toolStats,
        knowledge: knowledgeStats,
        agents: agentStats
      }
    } catch (error) {
      console.error('批量获取统计数据失败:', error) // i18n-ignore
      throw error
    }
  },

  /**
   * 获取调用统计时间序列数据
   * @param {string} type - 数据类型 (models/agents/tokens/tools)
   * @param {string} timeRange - 时间范围 (14hours/14days/14weeks)
   * @returns {Promise<Object>} - 时间序列统计数据
   */
  getCallTimeseries: (type = 'models', timeRange = '14days') => {
    return apiAdminGet(`/api/dashboard/stats/calls/timeseries?type=${type}&time_range=${timeRange}`)
  },

  getKnowledgeGaps: (params = {}) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') query.append(key, value)
    })
    return apiSuperAdminGet(`/api/dashboard/knowledge-gaps?${query.toString()}`)
  },

  getKnowledgeGap: (gapId) => apiSuperAdminGet(`/api/dashboard/knowledge-gaps/${gapId}`),

  updateKnowledgeGap: (gapId, data) =>
    apiSuperAdminPatch(`/api/dashboard/knowledge-gaps/${gapId}`, data),

  searchKnowledgeGapAnswer: (gapId) =>
    apiSuperAdminPost(`/api/dashboard/knowledge-gaps/${gapId}/web-search`, {}),

  saveKnowledgeGapQaPair: (gapId, data) =>
    apiSuperAdminPost(`/api/dashboard/knowledge-gaps/${gapId}/save-qa`, data),

  /**
   * 分页查询问答明细（产品线口径：拒答沿用判定域，正常回答按业务线关键词分类）
   * @param {Object} params - start_date/end_date/domain/keyword/limit/offset
   */
  getQaRecords: (params = {}) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') query.append(key, value)
    })
    return apiSuperAdminGet(`/api/dashboard/qa-records?${query.toString()}`)
  },

  /**
   * 按当前筛选条件导出问答明细 CSV（UTF-8 BOM）
   */
  exportQaRecords: (params = {}) => {
    const query = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') query.append(key, value)
    })
    return apiSuperAdminGet(`/api/dashboard/qa-records/export?${query.toString()}`, {}, 'blob')
  }
}
