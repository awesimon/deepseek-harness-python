import type { JsonValue } from './protocol.js'

/** Reject values that JSON serialization would coerce, omit, or recurse forever. */
export function assertJsonValue(value: unknown, label = 'value'): asserts value is JsonValue {
  validateJsonValue(value, label, new Set<object>())
}

/** Require a JSON object rather than another JSON value. */
export function assertJsonObject(
  value: unknown,
  label: string,
): asserts value is Record<string, JsonValue> {
  if (!isPlainObject(value)) throw new TypeError(`${label} must be a JSON object`)
  assertJsonValue(value, label)
}

function validateJsonValue(value: unknown, label: string, ancestors: Set<object>): void {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return
  if (typeof value === 'number') {
    if (Number.isFinite(value)) return
    throw new TypeError(`${label} must be JSON-compatible`)
  }
  if (typeof value !== 'object') throw new TypeError(`${label} must be JSON-compatible`)
  if (ancestors.has(value)) throw new TypeError(`${label} must be JSON-compatible`)
  ancestors.add(value)
  try {
    if (Array.isArray(value)) {
      for (let index = 0; index < value.length; ++index) {
        if (!Object.hasOwn(value, index)) throw new TypeError(`${label} must be JSON-compatible`)
        validateJsonValue(value[index], `${label}[${index}]`, ancestors)
      }
      return
    }
    if (!isPlainObject(value)) throw new TypeError(`${label} must be JSON-compatible`)
    for (const key of Reflect.ownKeys(value)) {
      if (typeof key !== 'string') throw new TypeError(`${label} must be JSON-compatible`)
      const property = Object.getOwnPropertyDescriptor(value, key)
      if (!property?.enumerable || !('value' in property)) {
        throw new TypeError(`${label} must be JSON-compatible`)
      }
      validateJsonValue(property.value, `${label}.${key}`, ancestors)
    }
  } finally {
    ancestors.delete(value)
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const prototype: unknown = Object.getPrototypeOf(value)
  return prototype === Object.prototype || prototype === null
}
