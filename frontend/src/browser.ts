import { Context } from '@deepseek-ai/cordis'

import { mountChat } from './chat.js'
import { BridgeConnection } from './connection.js'

const root = document.querySelector<HTMLElement>('#harness-root')
if (!root) throw new Error('Harness bootstrap root is missing')
const chat = mountChat(
  root,
  root.dataset.sessionId ?? 'default',
  root.dataset.model || 'deepseek-chat',
)

const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
const socket = new WebSocket(`${protocol}//${location.host}/bridge`)
const connection = new BridgeConnection(crypto.randomUUID(), socket, new Context())

root.dataset.bridge = 'connecting'
chat.setBridgeStatus('connecting')
socket.addEventListener('open', () => {
  connection.start()
  root.dataset.bridge = 'connected'
  chat.setBridgeStatus('connected')
})
socket.addEventListener('close', () => {
  root.dataset.bridge = 'closed'
  chat.setBridgeStatus('closed')
})
window.addEventListener('beforeunload', () => {
  void connection.dispose()
  chat.dispose()
}, { once: true })
