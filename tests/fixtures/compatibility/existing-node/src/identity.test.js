import assert from 'node:assert/strict'
import test from 'node:test'
import { identity } from './identity.js'

test('identity preserves the application value', () => {
  const value = { id: 'sample', score: 0 }
  assert.equal(identity(value), value)
})
