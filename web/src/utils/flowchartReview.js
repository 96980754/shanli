export const isPdfFlowchartFile = (file) =>
  Boolean(
    file && /\.pdf$/i.test(file.name || '') && (!file.type || file.type === 'application/pdf')
  )

export const isFlowchartDraftDirty = (savedMarkdown, currentMarkdown) =>
  currentMarkdown !== savedMarkdown

export const canEditFlowchartDraft = (preview, canManage) =>
  Boolean(
    preview &&
    canManage &&
    !preview.readonly &&
    !preview.confirmed_at &&
    ['flowchart_waiting_confirmation', 'error_flowchart_parsing'].includes(preview.status)
  )

export const getFlowchartWarningKeys = (metadata) => {
  const warnings = new Set([
    ...(metadata?.warnings || []),
    ...(metadata?.pages || []).flatMap((page) => page.warnings || [])
  ])
  if (warnings.has('FLOWCHART_RENDER_DPI_REDUCED') && warnings.has('FLOWCHART_COMPLEX_PAGE')) {
    return [
      'flowchart.warningReducedComplex',
      ...(warnings.has('FLOWCHART_EXTREME_ASPECT_RATIO') ? ['flowchart.warningAspect'] : [])
    ]
  }
  const result = []
  if (warnings.has('FLOWCHART_RENDER_DPI_REDUCED')) result.push('flowchart.warningReduced')
  if (warnings.has('FLOWCHART_COMPLEX_PAGE')) result.push('flowchart.warningComplex')
  if (warnings.has('FLOWCHART_EXTREME_ASPECT_RATIO')) result.push('flowchart.warningAspect')
  return result
}
