import type { Context, Effect, Plugin } from '@deepseek-ai/cordis'

import type { ClientPluginApi } from './adapter.js'
import { assertJsonObject, assertJsonValue } from './json.js'
import type { JsonValue } from './protocol.js'

const rpcTypes: unique symbol = Symbol('rpc-types')
const clientEventType: unique symbol = Symbol('client-event-type')
const serverEventType: unique symbol = Symbol('server-event-type')

/** JSON object accepted as Browser Bridge RPC arguments. */
export type RpcArguments = Record<string, JsonValue>

/** Immutable descriptor for one client-to-backend RPC method. */
export interface RpcMethod<Arguments extends RpcArguments, Result extends JsonValue> {
  readonly name: string
  readonly direction: 'rpc'
  readonly [rpcTypes]: {
    readonly arguments: (value: Arguments) => Arguments
    readonly result: Result
  }
}

/** Immutable descriptor for one client-to-backend Event. */
export interface ClientEvent<Payload extends JsonValue> {
  readonly name: string
  readonly direction: 'client'
  readonly [clientEventType]: (value: Payload) => Payload
}

/** Immutable descriptor for one backend-to-client Event. */
export interface ServerEvent<Payload extends JsonValue> {
  readonly name: string
  readonly direction: 'server'
  readonly [serverEventType]: (value: Payload) => Payload
}

/** Create an identity-free RPC method descriptor. */
export function rpcMethod<Arguments extends RpcArguments, Result extends JsonValue>(
  name: string,
): RpcMethod<Arguments, Result> {
  requireOneArgument(arguments.length, 'rpcMethod')
  return descriptor(name, 'rpc') as RpcMethod<Arguments, Result>
}

/** Create an identity-free client-to-backend Event descriptor. */
export function clientEvent<Payload extends JsonValue>(name: string): ClientEvent<Payload> {
  requireOneArgument(arguments.length, 'clientEvent')
  return descriptor(name, 'client') as ClientEvent<Payload>
}

/** Create an identity-free backend-to-client Event descriptor. */
export function serverEvent<Payload extends JsonValue>(name: string): ServerEvent<Payload> {
  requireOneArgument(arguments.length, 'serverEvent')
  return descriptor(name, 'server') as ServerEvent<Payload>
}

/** Revision-bound author API available while one client Fiber is active. */
export interface ClientPluginContext {
  readonly cordis: Context
  readonly pluginId: string
  readonly revision: string

  /** Call the matching backend Revision and validate both wire values. */
  call<Arguments extends RpcArguments, Result extends JsonValue>(
    method: RpcMethod<Arguments, Result>,
    args: Arguments,
    signal?: AbortSignal,
  ): Promise<Result>

  /** Send a client-origin Event to the matching backend Revision. */
  emit<Payload extends JsonValue>(event: ClientEvent<Payload>, payload: Payload): void

  /** Register a backend Event listener owned by this client Fiber. */
  on<Payload extends JsonValue>(
    event: ServerEvent<Payload>,
    handler: (payload: Payload) => void | Promise<void>,
  ): void

  /** Register a custom Effect owned by this client Fiber. */
  effect(setup: () => Effect): void
}

/** Setup callback accepted by {@link defineClientPlugin}. */
export type ClientPluginSetup = (
  context: ClientPluginContext,
) => void | Effect | Promise<void | (() => void | Promise<void>)>

/** Revision-bound factory consumed by the Browser Bridge adapter. */
export type ClientPluginFactory = (api?: ClientPluginApi) => Plugin<void>

/** Define a client plugin without accepting runtime identity from author code. */
export function defineClientPlugin(setup: ClientPluginSetup): ClientPluginFactory {
  requireOneArgument(arguments.length, 'defineClientPlugin')
  if (typeof setup !== 'function') throw new TypeError('client plugin setup must be a function')
  return function createPlugin(api?: ClientPluginApi): Plugin<void> {
    requireOneArgument(arguments.length, 'client plugin factory')
    return (cordis: Context) => {
      assertRevisionBoundApi(api)
      return setup(new BoundClientPluginContext(cordis, api))
    }
  }
}

class BoundClientPluginContext implements ClientPluginContext {
  readonly pluginId: string
  readonly revision: string

  constructor(
    readonly cordis: Context,
    private readonly api: ClientPluginApi,
  ) {
    this.pluginId = api.pluginId
    this.revision = api.revision
    Object.freeze(this)
  }

  async call<Arguments extends RpcArguments, Result extends JsonValue>(
    method: RpcMethod<Arguments, Result>,
    args: Arguments,
    signal?: AbortSignal,
  ): Promise<Result> {
    assertDescriptor(method, 'rpc')
    assertJsonObject(args, `RPC ${method.name} arguments`)
    const result = await this.api.call(method.name, args, signal)
    assertJsonValue(result, `RPC ${method.name} result`)
    return result as Result
  }

  emit<Payload extends JsonValue>(event: ClientEvent<Payload>, payload: Payload): void {
    assertDescriptor(event, 'client')
    assertJsonValue(payload, `Event ${event.name} payload`)
    this.api.emit(event.name, payload)
  }

  on<Payload extends JsonValue>(
    event: ServerEvent<Payload>,
    handler: (payload: Payload) => void | Promise<void>,
  ): void {
    assertDescriptor(event, 'server')
    if (typeof handler !== 'function') throw new TypeError('Event handler must be a function')
    this.cordis.effect(
      () => this.api.on(event.name, async (payload) => {
        assertJsonValue(payload, `Event ${event.name} payload`)
        await handler(payload as Payload)
      }),
      `client event ${event.name}`,
    )
  }

  effect(setup: () => Effect): void {
    if (typeof setup !== 'function') throw new TypeError('Effect setup must be a function')
    this.cordis.effect(setup, 'client plugin SDK effect')
  }
}

function descriptor(
  name: string,
  direction: 'rpc' | 'client' | 'server',
): Readonly<{ name: string, direction: 'rpc' | 'client' | 'server' }> {
  if (typeof name !== 'string' || !name.trim()) {
    throw new TypeError('protocol descriptor name must be a non-empty string')
  }
  return Object.freeze({ name, direction })
}

function assertRevisionBoundApi(
  api: ClientPluginApi | undefined,
): asserts api is ClientPluginApi {
  if (!api || typeof api.pluginId !== 'string' || !api.pluginId.trim()) {
    throw new TypeError('client plugin activation requires a revision-bound API')
  }
  if (typeof api.revision !== 'string' || !api.revision.trim()) {
    throw new TypeError('client plugin activation requires a revision-bound API')
  }
  if (typeof api.call !== 'function' || typeof api.emit !== 'function' || typeof api.on !== 'function') {
    throw new TypeError('client plugin activation requires a revision-bound API')
  }
}

function assertDescriptor(
  value: { readonly name: string, readonly direction: string },
  direction: 'rpc' | 'client' | 'server',
): void {
  if (value.direction !== direction || typeof value.name !== 'string' || !value.name.trim()) {
    throw new TypeError(`expected a ${direction} protocol descriptor`)
  }
}

function requireOneArgument(count: number, name: string): void {
  if (count !== 1) throw new TypeError(`${name} accepts exactly one argument`)
}
