import assert from 'node:assert/strict'

import {
  getUnavailableFormatMessage,
  normalizeDocumentFormatCapabilities
} from '../document_format_capabilities.js'

const capabilities = normalizeDocumentFormatCapabilities([
  {
    extension: 'DOC',
    enabled: false,
    requires_converter: true,
    availability: 'converter_unavailable',
    reason: 'LibreOffice 旧 Office 转换服务未安装或不可用'
  },
  {
    extension: '.gif',
    enabled: true,
    requires_converter: false,
    availability: 'available',
    reason: null
  },
  {
    extension: '.DOC',
    enabled: true,
    requires_converter: true,
    availability: 'available'
  }
])

assert.deepEqual(capabilities, [
  {
    extension: '.doc',
    enabled: false,
    requires_converter: true,
    availability: 'converter_unavailable',
    reason: 'LibreOffice 旧 Office 转换服务未安装或不可用'
  },
  {
    extension: '.gif',
    enabled: true,
    requires_converter: false,
    availability: 'available',
    reason: ''
  }
])
assert.equal(
  getUnavailableFormatMessage(capabilities),
  '.DOC：LibreOffice 旧 Office 转换服务未安装或不可用'
)
