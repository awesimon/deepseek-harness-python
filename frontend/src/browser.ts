import { Context } from '@deepseek-ai/cordis'

import { BridgeConnection } from './connection.js'

const root = document.querySelector<HTMLElement>('#harness-root')
if (!root) throw new Error('Harness bootstrap root is missing')

const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
const socket = new WebSocket(`${protocol}//${location.host}/bridge`)
const connection = new BridgeConnection(crypto.randomUUID(), socket, new Context())

root.dataset.bridge = 'connecting'
socket.addEventListener('open', () => {
  connection.start()
  root.dataset.bridge = 'connected'
})
socket.addEventListener('close', () => {
  root.dataset.bridge = 'closed'
})
window.addEventListener('beforeunload', () => {
  void connection.dispose()
}, { once: true })
