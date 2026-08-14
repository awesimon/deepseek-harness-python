import type { Context } from '@deepseek-ai/cordis'

import { BrowserModuleLoader, CordisClientAdapter, type ClientModuleLoader } from './adapter.js'
import type {
  BridgeEventFrame,
  ClientFrame,
  JsonValue,
  ReconcileCommand,
  RpcResultFrame,
  ServerFrame,
} from './protocol.js'

type EventHandler = (payload: JsonValue) => void | Promise<void>

interface PendingCall {
  resolve(value: JsonValue): void
  reject(error: Error): void
  removeAbort(): void
}

/** Structured remote RPC failure returned by the Python backend. */
export class BridgeRemoteError extends Error {
  constructor(
    readonly code: string,
    message: string,
  ) {
    super(message)
  }
}

/** Same-plugin/revision RPC and Event API handed to one client contribution. */
export class PluginChannel {
  constructor(
    private readonly connection: BridgeConnection,
    readonly pluginId: string,
    readonly revision: string,
  ) {}

  /** Invoke one explicitly registered backend method. */
  call(
    method: string,
    args: Record<string, JsonValue>,
    signal?: AbortSignal,
  ): Promise<JsonValue> {
    return this.connection.call(this.pluginId, this.revision, method, args, signal)
  }

  /** Emit one explicitly named Event to the matching backend Revision. */
  emit(name: string, payload: JsonValue): void {
    this.connection.emit(this.pluginId, this.revision, name, payload)
  }

  /** Register one Event listener and return its idempotent disposer. */
  on(name: string, handler: EventHandler): () => void {
    return this.connection.on(this.pluginId, this.revision, name, handler)
  }
}

/** Browser WebSocket owner that connects protocol frames to Cordis client Fibers. */
export class BridgeConnection {
  private readonly adapter: CordisClientAdapter
  private readonly pending = new Map<string, PendingCall>()
  private readonly handlers = new Map<string, EventHandler[]>()
  private nextCall = 1
  private started = false
  private disposed = false
  private incoming: Promise<void> = Promise.resolve()

  constructor(
    readonly pageId: string,
    private readonly socket: WebSocket,
    context: Context,
    loader: ClientModuleLoader = new BrowserModuleLoader(),
  ) {
    this.adapter = new CordisClientAdapter(
      context,
      loader,
      (frame) => this.send(frame),
      (desired) => this.channel(desired.pluginId, desired.revision),
    )
  }

  /** Attach socket listeners and send the one required hello frame. */
  start(loaded: Record<string, string> = {}): void {
    if (this.started) throw new Error('Browser Bridge connection already started')
    this.started = true
    this.socket.addEventListener('message', this.handleMessage)
    this.socket.addEventListener('close', this.handleClose)
    this.send({
      protocol: '1',
      type: 'hello',
      pageId: this.pageId,
      loaded,
    })
  }

  /** Return the API for one exact active client plugin identity. */
  channel(pluginId: string, revision: string): PluginChannel {
    return new PluginChannel(this, pluginId, revision)
  }

  /** Dispose client Fibers, listeners, and pending calls, then close the socket. */
  async dispose(): Promise<void> {
    if (this.disposed) return
    this.disposed = true
    this.socket.removeEventListener('message', this.handleMessage)
    this.socket.removeEventListener('close', this.handleClose)
    for (const call of this.pending.values()) {
      call.removeAbort()
      call.reject(new Error('Browser Bridge connection disposed'))
    }
    this.pending.clear()
    this.handlers.clear()
    await this.adapter.dispose()
    this.socket.close(1000, 'Browser Bridge connection disposed')
  }

  call(
    pluginId: string,
    revision: string,
    method: string,
    args: Record<string, JsonValue>,
    signal?: AbortSignal,
  ): Promise<JsonValue> {
    if (signal?.aborted) return Promise.reject(abortError())
    const callId = `${this.pageId}:${this.nextCall++}`
    return new Promise<JsonValue>((resolve, reject) => {
      const abort = () => {
        if (!this.pending.delete(callId)) return
        this.send({ protocol: '1', type: 'rpc-cancel', pageId: this.pageId, callId })
        reject(abortError())
      }
      signal?.addEventListener('abort', abort, { once: true })
      this.pending.set(callId, {
        resolve,
        reject,
        removeAbort: () => signal?.removeEventListener('abort', abort),
      })
      this.send({
        protocol: '1',
        type: 'rpc-call',
        pageId: this.pageId,
        callId,
        pluginId,
        revision,
        method,
        arguments: args,
      })
    })
  }

  emit(pluginId: string, revision: string, name: string, payload: JsonValue): void {
    this.send({
      protocol: '1',
      type: 'event',
      pageId: this.pageId,
      pluginId,
      revision,
      name,
      payload,
    })
  }

  on(pluginId: string, revision: string, name: string, handler: EventHandler): () => void {
    const key = eventKey(pluginId, revision, name)
    const handlers = this.handlers.get(key) ?? []
    handlers.push(handler)
    this.handlers.set(key, handlers)
    let active = true
    return () => {
      if (!active) return
      active = false
      const current = this.handlers.get(key)
      if (!current) return
      const index = current.indexOf(handler)
      if (index >= 0) current.splice(index, 1)
      if (!current.length) this.handlers.delete(key)
    }
  }

  private readonly handleMessage = (event: MessageEvent<unknown>): void => {
    this.incoming = this.incoming.then(() => this.receive(decodeServerFrame(event.data)))
    this.incoming.catch(() => this.socket.close(1008, 'invalid Browser Bridge frame'))
  }

  private readonly handleClose = (): void => {
    void this.dispose()
  }

  private async receive(frame: ServerFrame): Promise<void> {
    if (frame.type === 'reconcile') {
      await this.adapter.reconcile(frame)
      return
    }
    if (frame.type === 'rpc-result') {
      this.resolveCall(frame)
      return
    }
    const handlers = this.handlers.get(eventKey(frame.pluginId, frame.revision, frame.name)) ?? []
    for (const handler of [...handlers]) await handler(frame.payload)
  }

  private resolveCall(frame: RpcResultFrame): void {
    const pending = this.pending.get(frame.callId)
    if (!pending) return
    this.pending.delete(frame.callId)
    pending.removeAbort()
    if ('errorCode' in frame) {
      pending.reject(new BridgeRemoteError(frame.errorCode, frame.errorMessage))
    } else {
      pending.resolve(frame.result)
    }
  }

  private send(frame: ClientFrame): void {
    if (this.disposed) throw new Error('Browser Bridge connection is disposed')
    this.socket.send(JSON.stringify(frame))
  }
}

/** Validate the server frame fields consumed by the browser runtime. */
export function decodeServerFrame(value: unknown): ServerFrame {
  const frame = parseObject(value)
  if (frame.protocol !== '1') throw new Error('unsupported Browser Bridge protocol')
  if (frame.type === 'reconcile') return parseReconcile(frame)
  if (frame.type === 'rpc-result') return parseRpcResult(frame)
  if (frame.type === 'event') return parseEvent(frame)
  throw new Error(`unexpected Browser Bridge frame: ${String(frame.type)}`)
}

function parseReconcile(frame: Record<string, unknown>): ReconcileCommand {
  exactKeys(frame, ['protocol', 'type', 'operationId', 'desired'])
  if (!Array.isArray(frame.desired)) throw new TypeError('reconcile desired must be an array')
  return {
    protocol: '1',
    type: 'reconcile',
    operationId: requiredString(frame.operationId),
    desired: frame.desired.map((value) => {
      const item = parseObject(value)
      exactKeys(item, [
        'pluginId',
        'revision',
        'bundleUrl',
        'bundleSha256',
        'protocolSchemaUrl',
        'activationPolicy',
      ])
      const policy = item.activationPolicy
      if (policy !== 'required' && policy !== 'optional') {
        throw new TypeError('invalid client activation policy')
      }
      if (item.protocolSchemaUrl !== null && typeof item.protocolSchemaUrl !== 'string') {
        throw new TypeError('invalid client protocol Schema URL')
      }
      return {
        pluginId: requiredString(item.pluginId),
        revision: requiredString(item.revision),
        bundleUrl: requiredString(item.bundleUrl),
        bundleSha256: requiredString(item.bundleSha256),
        protocolSchemaUrl: item.protocolSchemaUrl,
        activationPolicy: policy,
      }
    }),
  }
}

function parseRpcResult(frame: Record<string, unknown>): RpcResultFrame {
  if ('errorCode' in frame) {
    exactKeys(frame, ['protocol', 'type', 'callId', 'errorCode', 'errorMessage'])
    return {
      protocol: '1',
      type: 'rpc-result',
      callId: requiredString(frame.callId),
      errorCode: requiredString(frame.errorCode),
      errorMessage: requiredString(frame.errorMessage),
    }
  }
  exactKeys(frame, ['protocol', 'type', 'callId', 'result'])
  return {
    protocol: '1',
    type: 'rpc-result',
    callId: requiredString(frame.callId),
    result: frame.result as JsonValue,
  }
}

function parseEvent(frame: Record<string, unknown>): BridgeEventFrame {
  exactKeys(frame, ['protocol', 'type', 'pageId', 'pluginId', 'revision', 'name', 'payload'])
  return {
    protocol: '1',
    type: 'event',
    pageId: requiredString(frame.pageId),
    pluginId: requiredString(frame.pluginId),
    revision: requiredString(frame.revision),
    name: requiredString(frame.name),
    payload: frame.payload as JsonValue,
  }
}

function parseObject(value: unknown): Record<string, unknown> {
  const decoded: unknown = typeof value === 'string' ? JSON.parse(value) : value
  if (typeof decoded !== 'object' || decoded === null || Array.isArray(decoded)) {
    throw new TypeError('Browser Bridge frame must be an object')
  }
  return decoded as Record<string, unknown>
}

function exactKeys(value: Record<string, unknown>, expected: string[]): void {
  const actual = Object.keys(value).sort()
  const allowed = [...expected].sort()
  if (actual.length !== allowed.length || actual.some((key, index) => key !== allowed[index])) {
    throw new TypeError('Browser Bridge frame contains missing or unknown fields')
  }
}

function requiredString(value: unknown): string {
  if (typeof value !== 'string' || !value) throw new TypeError('expected a non-empty string')
  return value
}

function eventKey(pluginId: string, revision: string, name: string): string {
  return JSON.stringify([pluginId, revision, name])
}

function abortError(): Error {
  return new DOMException('RPC call aborted', 'AbortError')
}
