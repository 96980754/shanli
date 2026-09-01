/**
 * 注册 test-alias-loader 的入口（配合 `node --import` 使用）
 * 用法：node --import ./scripts/test-alias-register.mjs <test.js>
 */
import { register } from 'node:module'

register(new URL('./test-alias-loader.mjs', import.meta.url))
