import { reactive } from 'vue'
import { modelProviderApi } from '@/apis/system_api'
import { i18n } from '@/i18n'

/**
 * 模型状态检查 composable，供 Chat/Embedding/Rerank 模型选择器共用。
 */
export function useModelStatus() {
  const statusMap = reactive({})

  const getStatusIcon = (key) => {
    const status = statusMap[key]
    if (!status) return '○'
    if (status.status === 'available') return '✓'
    if (status.status === 'unavailable') return '✗'
    if (status.status === 'error') return '⚠'
    return '○'
  }

  const getStatusClass = (key) => {
    return statusMap[key]?.status || ''
  }

  const getStatusTooltip = (key) => {
    const status = statusMap[key]
    if (!status) return i18n.global.t('modelStatus.statusUnknown')
    const text =
      {
        available: i18n.global.t('modelStatus.available'),
        unavailable: i18n.global.t('modelStatus.unavailable'),
        error: i18n.global.t('modelStatus.error')
      }[status.status] || i18n.global.t('common.unknown')
    return i18n.global.t('modelStatus.tooltipFormat', {
      text,
      message: status.message || i18n.global.t('modelStatus.noDetail')
    })
  }

  const checkV2Status = async (spec) => {
    try {
      const response = await modelProviderApi.getModelStatusBySpec(spec)
      if (response.data) {
        statusMap[spec] = response.data
      }
    } catch {
      statusMap[spec] = { spec, status: 'error', message: i18n.global.t('modelStatus.checkFailed') }
    }
  }

  const checkV2Statuses = async (models) => {
    for (const model of models || []) {
      await checkV2Status(model.spec)
    }
  }

  return {
    statusMap,
    getStatusIcon,
    getStatusClass,
    getStatusTooltip,
    checkV2Status,
    checkV2Statuses
  }
}
