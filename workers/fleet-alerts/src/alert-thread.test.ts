import { describe, expect, it } from 'vitest'
import { threadHeaders } from './alert-thread'

const ALERT_STATE_HEADER = 'X-SMD-Alert-State'

const ID = '<smd-alert.00000000-0000-4000-8000-000000000000@smd.services>'

describe('threadHeaders', () => {
  it('an alert names itself and carries no state header', () => {
    expect(threadHeaders('opened', ID)).toEqual({ 'Message-ID': ID })
    expect(threadHeaders('opened', null)).toBeUndefined()
  })

  it('a reminder threads under its alert and is never stamped resolved', () => {
    const headers = threadHeaders('reminder', ID)
    expect(headers).toEqual({ 'In-Reply-To': ID, References: ID })
    expect(headers?.[ALERT_STATE_HEADER]).toBeUndefined()
    expect(threadHeaders('reminder', null)).toBeUndefined()
  })

  it('a recovery threads under its alert and is stamped resolved', () => {
    expect(threadHeaders('resolved', ID)).toEqual({
      'In-Reply-To': ID,
      References: ID,
      [ALERT_STATE_HEADER]: 'resolved',
    })
    expect(threadHeaders('resolved', null)).toEqual({ [ALERT_STATE_HEADER]: 'resolved' })
  })
})
