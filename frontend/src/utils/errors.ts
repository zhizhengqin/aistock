/** Extract a displayable message from an API error (detail may be a structured object). */
export function errMsg(err: any, fallback: string): string {
  const detail = err?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message
  return fallback
}
