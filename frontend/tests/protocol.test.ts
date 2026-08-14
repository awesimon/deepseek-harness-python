import { describe, expect, it } from 'vitest'

import fixtures from '../../harness/protocol/bridge-v1.fixtures.json'
import { decodeServerFrame } from '../src/index.js'

describe('Browser Bridge protocol fixtures', () => {
  it('matches Python outcomes for shared server frames', () => {
    for (const frame of fixtures.serverValid) {
      expect(() => decodeServerFrame(frame)).not.toThrow()
    }
    for (const frame of fixtures.serverInvalid) {
      expect(() => decodeServerFrame(frame)).toThrow()
    }
  })
})
