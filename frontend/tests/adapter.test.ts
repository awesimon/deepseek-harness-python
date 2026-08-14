import { Context, type Plugin } from '@deepseek-ai/cordis'
import { describe, expect, it } from 'vitest'

import {
  CordisClientAdapter,
  type ClientPluginApi,
  type ClientModuleLoader,
  type DesiredClient,
  type LoadedClientModule,
  type ReconcileCommand,
  type ReconcileResultFrame,
  verifyBundle,
} from '../src/index.js'

class TestLoader implements ClientModuleLoader {
  readonly modules = new Map<string, unknown>()
  readonly released: string[] = []

  async load(desired: DesiredClient): Promise<LoadedClientModule> {
    const exports = this.modules.get(desired.revision)
    if (!exports) throw new Error(`missing test module ${desired.revision}`)
    return {
      exports,
      release: () => this.released.push(desired.revision),
    }
  }
}

function desired(revision: string): DesiredClient {
  return {
    pluginId: 'com.example.client',
    revision,
    bundleUrl: `/plugins/com.example.client/${revision}/client.js`,
    bundleSha256: '0'.repeat(64),
    protocolSchemaUrl: null,
    activationPolicy: 'required',
  }
}

function command(operationId: string, revisions: string[]): ReconcileCommand {
  return {
    protocol: '1',
    type: 'reconcile',
    operationId,
    desired: revisions.map(desired),
  }
}

describe('CordisClientAdapter', () => {
  it('preserves matching revisions and disposes before replacement and removal', async () => {
    const context = new Context()
    const loader = new TestLoader()
    const reports: ReconcileResultFrame[] = []
    const lifecycle: string[] = []
    const plugin = (revision: string): Plugin<void> => () => {
      lifecycle.push(`start:${revision}`)
      return () => lifecycle.push(`stop:${revision}`)
    }
    loader.modules.set('rev-1', { default: plugin('rev-1') })
    loader.modules.set('rev-2', { createPlugin: () => plugin('rev-2') })
    const adapter = new CordisClientAdapter(context, loader, (frame) => {
      reports.push(frame)
    })

    await adapter.reconcile(command('operation-1', ['rev-1']))
    await adapter.reconcile(command('operation-2', ['rev-1']))
    expect(lifecycle).toEqual(['start:rev-1'])
    expect(adapter.snapshot().get('com.example.client')).toBe('rev-1')

    await adapter.reconcile(command('operation-3', ['rev-2']))
    expect(lifecycle).toEqual(['start:rev-1', 'stop:rev-1', 'start:rev-2'])
    expect(loader.released).toEqual(['rev-1'])

    await adapter.reconcile(command('operation-4', []))
    expect(lifecycle).toEqual(['start:rev-1', 'stop:rev-1', 'start:rev-2', 'stop:rev-2'])
    expect(loader.released).toEqual(['rev-1', 'rev-2'])
    expect(adapter.snapshot().size).toBe(0)
    expect(reports.at(-1)).toMatchObject({ type: 'reconcile-complete', success: true })
  })

  it('reports activation failure and releases attempt-owned modules', async () => {
    const context = new Context()
    const loader = new TestLoader()
    const reports: ReconcileResultFrame[] = []
    loader.modules.set('broken', { default: 'not-a-plugin' })
    const adapter = new CordisClientAdapter(context, loader, (frame) => {
      reports.push(frame)
    })

    await adapter.reconcile(command('operation-1', ['broken']))

    expect(loader.released).toEqual(['broken'])
    expect(adapter.snapshot().size).toBe(0)
    expect(reports).toContainEqual(expect.objectContaining({ state: 'failed' }))
    expect(reports.at(-1)).toMatchObject({ success: false })
  })

  it('binds a client factory to its exact Plugin ID and Revision', async () => {
    const context = new Context()
    const loader = new TestLoader()
    let received: ClientPluginApi | undefined
    const api = {
      pluginId: 'com.example.client',
      revision: 'rev-1',
      call: async () => null,
      emit: () => undefined,
      on: () => () => undefined,
    } satisfies ClientPluginApi
    loader.modules.set('rev-1', {
      createPlugin: (binding: ClientPluginApi) => {
        received = binding
        return () => undefined
      },
    })
    const adapter = new CordisClientAdapter(
      context,
      loader,
      () => undefined,
      () => api,
    )

    await adapter.reconcile(command('operation-1', ['rev-1']))

    expect(received).toBe(api)
    expect(received).toMatchObject({
      pluginId: 'com.example.client',
      revision: 'rev-1',
    })
    await adapter.dispose()
  })

  it('rejects a bundle hash mismatch', async () => {
    const bytes = new TextEncoder().encode('client')
    await expect(verifyBundle(bytes, '0'.repeat(64))).rejects.toThrow('SHA-256 mismatch')
  })
})
