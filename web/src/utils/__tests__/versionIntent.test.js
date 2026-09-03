import assert from 'node:assert/strict'

import { detectVersionIntent } from '../versionIntent.js'

// 命中版本意图
for (const query of [
  '这个文档的版本历史是怎样的',
  '这和旧版有什么区别',
  '想看看历史版本的内容',
  '以前的内容是怎么样的',
  '之前的规则是什么',
  '原先的规定是什么',
  '当时是怎么写的',
  '改了什么内容',
  '哪些地方变了什么',
  'v2 和 v3 的区别',
  '当前版本和修订版差异',
  '有什么变化，版本升级说明',
  '请对比一下新旧版本',
  // 验收③ 真实例句：版本词表命中不到的“…1.1有什么内容？和1.2相比呢”类表述
  '测试文档1.1有什么内容？和1.2相比呢',
  '相比老版，新版优化了什么',
  // 仅凭“两个带点版本号 + 和/与”识别（无词表词）
  '1.1和1.2的内容差别大不大',
  'v2.1 与 v2.0 变动明显吗'
]) {
  assert.equal(detectVersionIntent(query), true, `应命中版本意图: ${query}`)
}

// 未命中（普通提问，不应打扰）
for (const query of [
  '',
  '   ',
  null,
  undefined,
  123,
  '今天天气怎么样',
  '帮我总结一下文档内容',
  '什么是知识库',
  '请推荐适合的问答场景',
  '这份文档提到哪些关键点',
  // 单个版本号（无词表词、无“和/与”并列）不是版本对比意图，不应误触发澄清
  '1.2 主要讲了什么',
  '把第2.1章的目录列出来'
]) {
  assert.equal(detectVersionIntent(query), false, `不应命中版本意图: ${query}`)
}

// 边界：大小写与周边字符不影响中文子串命中
assert.equal(detectVersionIntent('请使用版本对比功能查看'), true)
assert.equal(detectVersionIntent('这不是版本问题'), true) // 词表规则触发，不做语义消歧（预期行为）

console.log('versionIntent tests passed')
