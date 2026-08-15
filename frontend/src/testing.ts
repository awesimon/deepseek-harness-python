import { Context, type Fiber, type Message } from '@deepseek-ai/cordis'

import type { ClientPluginApi } from './adapter.js'
import { assertJsonValue } from './json.js'
import type { JsonValue } from './protocol.js'
import {
  type ClientPluginFactory,
  type RpcArguments,
  type ServerEvent,
} from './sdk.js'

/** One RPC invocation captured by the client plugin test harness. */
export interface ClientPluginHarnessCall {
  readonly method: string
  readonly arguments: Readonly<RpcArguments>
  readonly signal?: AbortSignal
}

/** One client-origin Event captured by the client plugin test harness. */
export interface ClientPluginHarnessEvent {
  readonly name: string
  readonly payload: JsonValue
}

/** Options for a synthetic reconciliation-owned client identity. */
export interface ClientPluginHarnessOptions {
  readonly pluginId?: string
  readonly revision?: string
  readonly call?: (
    method: string,
    args: Readonly<RpcArguments>,
    signal?: AbortSignal,
  ) => JsonValue | Promise<JsonValue>
}

/** Mounted real Cordis client plugin with deterministic traffic and disposal. */
export interface ClientPluginHarness {
  readonly cordis: Context
  readonly pluginId: string
  readonly revision: string
  readonly calls: readonly ClientPluginHarnessCall[]
  readonly emitted: readonly ClientPluginHarnessEvent[]
  readonly activeListenerCount: number

  /** Deliver one backend-origin Event through the revision-bound fake API. */
  dispatch<Payload extends JsonValue>(
    event: ServerEvent<Payload>,
    payload: Payload,
  ): Promise<void>

  /** Dispose the mounted Fiber; repeated calls share the first result. */
  dispose(): Promise<void>
}

/** Mount a public client factory with a test-only fixture identity. */
export async function createClientPluginHarness(
  createPlugin: ClientPluginFactory,
  options: ClientPluginHarnessOptions = {},
): Promise<ClientPluginHarness> {
  const state = new HarnessState(options)
  const cordis = new Context()
  let fiber: Fiber | undefined
  try {
    fiber = cordis.plugin(createPlugin(state.api))
    await fiber
    state.mount(cordis, fiber)
    return state
  } catch (error) {
    try {
      await fiber?.dispose()
    } finally {
      state.deactivate()
    }
    throw error
  }
}

type EventHandler = (payload: JsonValue) => void | Promise<void>

class HarnessState implements ClientPluginHarness {
  readonly pluginId: string
  readonly revision: string
  readonly calls: ClientPluginHarnessCall[] = []
  readonly emitted: ClientPluginHarnessEvent[] = []
  readonly api: ClientPluginApi
  cordis!: Context
  private fiber!: Fiber
  private active = true
  private disposal?: Promise<void>
  private readonly listeners = new Map<string, EventHandler[]>()
  private readonly callHandler: NonNullable<ClientPluginHarnessOptions['call']>

  constructor(options: ClientPluginHarnessOptions) {
    this.pluginId = options.pluginId ?? 'com.example.test-plugin'
    this.revision = options.revision ?? 'test-revision'
    this.callHandler = options.call ?? (() => null)
    this.api = {
      pluginId: this.pluginId,
      revision: this.revision,
      call: (method, args, signal) => this.call(method, args, signal),
      emit: (name, payload) => this.emit(name, payload),
      on: (name, handler) => this.on(name, handler),
    }
  }

  get activeListenerCount(): number {
    let count = 0
    for (const handlers of this.listeners.values()) count += handlers.length
    return count
  }

  mount(cordis: Context, fiber: Fiber): void {
    this.cordis = cordis
    this.fiber = fiber
  }

  deactivate(): void {
    this.active = false
    this.listeners.clear()
  }

  async dispatch<Payload extends JsonValue>(
    event: ServerEvent<Payload>,
    payload: Payload,
  ): Promise<void> {
    this.assertActive()
    if (event.direction !== 'server') throw new TypeError('expected a server protocol descriptor')
    assertJsonValue(payload, `Event ${event.name} payload`)
    for (const handler of [...(this.listeners.get(event.name) ?? [])]) await handler(payload)
  }

  dispose(): Promise<void> {
    this.disposal ??= this.disposeOnce()
    return this.disposal
  }

  private async disposeOnce(): Promise<void> {
    const firstDisposalDiagnostic = this.cordis.logger.buffer.length
    try {
      await this.fiber.dispose()
    } finally {
      this.deactivate()
    }
    const errors = this.cordis.logger.buffer
      .slice(firstDisposalDiagnostic)
      .filter((message) => message.type === 'error')
      .map((message) => diagnosticError(message))
    if (errors.length) {
      throw new AggregateError(errors, 'client plugin cleanup failed')
    }
  }

  private async call(
    method: string,
    args: RpcArguments,
    signal?: AbortSignal,
  ): Promise<JsonValue> {
    this.assertActive()
    if (signal?.aborted) throw abortError()
    const frozenArguments = freezeJson(args) as Readonly<RpcArguments>
    const record: ClientPluginHarnessCall = signal
      ? Object.freeze({ method, arguments: frozenArguments, signal })
      : Object.freeze({ method, arguments: frozenArguments })
    this.calls.push(record)
    const result = Promise.resolve(this.callHandler(method, frozenArguments, signal))
    if (!signal) return result
    return new Promise<JsonValue>((resolve, reject) => {
      const abort = () => {
        signal.removeEventListener('abort', abort)
        reject(abortError())
      }
      signal.addEventListener('abort', abort, { once: true })
      if (signal.aborted) abort()
      result.then(
        (value) => {
          signal.removeEventListener('abort', abort)
          resolve(value)
        },
        (error: unknown) => {
          signal.removeEventListener('abort', abort)
          reject(error)
        },
      )
    })
  }

  private emit(name: string, payload: JsonValue): void {
    this.assertActive()
    this.emitted.push(Object.freeze({ name, payload: freezeJson(payload) }))
  }

  private on(name: string, handler: EventHandler): () => void {
    this.assertActive()
    const handlers = this.listeners.get(name) ?? []
    handlers.push(handler)
    this.listeners.set(name, handlers)
    let active = true
    return () => {
      if (!active) return
      active = false
      const current = this.listeners.get(name)
      if (!current) return
      const index = current.indexOf(handler)
      if (index >= 0) current.splice(index, 1)
      if (!current.length) this.listeners.delete(name)
    }
  }

  private assertActive(): void {
    if (!this.active) throw new Error('client plugin harness is disposed')
  }
}

function freezeJson(value: JsonValue): JsonValue {
  if (Array.isArray(value)) {
    return Object.freeze(value.map(freezeJson)) as unknown as JsonValue
  }
  if (value && typeof value === 'object') {
    return Object.freeze(Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, freezeJson(item)]),
    ))
  }
  return value
}

function diagnosticError(message: Message): Error {
  const value: unknown = message.args[0]
  return value instanceof Error ? value : new Error(String(value))
}

function abortError(): DOMException {
  return new DOMException('The operation was aborted', 'AbortError')
}
