import { describe, expect, it } from 'vitest'

import { ChatApi, ChatApiError } from '../src/chat.js'

interface RequestRecord {
  readonly input: RequestInfo | URL
  readonly init: RequestInit | undefined
}

function response(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  } as Response
}

describe('ChatApi', () => {
  it('loads the active Session projection and invokes the Agent', async () => {
    const requests: RequestRecord[] = []
    const api = new ChatApi(async (input, init) => {
      requests.push({ input, init })
      if (requests.length === 1) {
        return response({
          session_id: 'session/one',
          transcript: [{ sequence: 1, kind: 'user', content: 'hello' }],
        })
      }
      return response({
        id: 'chatcmpl-test',
        choices: [{ message: { role: 'assistant', content: 'world' } }],
      })
    })

    await expect(api.loadSession('session/one')).resolves.toEqual({
      session_id: 'session/one',
      transcript: [{ sequence: 1, kind: 'user', content: 'hello' }],
    })
    await expect(api.invoke('hello', 'invocation-1', 'deepseek-v4-flash')).resolves.toBe('world')
    expect(requests.map(({ input }) => input)).toEqual([
      '/api/v1/sessions/session%2Fone',
      '/chat/completions',
    ])
    expect(requests[1]?.init).toMatchObject({
      method: 'POST',
      body: JSON.stringify({
        model: 'deepseek-v4-flash',
        messages: [{ role: 'user', content: 'hello' }],
        stream: false,
      }),
      headers: expect.objectContaining({ 'X-Request-ID': 'invocation-1' }),
    })
  })

  it('preserves structured Host errors', async () => {
    const api = new ChatApi(async () => response(
      { code: 'route_unavailable', message: 'no route' },
      503,
    ))

    await expect(api.invoke('hello', 'invocation-2')).rejects.toEqual(
      new ChatApiError('route_unavailable', 'no route', 503),
    )
  })

  it('consumes DeepSeek-compatible SSE deltas until DONE', async () => {
    const encoder = new TextEncoder()
    const api = new ChatApi(async () => ({
      ok: true,
      status: 200,
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(encoder.encode(
            'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
            + 'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n',
          ))
          controller.enqueue(encoder.encode('data: [DONE]\n\n'))
          controller.close()
        },
      }),
    }) as Response)
    const deltas: string[] = []

    await api.stream('hello', 'invocation-3', 'deepseek-chat', (delta) => deltas.push(delta))

    expect(deltas).toEqual(['hel', 'lo'])
  })
})
