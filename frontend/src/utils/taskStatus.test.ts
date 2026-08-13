import { describe, expect, it } from 'vitest'
import { isTaskFailure, isTaskTerminal } from './taskStatus'

describe('task status polling helpers', () => {
  it('treats failed_unknown as a terminal failure', () => {
    expect(isTaskFailure('failed_unknown')).toBe(true)
    expect(isTaskTerminal('failed_unknown')).toBe(true)
  })

  it('keeps running and pending statuses pollable', () => {
    expect(isTaskFailure('running')).toBe(false)
    expect(isTaskTerminal('pending')).toBe(false)
    expect(isTaskTerminal('success')).toBe(true)
  })
})
