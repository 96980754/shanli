/**
 * 版本意图识别（纯前端规则，不做 LLM 预判门禁）。
 *
 * 命中以下词表任一即视为用户问题可能涉及「文档版本」：
 * 普通回答仍基于当前版本；此时在回答下方渲染澄清注记与对比引导，
 * 避免用户误以为历史版本内容参与了检索。
 */
const VERSION_INTENT_KEYWORDS = [
  '版本',
  '旧版',
  '历史版本',
  '历史',
  '以前',
  '之前',
  '原先',
  '当时',
  '改了什么',
  '变了什么',
  '区别',
  '差异',
  '对比',
  '相比',
  '比较',
  '新旧',
  '升级',
  '修订',
  '当前版本'
]

// 问句中同时出现两个带点版本号且用「和/与」相连（如「1.1 和 1.2」「v2.1 与 v2.0」），
// 通常在做版本对比——词表命中不到的「…1.1有什么内容？和1.2相比呢」类表述由它兜住。
const VERSION_NUMBER_PATTERN = /(?:^|[^\d.])(\d+(?:\.\d+)+)(?=[^\d.]|$)/g
const VERSION_JOIN_WORD_PATTERN = /和|与/

const hasVersionNumberPair = (text) => {
  const numbers = text.match(VERSION_NUMBER_PATTERN) || []
  return numbers.length >= 2 && VERSION_JOIN_WORD_PATTERN.test(text)
}

export const detectVersionIntent = (query) => {
  const text = typeof query === 'string' ? query.trim() : ''
  if (!text) return false
  return (
    VERSION_INTENT_KEYWORDS.some((keyword) => text.includes(keyword)) || hasVersionNumberPair(text)
  )
}
