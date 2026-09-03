// 反馈点踩原因展示工具：新提交存稳定 code（可选 \n detail），历史行可能存中/英文
// 本地化标签或自由文本。读取时统一按 code 本地化，无法归类的整段原样展示。

// 与 RefsComponent.dislikeReasonOptions / 后端 FEEDBACK_REASON_OPTIONS 对齐
export const FEEDBACK_REASON_KEYS = {
  answer_incorrect: 'refs.reasonAnswerIncorrect',
  outdated: 'refs.reasonOutdated',
  irrelevant: 'refs.reasonIrrelevant',
  other: 'refs.reasonOther'
}

/** code → i18n key；非已知 code 返回 null */
export function feedbackReasonKey(code) {
  return Object.prototype.hasOwnProperty.call(FEEDBACK_REASON_KEYS, code)
    ? FEEDBACK_REASON_KEYS[code]
    : null
}

/**
 * 拆分存储文本：已知 code（首行）→ { code, detail }；否则历史标签/自由文本 → { label: 原文 }。
 */
export function parseFeedbackReason(raw) {
  const text = String(raw || '').trim()
  if (!text) return { code: null, label: null, detail: null }
  const sep = text.indexOf('\n')
  const firstLine = (sep >= 0 ? text.slice(0, sep) : text).trim()
  if (feedbackReasonKey(firstLine)) {
    const detail = sep >= 0 ? text.slice(sep + 1).trim() || null : null
    return { code: firstLine, label: null, detail }
  }
  return { code: null, label: text, detail: null }
}

/**
 * 渲染可读文本：已知 code → t(key)（+ detail 附后）；否则原样返回历史文本。
 */
export function formatFeedbackReason(raw, t) {
  const parsed = parseFeedbackReason(raw)
  if (parsed.code) {
    const label = t(feedbackReasonKey(parsed.code))
    return parsed.detail ? `${label}\n${parsed.detail}` : label
  }
  return parsed.label || ''
}
