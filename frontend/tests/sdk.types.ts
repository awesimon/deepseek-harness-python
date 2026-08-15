import type { JsonValue } from '../src/protocol.js'
import {
  clientEvent,
  defineClientPlugin,
  rpcMethod,
  serverEvent,
  type ClientPluginContext,
} from '../src/sdk.js'

const method = rpcMethod<{ verbose: boolean }, { title: string }>('describe')
const outgoing = clientEvent<{ value: number }>('outgoing')
const incoming = serverEvent<{ label: string }>('incoming')

defineClientPlugin(async (ctx) => {
  const result = await ctx.call(method, { verbose: true })
  result.title satisfies string
  ctx.emit(outgoing, { value: 1 })
  ctx.on(incoming, ({ label }) => {
    label satisfies string
  })

  // @ts-expect-error client-origin Events cannot be listened to.
  ctx.on(outgoing, () => undefined)
  // @ts-expect-error server-origin Events cannot be emitted by the client.
  ctx.emit(incoming, { label: 'wrong direction' })
  // @ts-expect-error RPC argument fields retain their declared types.
  await ctx.call(method, { verbose: 'yes' })
  // @ts-expect-error production factories do not accept a Plugin ID.
  defineClientPlugin(() => undefined, 'com.example.plugin')
})

declare const context: ClientPluginContext
declare const dynamicPayload: JsonValue
// @ts-expect-error a generic JSON value does not satisfy the declared Event payload.
context.emit(outgoing, dynamicPayload)
