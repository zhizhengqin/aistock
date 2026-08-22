export function formatSignedPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '暂无'
  return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(2)}%`
}

export function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '暂无'
  const abs = Math.abs(value)
  if (abs >= 100000000) return `${(value / 100000000).toFixed(2)}亿`
  if (abs >= 10000) return `${(value / 10000).toFixed(2)}万`
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return '暂无'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN')
}

export function trendLabel(value: string | undefined): string {
  return ({ new: '新晋', heating: '升温', cooling: '降温', steady: '平稳', insufficient_history: '历史不足' } as Record<string, string>)[value || ''] || '历史不可比'
}

export function signedClass(value: number | null | undefined): 'up' | 'down' | '' {
  if (value === null || value === undefined) return ''
  return value >= 0 ? 'up' : 'down'
}
