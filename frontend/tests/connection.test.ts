import { Context } from '@deepseek-ai/cordis'
import { describe, expect, it } from 'vitest'

import {
  BridgeConnection,
  type ClientModuleLoader,
  type DesiredClient,
  type LoadedClientModule,
} from '../src/index.js'

class TestSocket extends EventTarget {
  readonly sent: Record<string, unknown>[] = []
  closed = false

  send(data: string): void {
    this.sent.push(JSON.parse(data) as Record<string, unknown>)
  }

  close(): void {
    this.closed = true
  }

  receive(frame: Record<string, unknown>): void {
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(frame) }))
  }
}

class EmptyLoader implements ClientModuleLoader {
  async load(_desired: DesiredClient): Promise<LoadedClientModule> {
    throw new Error('no client modules expected')
  }
}

function socketAsWebSocket(socket: TestSocket): WebSocket {
  return socket as unknown as WebSocket
}

describe('BridgeConnection', () => {
  it('adds exact identity to RPC and Events and routes responses', async () => {
    const socket = new TestSocket()
    const connection = new BridgeConnection(
      'page-1',
      socketAsWebSocket(socket),
      new Context(),
      new EmptyLoader(),
    )
    connection.start()
    const channel = connection.channel('com.example.client', 'rev-1')
    const call = channel.call('echo', { value: 'hello' })
    const callFrame = socket.sent.at(-1)
    expect(callFrame).toMatchObject({
      type: 'rpc-call',
      pageId: 'page-1',
      pluginId: 'com.example.client',
      revision: 'rev-1',
    })
    socket.receive({
      protocol: '1',
      type: 'rpc-result',
      callId: callFrame?.callId,
      result: { echo: 'hello' },
    })
    await expect(call).resolves.toEqual({ echo: 'hello' })

    const events: unknown[] = []
    const dispose = channel.on('changed', (payload) => {
      events.push(payload)
    })
    socket.receive({
      protocol: '1',
      type: 'event',
      pageId: 'page-1',
      pluginId: 'com.example.client',
      revision: 'rev-1',
      name: 'changed',
      payload: { value: 1 },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(events).toEqual([{ value: 1 }])
    dispose()

    channel.emit('changed', { value: 2 })
    expect(socket.sent.at(-1)).toMatchObject({
      type: 'event',
      pageId: 'page-1',
      pluginId: 'com.example.client',
      revision: 'rev-1',
    })
    await connection.dispose()
  })

  it('sends cancellation and rejects the pending call', async () => {
    const socket = new TestSocket()
    const connection = new BridgeConnection(
      'page-1',
      socketAsWebSocket(socket),
      new Context(),
      new EmptyLoader(),
    )
    connection.start()
    const controller = new AbortController()
    const call = connection.channel('com.example.client', 'rev-1').call('wait', {}, controller.signal)
    controller.abort()

    await expect(call).rejects.toMatchObject({ name: 'AbortError' })
    expect(socket.sent.at(-1)).toMatchObject({ type: 'rpc-cancel', pageId: 'page-1' })
    await connection.dispose()
  })
})
