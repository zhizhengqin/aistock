/** Task status helpers shared by all long-running task pollers. */
export function isTaskFailure(status: string | null | undefined): boolean {
  return status === 'failed' || status === 'failed_unknown'
}

export function isTaskTerminal(status: string | null | undefined): boolean {
  return status === 'success' || isTaskFailure(status)
}
