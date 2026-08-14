import type { Context, Fiber, Plugin } from '@deepseek-ai/cordis'

import type {
  DesiredClient,
  JsonValue,
  PluginLoadResult,
  ReconcileCommand,
  ReconcileComplete,
  ReconcileResultFrame,
} from './protocol.js'

/** Revision-bound Bridge API supplied to a client plugin factory. */
export interface ClientPluginApi {
  readonly pluginId: string
  readonly revision: string
  call(
    method: string,
    args: Record<string, JsonValue>,
    signal?: AbortSignal,
  ): Promise<JsonValue>
  emit(name: string, payload: JsonValue): void
  on(name: string, handler: (payload: JsonValue) => void | Promise<void>): () => void
}

type ClientApiFactory = (desired: DesiredClient) => ClientPluginApi

/** Loaded JavaScript module plus ownership cleanup for its import URL. */
export interface LoadedClientModule {
  exports: unknown
  release(): void
}

/** Strategy for fetching, verifying, and importing one desired client module. */
export interface ClientModuleLoader {
  load(desired: DesiredClient): Promise<LoadedClientModule>
}

/** Browser implementation using fetch, Web Crypto, Blob URLs, and dynamic import. */
export class BrowserModuleLoader implements ClientModuleLoader {
  async load(desired: DesiredClient): Promise<LoadedClientModule> {
    const response = await fetch(desired.bundleUrl)
    if (!response.ok) {
      throw new Error(`client bundle fetch failed: ${response.status}`)
    }
    const bytes = new Uint8Array(await response.arrayBuffer())
    await verifyBundle(bytes, desired.bundleSha256)
    const url = URL.createObjectURL(new Blob([bytes], { type: 'text/javascript' }))
    try {
      const exports: unknown = await import(/* @vite-ignore */ url)
      return {
        exports,
        release: () => URL.revokeObjectURL(url),
      }
    } catch (error) {
      URL.revokeObjectURL(url)
      throw error
    }
  }
}

interface ActiveClient {
  desired: DesiredClient
  fiber: Fiber
  release(): void
}

type ResultSink = (frame: ReconcileResultFrame) => void | Promise<void>

/** Reconciles content-addressed modules into child Cordis Fibers. */
export class CordisClientAdapter {
  private readonly active = new Map<string, ActiveClient>()
  private generation = 0
  private queue: Promise<void> = Promise.resolve()

  constructor(
    private readonly context: Context,
    private readonly loader: ClientModuleLoader,
    private readonly report: ResultSink,
    private readonly createApi?: ClientApiFactory,
  ) {}

  /** Serialize one full-graph operation while immediately superseding older work. */
  reconcile(command: ReconcileCommand): Promise<void> {
    const generation = ++this.generation
    const operation = this.queue.then(() => this.apply(command, generation))
    this.queue = operation.catch(() => undefined)
    return operation
  }

  /** Return the exact active Revision for each Plugin ID. */
  snapshot(): ReadonlyMap<string, string> {
    return new Map(
      [...this.active].map(([pluginId, client]) => [pluginId, client.desired.revision]),
    )
  }

  /** Dispose every active client Fiber and imported module. */
  async dispose(): Promise<void> {
    ++this.generation
    await this.queue
    await Promise.all([...this.active].map(([pluginId]) => this.unload(pluginId)))
  }

  private async apply(command: ReconcileCommand, generation: number): Promise<void> {
    const desired = new Map(command.desired.map((item) => [item.pluginId, item]))
    const failures: string[] = []

    for (const [pluginId, client] of [...this.active]) {
      const target = desired.get(pluginId)
      if (target?.revision === client.desired.revision) continue
      await this.sendResult(command, client.desired, 'unloading', null)
      await this.unload(pluginId)
      await this.sendResult(command, client.desired, 'absent', null)
      if (generation !== this.generation) return
    }

    for (const item of command.desired) {
      if (generation !== this.generation) return
      if (this.active.get(item.pluginId)?.desired.revision === item.revision) continue
      await this.sendResult(command, item, 'loading', null)
      try {
        await this.load(item, generation)
        if (generation !== this.generation) return
        await this.sendResult(command, item, 'active', null)
      } catch (error) {
        const message = errorMessage(error)
        failures.push(`${item.pluginId}: ${message}`)
        await this.sendResult(command, item, 'failed', message)
      }
    }

    if (generation !== this.generation) return
    const complete: ReconcileComplete = failures.length
      ? {
          protocol: '1',
          type: 'reconcile-complete',
          operationId: command.operationId,
          success: false,
          error: failures.join('; '),
        }
      : {
          protocol: '1',
          type: 'reconcile-complete',
          operationId: command.operationId,
          success: true,
          error: null,
        }
    await this.report(complete)
  }

  private async load(desired: DesiredClient, generation: number): Promise<void> {
    const loaded = await this.loader.load(desired)
    if (generation !== this.generation) {
      loaded.release()
      return
    }
    let fiber: Fiber | undefined
    try {
      const plugin = await resolveClientPlugin(
        loaded.exports,
        this.createApi?.(desired),
      )
      fiber = this.context.plugin(plugin)
      await fiber
      if (generation !== this.generation) {
        await fiber.dispose()
        loaded.release()
        return
      }
      this.active.set(desired.pluginId, {
        desired,
        fiber,
        release: loaded.release,
      })
    } catch (error) {
      if (fiber) await fiber.dispose()
      loaded.release()
      throw error
    }
  }

  private async unload(pluginId: string): Promise<void> {
    const client = this.active.get(pluginId)
    if (!client) return
    this.active.delete(pluginId)
    try {
      await client.fiber.dispose()
    } finally {
      client.release()
    }
  }

  private async sendResult(
    command: ReconcileCommand,
    desired: DesiredClient,
    state: PluginLoadResult['state'],
    error: string | null,
  ): Promise<void> {
    await this.report({
      protocol: '1',
      type: 'plugin-result',
      operationId: command.operationId,
      pluginId: desired.pluginId,
      revision: desired.revision,
      state,
      error,
    })
  }
}

/** Reject bytes whose SHA-256 does not match the desired content Revision. */
export async function verifyBundle(bytes: Uint8Array, expected: string): Promise<void> {
  const copy = new Uint8Array(bytes.byteLength)
  copy.set(bytes)
  const digest = await crypto.subtle.digest('SHA-256', copy.buffer)
  const actual = [...new Uint8Array(digest)]
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('')
  if (actual !== expected) {
    throw new Error(`client bundle SHA-256 mismatch: expected ${expected}, received ${actual}`)
  }
}

async function resolveClientPlugin(
  exports: unknown,
  api: ClientPluginApi | undefined,
): Promise<Plugin<void>> {
  if (!isRecord(exports)) throw new TypeError('client module must export an object')
  const factory = exports.createPlugin
  const candidate = typeof factory === 'function'
    ? await factory(api)
    : (exports.plugin ?? exports.default)
  if (typeof candidate === 'function') return candidate as Plugin<void>
  if (isRecord(candidate) && typeof candidate.apply === 'function') {
    return candidate as unknown as Plugin<void>
  }
  throw new TypeError('client module must export plugin/default or createPlugin()')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
