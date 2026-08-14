/** JSON values admitted by the Browser Bridge wire protocol. */
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }

/** One exact client contribution desired by the Python Host. */
export interface DesiredClient {
  pluginId: string
  revision: string
  bundleUrl: string
  bundleSha256: string
  protocolSchemaUrl: string | null
  activationPolicy: 'required' | 'optional'
}

/** Complete desired graph for one superseding operation. */
export interface ReconcileCommand {
  protocol: '1'
  type: 'reconcile'
  operationId: string
  desired: DesiredClient[]
}

/** Page-local lifecycle state reported for one exact Revision. */
export type PagePluginState = 'absent' | 'loading' | 'active' | 'waiting' | 'failed' | 'unloading'

/** One page-local plugin result. */
export interface PluginLoadResult {
  protocol: '1'
  type: 'plugin-result'
  operationId: string
  pluginId: string
  revision: string
  state: PagePluginState
  error: string | null
}

/** Terminal result for one reconciliation operation. */
export interface ReconcileComplete {
  protocol: '1'
  type: 'reconcile-complete'
  operationId: string
  success: boolean
  error: string | null
}

/** Frames emitted by the Cordis client adapter during reconciliation. */
export type ReconcileResultFrame = PluginLoadResult | ReconcileComplete

/** Initial inventory sent once for a logical browser connection. */
export interface HelloFrame {
  protocol: '1'
  type: 'hello'
  pageId: string
  loaded: Record<string, string>
}

/** Same-plugin/revision call sent from a client contribution. */
export interface RpcCallFrame {
  protocol: '1'
  type: 'rpc-call'
  pageId: string
  callId: string
  pluginId: string
  revision: string
  method: string
  arguments: Record<string, JsonValue>
}

/** Structured backend result for one RPC call. */
export type RpcResultFrame = {
  protocol: '1'
  type: 'rpc-result'
  callId: string
  result: JsonValue
} | {
  protocol: '1'
  type: 'rpc-result'
  callId: string
  errorCode: string
  errorMessage: string
}

/** Best-effort cancellation for one active RPC call. */
export interface RpcCancelFrame {
  protocol: '1'
  type: 'rpc-cancel'
  pageId: string
  callId: string
}

/** Explicit same-plugin/revision Event in either direction. */
export interface BridgeEventFrame {
  protocol: '1'
  type: 'event'
  pageId: string
  pluginId: string
  revision: string
  name: string
  payload: JsonValue
}

/** Browser-originated frames emitted by the client runtime. */
export type ClientFrame = HelloFrame | ReconcileResultFrame | RpcCallFrame | RpcCancelFrame | BridgeEventFrame

/** Host-originated frames accepted by the client runtime. */
export type ServerFrame = ReconcileCommand | RpcResultFrame | BridgeEventFrame
