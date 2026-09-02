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
  '升级',
  '修订',
  '当前版本'
]

export const detectVersionIntent = (query) => {
  const text = typeof query === 'string' ? query.trim() : ''
  if (!text) return false
  return VERSION_INTENT_KEYWORDS.some((keyword) => text.includes(keyword))
}
