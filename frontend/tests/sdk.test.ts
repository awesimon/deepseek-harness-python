import { Context } from '@deepseek-ai/cordis'
import { describe, expect, it } from 'vitest'

import {
  clientEvent,
  defineClientPlugin,
  rpcMethod,
  serverEvent,
  type ClientPluginApi,
  type JsonValue,
} from '../src/index.js'
import { createClientPluginHarness } from '../src/testing.js'

const describeRpc = rpcMethod<{ verbose: boolean }, { title: string }>('describe')
const changedByClient = clientEvent<{ value: number }>('changed-by-client')
const changedByServer = serverEvent<{ value: number }>('changed-by-server')

describe('client plugin SDK descriptors', () => {
  it('creates immutable identity-free descriptors', () => {
    expect(describeRpc).toEqual({ name: 'describe', direction: 'rpc' })
    expect(changedByClient).toEqual({ name: 'changed-by-client', direction: 'client' })
    expect(changedByServer).toEqual({ name: 'changed-by-server', direction: 'server' })
    expect(Object.isFrozen(describeRpc)).toBe(true)
    expect(Object.keys(describeRpc)).toEqual(['name', 'direction'])
  })

  it('rejects empty names and undeclared production arguments', () => {
    expect(() => rpcMethod('   ')).toThrow('non-empty string')
    expect(() => Reflect.apply(clientEvent, undefined, ['event', 'plugin-id'])).toThrow(
      'exactly one argument',
    )
    expect(() => Reflect.apply(defineClientPlugin, undefined, [() => undefined, 'revision'])).toThrow(
      'exactly one argument',
    )
    const createPlugin = defineClientPlugin(() => undefined)
    expect(() => Reflect.apply(createPlugin, undefined, [undefined, 'revision'])).toThrow(
      'exactly one argument',
    )
  })
})

describe('client plugin SDK lifecycle', () => {
  it('uses only the reconciliation identity for RPC and bidirectional Events', async () => {
    const observations: string[] = []
    const createPlugin = defineClientPlugin(async (ctx) => {
      expect(Object.isFrozen(ctx)).toBe(true)
      observations.push(`${ctx.pluginId}@${ctx.revision}`)
      const result = await ctx.call(describeRpc, { verbose: true })
      observations.push(result.title)
      ctx.emit(changedByClient, { value: 1 })
      ctx.on(changedByServer, ({ value }) => {
        observations.push(`server:${value}`)
      })
    })
    const harness = await createClientPluginHarness(createPlugin, {
      pluginId: 'com.example.full-stack',
      revision: 'sha256:revision-1',
      call: (method, args) => ({ title: `${method}:${String(args.verbose)}` }),
    })

    await harness.dispatch(changedByServer, { value: 2 })

    expect(observations).toEqual([
      'com.example.full-stack@sha256:revision-1',
      'describe:true',
      'server:2',
    ])
    expect(harness.calls).toEqual([{
      method: 'describe',
      arguments: { verbose: true },
    }])
    expect(harness.emitted).toEqual([{
      name: 'changed-by-client',
      payload: { value: 1 },
    }])
    expect(harness.activeListenerCount).toBe(1)
    await harness.dispose()
    expect(harness.activeListenerCount).toBe(0)
    await expect(harness.dispatch(changedByServer, { value: 3 })).rejects.toThrow('disposed')
  })

  it('owns listeners, custom Effects, and setup cleanup in the Fiber', async () => {
    const cleanup: string[] = []
    const createPlugin = defineClientPlugin((ctx) => {
      ctx.on(changedByServer, () => undefined)
      ctx.effect(() => () => {
        cleanup.push('effect')
      })
      return () => {
        cleanup.push('setup')
      }
    })
    const harness = await createClientPluginHarness(createPlugin)

    expect(harness.activeListenerCount).toBe(1)
    const first = harness.dispose()
    const second = harness.dispose()
    expect(first).toBe(second)
    await first

    expect(cleanup.sort()).toEqual(['effect', 'setup'])
    expect(harness.activeListenerCount).toBe(0)
  })

  it('rolls back attempt-owned Effects when setup fails', async () => {
    const cleanup: string[] = []
    const createPlugin = defineClientPlugin((ctx) => {
      ctx.effect(() => () => {
        cleanup.push('rolled-back')
      })
      throw new Error('setup failed')
    })

    await expect(createClientPluginHarness(createPlugin)).rejects.toThrow('setup failed')
    expect(cleanup).toEqual(['rolled-back'])
  })

  it('reports cleanup failure after attempting every owned cleanup', async () => {
    const cleanup: string[] = []
    const createPlugin = defineClientPlugin((ctx) => {
      ctx.effect(() => () => {
        cleanup.push('first')
      })
      ctx.effect(() => () => {
        cleanup.push('failing')
        throw new Error('cleanup failed')
      })
      ctx.effect(() => () => {
        cleanup.push('last')
      })
    })
    const harness = await createClientPluginHarness(createPlugin)

    await expect(harness.dispose()).rejects.toMatchObject({
      message: 'client plugin cleanup failed',
      errors: [expect.objectContaining({ message: 'cleanup failed' })],
    })
    expect(cleanup).toEqual(['last', 'failing', 'first'])
    expect(harness.activeListenerCount).toBe(0)
    await expect(harness.dispose()).rejects.toThrow('client plugin cleanup failed')
  })

  it('fails Fiber activation without a revision-bound API', async () => {
    const cordis = new Context()
    const createPlugin = defineClientPlugin(() => undefined)
    const fiber = cordis.plugin(createPlugin(undefined))

    await expect(fiber).rejects.toThrow('revision-bound API')
    await fiber.dispose()
  })

  it('passes RPC cancellation through without converting it to success', async () => {
    const wait = rpcMethod<Record<string, never>, null>('wait')
    const controller = new AbortController()
    let operation: Promise<null> | undefined
    const createPlugin = defineClientPlugin((ctx) => {
      operation = ctx.call(wait, {}, controller.signal)
    })
    const harness = await createClientPluginHarness(createPlugin, {
      call: () => new Promise<JsonValue>(() => undefined),
    })

    controller.abort()
    await expect(operation).rejects.toMatchObject({ name: 'AbortError' })
    expect(harness.calls[0]?.signal).toBe(controller.signal)
    await harness.dispose()
  })

  it('reports listener failures through dispatch', async () => {
    const createPlugin = defineClientPlugin((ctx) => {
      ctx.on(changedByServer, () => {
        throw new Error('listener failed')
      })
    })
    const harness = await createClientPluginHarness(createPlugin)

    await expect(harness.dispatch(changedByServer, { value: 1 })).rejects.toThrow('listener failed')
    await harness.dispose()
  })
})

describe('client plugin SDK JSON validation', () => {
  it('rejects unsupported outbound RPC and Event values before transport', async () => {
    let context: Parameters<Parameters<typeof defineClientPlugin>[0]>[0] | undefined
    const harness = await createClientPluginHarness(defineClientPlugin((ctx) => {
      context = ctx
    }))

    await expect(context?.call(
      describeRpc,
      { verbose: Number.NaN } as unknown as { verbose: boolean },
    )).rejects.toThrow('JSON-compatible')
    expect(() => context?.emit(
      changedByClient,
      { value: undefined } as unknown as { value: number },
    )).toThrow('JSON-compatible')
    expect(harness.calls).toHaveLength(0)
    expect(harness.emitted).toHaveLength(0)
    await harness.dispose()
  })

  it('rejects unsupported RPC results and incoming Event payloads', async () => {
    const createPlugin = defineClientPlugin(async (ctx) => {
      ctx.on(changedByServer, () => undefined)
      await ctx.call(describeRpc, { verbose: false })
    })
    await expect(createClientPluginHarness(createPlugin, {
      call: () => new Date() as unknown as JsonValue,
    })).rejects.toThrow('JSON-compatible')

    const harness = await createClientPluginHarness(defineClientPlugin((ctx) => {
      ctx.on(changedByServer, () => undefined)
    }))
    await expect(harness.dispatch(
      changedByServer,
      { value: Number.POSITIVE_INFINITY },
    )).rejects.toThrow('JSON-compatible')
    await harness.dispose()
  })
})

describe('raw client entrypoints', () => {
  it('remain accepted by the existing adapter API', () => {
    const raw: ClientPluginApi = {
      pluginId: 'com.example.raw',
      revision: 'raw-revision',
      call: async () => null,
      emit: () => undefined,
      on: () => () => undefined,
    }
    expect(raw.pluginId).toBe('com.example.raw')
  })
})
