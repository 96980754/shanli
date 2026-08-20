import assert from 'node:assert/strict'
import {
  buildIndustrySolutionPayload,
  buildIndustrySolutionQuery,
  normalizeIndustrySolutionProducts
} from '../../utils/industrySolution.js'

assert.deepEqual(normalizeIndustrySolutionProducts([' 产品A ', '产品B', '产品b', '']), [
  '产品A',
  '产品B'
])

const payload = buildIndustrySolutionPayload({
  industry: ' 智慧园区 ',
  requirement: ' 统一管理终端 ',
  products: ['产品A', '产品B']
})

assert.deepEqual(payload, {
  industry: '智慧园区',
  requirement: '统一管理终端',
  products: ['产品A', '产品B']
})
assert.match(buildIndustrySolutionQuery(payload), /产品：产品A、产品B/)
assert.match(buildIndustrySolutionQuery(payload), /需求：统一管理终端/)

console.log('industry solution helpers: PASS')
