export const MIN_INDUSTRY_SOLUTION_PRODUCTS = 2
export const MAX_INDUSTRY_SOLUTION_PRODUCTS = 5

export const normalizeIndustrySolutionProducts = (products = []) => {
  const seen = new Set()
  return products.reduce((result, product) => {
    const name = String(product || '').trim()
    const key = name.toLocaleLowerCase()
    if (name && !seen.has(key)) {
      seen.add(key)
      result.push(name)
    }
    return result
  }, [])
}

export const buildIndustrySolutionPayload = ({ industry, requirement, products }) => ({
  industry: String(industry || '').trim(),
  requirement: String(requirement || '').trim(),
  products: normalizeIndustrySolutionProducts(products)
})

export const buildIndustrySolutionQuery = ({ industry, requirement, products }) =>
  `请为“${industry}”场景生成多产品行业解决方案。\n需求：${requirement}\n产品：${products.join('、')}`
