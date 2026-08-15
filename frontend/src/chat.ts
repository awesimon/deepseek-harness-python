export interface SessionTranscriptEntry {
  readonly sequence: number
  readonly kind: string
  readonly content: unknown
}

interface SessionResponse {
  readonly session_id: string
  readonly transcript: readonly SessionTranscriptEntry[]
}

interface InvocationResponse {
  readonly message?: {
    readonly role?: unknown
    readonly content?: unknown
  }
}

export class ChatApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

type Fetcher = typeof fetch

/** HTTP client for the active Host Session and serialized Agent invocations. */
export class ChatApi {
  constructor(private readonly fetcher: Fetcher = (...args) => globalThis.fetch(...args)) {}

  /** Load the deterministic transcript for one active Session. */
  async loadSession(sessionId: string): Promise<SessionResponse> {
    const response = await this.fetcher(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, {
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    })
    const payload = await readJson(response)
    if (!response.ok) throw apiError(response.status, payload)
    if (!isRecord(payload) || typeof payload.session_id !== 'string' || !Array.isArray(payload.transcript)) {
      throw new Error('Host returned an invalid Session projection')
    }
    return {
      session_id: payload.session_id,
      transcript: payload.transcript.map(parseTranscriptEntry),
    }
  }

  /** Submit one user input and return the committed Assistant message. */
  async invoke(input: string, invocationId = randomId(), model = 'deepseek-chat'): Promise<string> {
    const response = await this.fetcher(
      '/chat/completions',
      {
        method: 'POST',
        headers: {
          Accept: 'application/json',
          'Content-Type': 'application/json',
          'X-Request-ID': invocationId,
        },
        body: JSON.stringify({
          model,
          messages: [{ role: 'user', content: input }],
          stream: false,
        }),
      },
    )
    const payload = await readJson(response)
    if (!response.ok) throw apiError(response.status, payload)
    if (
      !isRecord(payload)
      || !Array.isArray(payload.choices)
      || !isRecord(payload.choices[0])
      || !isRecord(payload.choices[0].message)
      || typeof payload.choices[0].message.content !== 'string'
    ) {
      throw new Error('Host returned an invalid Assistant response')
    }
    return payload.choices[0].message.content
  }

  /** Consume one DeepSeek-compatible streaming completion and emit content deltas. */
  async stream(
    input: string,
    invocationId = randomId(),
    model = 'deepseek-chat',
    onDelta: (content: string) => void,
  ): Promise<void> {
    const response = await this.fetcher('/chat/completions', {
      method: 'POST',
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
        'X-Request-ID': invocationId,
      },
      body: JSON.stringify({
        model,
        messages: [{ role: 'user', content: input }],
        stream: true,
      }),
    })
    if (!response.ok) throw apiError(response.status, await readJson(response))
    const reader = response.body?.getReader()
    if (!reader) throw new Error('Host returned an unreadable streaming response')
    const decoder = new TextDecoder()
    let pending = ''
    let completed = false
    while (!completed) {
      const chunk = await reader.read()
      pending += decoder.decode(chunk.value, { stream: !chunk.done })
      const lines = pending.split(/\r?\n/u)
      pending = lines.pop() ?? ''
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        const data = line.slice(6)
        if (data === '[DONE]') {
          completed = true
          break
        }
        const parsed = JSON.parse(data) as unknown
        if (!isRecord(parsed) || !Array.isArray(parsed.choices) || !isRecord(parsed.choices[0])) {
          throw new Error('Host returned an invalid streaming choice')
        }
        const delta = parsed.choices[0].delta
        if (isRecord(delta) && typeof delta.content === 'string') onDelta(delta.content)
      }
      if (chunk.done) break
    }
    if (!completed) throw new Error('Host stream ended before [DONE]')
  }
}

interface ChatMount {
  setBridgeStatus(status: 'connecting' | 'connected' | 'closed'): void
  dispose(): void
}

interface ChatMessage {
  readonly kind: 'user' | 'assistant' | 'tool' | 'error'
  content: string
  readonly sequence?: number
}

const STYLE_ID = 'harness-chat-styles'

/** Mount the product chat surface into the Host bootstrap root. */
export function mountChat(
  root: HTMLElement,
  sessionId: string,
  model = 'deepseek-chat',
  api = new ChatApi(),
): ChatMount {
  installStyles(root.ownerDocument)
  const document = root.ownerDocument
  const shell = element(document, 'div', 'chat-shell')
  const sidebar = element(document, 'aside', 'chat-sidebar')
  const brand = element(document, 'div', 'chat-brand')
  brand.innerHTML = '<span class="chat-brand-mark">DS</span><span>DEEPSEEK<br>HARNESS</span>'
  const sessionPanel = element(document, 'section', 'chat-session-panel')
  const sessionLabel = element(document, 'p', 'chat-eyebrow', 'SESSION')
  const sessionValue = element(document, 'p', 'chat-session-id', sessionId)
  const bridgeLabel = element(document, 'p', 'chat-eyebrow', 'BRIDGE')
  const bridgeStatus = element(document, 'p', 'chat-bridge-status')
  const bridgeDot = element(document, 'span', 'chat-status-dot')
  const bridgeText = element(document, 'span', undefined, 'Connecting')
  bridgeStatus.append(bridgeDot, bridgeText)
  const refresh = element(document, 'button', 'chat-refresh', '↻')
  refresh.type = 'button'
  refresh.title = '刷新 Session'
  refresh.setAttribute('aria-label', '刷新 Session')
  sessionPanel.append(sessionLabel, sessionValue, bridgeLabel, bridgeStatus, refresh)
  sidebar.append(brand, sessionPanel)

  const main = element(document, 'main', 'chat-main')
  const header = element(document, 'header', 'chat-header')
  const titleBlock = element(document, 'div')
  titleBlock.append(
    element(document, 'p', 'chat-eyebrow', 'AGENT SESSION'),
    element(document, 'h1', undefined, '对话'),
  )
  const backendStatus = element(document, 'span', 'chat-backend-status', '准备就绪')
  header.append(titleBlock, backendStatus)
  const messages = element(document, 'section', 'chat-messages')
  messages.setAttribute('aria-live', 'polite')
  messages.setAttribute('aria-label', '对话消息')
  const composer = element(document, 'form', 'chat-composer')
  const input = element(document, 'textarea', 'chat-input') as HTMLTextAreaElement
  input.rows = 1
  input.placeholder = '输入消息...'
  input.setAttribute('aria-label', '消息内容')
  const send = element(document, 'button', 'chat-send', '↑')
  send.type = 'submit'
  send.title = '发送消息'
  send.setAttribute('aria-label', '发送消息')
  composer.append(input, send)
  main.append(header, messages, composer)
  shell.append(sidebar, main)
  root.replaceChildren(shell)

  let disposed = false
  let busy = false
  let refreshGeneration = 0
  const transcript: ChatMessage[] = []

  const render = (): void => {
    messages.replaceChildren()
    if (!transcript.length) {
      const empty = element(document, 'div', 'chat-empty')
      empty.append(
        element(document, 'span', 'chat-empty-mark', '◌'),
        element(document, 'p', undefined, '准备开始对话'),
      )
      messages.append(empty)
      return
    }
    for (const message of transcript) {
      const row = element(document, 'article', `chat-message chat-message-${message.kind}`)
      if (message.sequence !== undefined) row.dataset.sequence = String(message.sequence)
      const label = message.kind === 'user'
        ? '你'
        : message.kind === 'assistant'
          ? '助手'
          : message.kind === 'tool'
            ? '工具'
            : '错误'
      row.append(element(document, 'span', 'chat-message-label', label))
      const bubble = element(document, 'div', 'chat-bubble')
      bubble.textContent = message.content
      row.append(bubble)
      messages.append(row)
    }
    messages.scrollTop = messages.scrollHeight
  }

  const setBackendStatus = (text: string, state: 'ready' | 'working' | 'error' = 'ready'): void => {
    backendStatus.textContent = text
    backendStatus.dataset.state = state
  }

  const load = async (): Promise<void> => {
    const generation = ++refreshGeneration
    setBackendStatus('读取 Session... ', 'working')
    try {
      const result = await api.loadSession(sessionId)
      if (disposed || generation !== refreshGeneration) return
      transcript.splice(0, transcript.length, ...result.transcript.flatMap(toChatMessages))
      render()
      setBackendStatus('已同步', 'ready')
    } catch (error) {
      if (disposed || generation !== refreshGeneration) return
      setBackendStatus(errorMessage(error), 'error')
      render()
    }
  }

  const submit = async (event: SubmitEvent): Promise<void> => {
    event.preventDefault()
    if (busy || disposed) return
    const text = input.value.trim()
    if (!text) return
    busy = true
    input.value = ''
    input.style.height = ''
    transcript.push({ kind: 'user', content: text })
    render()
    setBackendStatus('生成中...', 'working')
    input.disabled = true
    send.disabled = true
    try {
      const assistant: ChatMessage = { kind: 'assistant', content: '' }
      transcript.push(assistant)
      render()
      await api.stream(text, undefined, model, (delta) => {
        assistant.content += delta
        render()
      })
      if (disposed) return
      setBackendStatus('已同步', 'ready')
    } catch (error) {
      if (disposed) return
      const last = transcript.at(-1)
      if (last?.kind === 'assistant' && !last.content) transcript.pop()
      transcript.push({ kind: 'error', content: errorMessage(error) })
      setBackendStatus('请求失败', 'error')
    } finally {
      busy = false
      input.disabled = false
      send.disabled = false
      if (!disposed) {
        render()
        input.focus()
      }
    }
  }

  const resize = (): void => {
    input.style.height = 'auto'
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`
  }
  const keydown = (event: KeyboardEvent): void => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      composer.requestSubmit()
    }
  }
  const refreshSession = (): void => {
    if (!busy) void load()
  }
  composer.addEventListener('submit', submit)
  input.addEventListener('input', resize)
  input.addEventListener('keydown', keydown)
  refresh.addEventListener('click', refreshSession)
  render()
  void load()

  return {
    setBridgeStatus(status) {
      if (disposed) return
      bridgeDot.dataset.state = status
      bridgeText.textContent = status === 'connected'
        ? 'Connected'
        : status === 'closed'
          ? 'Closed'
          : 'Connecting'
    },
    dispose() {
      if (disposed) return
      disposed = true
      composer.removeEventListener('submit', submit)
      input.removeEventListener('input', resize)
      input.removeEventListener('keydown', keydown)
      refresh.removeEventListener('click', refreshSession)
    },
  }
}

function toChatMessages(entry: SessionTranscriptEntry): ChatMessage[] {
  if (entry.kind === 'user' || entry.kind === 'assistant') {
    return [{ kind: entry.kind, content: contentText(entry.content), sequence: entry.sequence }]
  }
  if (entry.kind === 'tool') {
    return [{ kind: 'tool', content: contentText(entry.content), sequence: entry.sequence }]
  }
  if (entry.kind === 'step-error') {
    return [{ kind: 'error', content: contentText(entry.content), sequence: entry.sequence }]
  }
  return []
}

function contentText(value: unknown): string {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function parseTranscriptEntry(value: unknown): SessionTranscriptEntry {
  if (!isRecord(value) || typeof value.sequence !== 'number' || typeof value.kind !== 'string') {
    throw new Error('Host returned an invalid transcript entry')
  }
  return { sequence: value.sequence, kind: value.kind, content: value.content }
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json() as unknown
  } catch {
    throw new Error(`Host returned invalid JSON (${response.status})`)
  }
}

function apiError(status: number, payload: unknown): ChatApiError {
  if (isRecord(payload) && typeof payload.code === 'string' && typeof payload.message === 'string') {
    return new ChatApiError(payload.code, payload.message, status)
  }
  return new ChatApiError('request_failed', `Host request failed (${status})`, status)
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

function randomId(): string {
  return typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `browser-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

function element<T extends keyof HTMLElementTagNameMap>(
  document: Document,
  tag: T,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[T] {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

function installStyles(document: Document): void {
  if (document.getElementById(STYLE_ID)) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
    :root {
      color-scheme: light;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #18212f;
      background: #eef1f4;
      font-synthesis: none;
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-width: 320px; min-height: 100vh; background: #eef1f4; }
    button, textarea { font: inherit; }
    button { cursor: pointer; }
    .chat-shell { display: grid; grid-template-columns: 248px minmax(0, 1fr); min-height: 100vh; }
    .chat-sidebar { display: flex; flex-direction: column; justify-content: space-between; padding: 28px 20px; background: #17212b; color: #e8edf2; }
    .chat-brand { display: flex; align-items: center; gap: 11px; font-size: 12px; font-weight: 700; letter-spacing: .12em; line-height: 1.35; }
    .chat-brand-mark { display: grid; place-items: center; width: 36px; height: 36px; border-radius: 8px; background: #ef8354; color: #17212b; font-size: 12px; letter-spacing: 0; }
    .chat-session-panel { position: relative; padding: 18px 0 4px; border-top: 1px solid #35414d; }
    .chat-eyebrow { margin: 0 0 8px; color: #8e9ba8; font-size: 10px; font-weight: 700; letter-spacing: .16em; }
    .chat-session-id { margin: 0 0 24px; overflow: hidden; color: #f4f7f9; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
    .chat-bridge-status { display: flex; align-items: center; gap: 8px; margin: 0; color: #b8c2cb; font-size: 12px; }
    .chat-status-dot { width: 7px; height: 7px; border-radius: 50%; background: #f2b35d; }
    .chat-status-dot[data-state="connected"] { background: #63c29a; }
    .chat-status-dot[data-state="closed"] { background: #d96a6a; }
    .chat-refresh { align-self: flex-start; margin-top: 22px; border: 0; padding: 4px 0; background: transparent; color: #9da9b4; font-size: 22px; line-height: 1; }
    .chat-refresh:hover { color: #ffffff; }
    .chat-main { display: grid; grid-template-rows: auto minmax(0, 1fr) auto; min-width: 0; min-height: 100vh; background: #f7f8fa; }
    .chat-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 30px clamp(22px, 5vw, 72px) 22px; border-bottom: 1px solid #dfe4e8; background: #ffffff; }
    .chat-header h1 { margin: 0; font-size: 24px; font-weight: 700; letter-spacing: 0; }
    .chat-backend-status { color: #71808d; font-size: 12px; }
    .chat-backend-status[data-state="working"] { color: #bd7b26; }
    .chat-backend-status[data-state="error"] { color: #b44949; }
    .chat-messages { display: flex; flex-direction: column; gap: 22px; overflow-y: auto; padding: 34px clamp(22px, 5vw, 72px); }
    .chat-empty { display: grid; place-items: center; align-content: center; gap: 12px; min-height: 320px; color: #71808d; }
    .chat-empty-mark { color: #ef8354; font-size: 30px; }
    .chat-empty p { margin: 0; font-size: 15px; }
    .chat-message { display: grid; grid-template-columns: 52px minmax(0, 1fr); gap: 12px; max-width: 760px; }
    .chat-message-user { align-self: flex-end; grid-template-columns: minmax(0, 1fr) 52px; }
    .chat-message-user .chat-message-label { order: 2; text-align: right; }
    .chat-message-label { padding-top: 9px; color: #8996a2; font-size: 11px; font-weight: 700; }
    .chat-bubble { white-space: pre-wrap; overflow-wrap: anywhere; padding: 13px 16px; border: 1px solid #dfe4e8; border-radius: 8px; background: #ffffff; color: #253242; font-size: 15px; line-height: 1.65; box-shadow: 0 2px 8px rgb(22 33 43 / 4%); }
    .chat-message-user .chat-bubble { border-color: #d4e6e8; background: #e8f2f2; }
    .chat-message-tool .chat-bubble { background: #f1f3f5; color: #586673; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
    .chat-message-error .chat-bubble { border-color: #edcaca; background: #fff3f3; color: #a44242; }
    .chat-composer { display: flex; align-items: flex-end; gap: 12px; margin: 0 clamp(22px, 5vw, 72px) 28px; padding: 10px 10px 10px 16px; border: 1px solid #ccd5dc; border-radius: 8px; background: #ffffff; box-shadow: 0 4px 18px rgb(22 33 43 / 7%); }
    .chat-input { flex: 1; min-height: 24px; max-height: 160px; resize: none; border: 0; outline: 0; background: transparent; color: #18212f; font-size: 15px; line-height: 1.6; }
    .chat-input::placeholder { color: #9aa6b0; }
    .chat-send { display: grid; flex: 0 0 38px; place-items: center; width: 38px; height: 38px; border: 0; border-radius: 6px; background: #ef8354; color: #17212b; font-size: 23px; line-height: 1; }
    .chat-send:hover { background: #e37343; }
    .chat-send:disabled { cursor: wait; opacity: .55; }
    @media (max-width: 720px) {
      .chat-shell { display: block; }
      .chat-sidebar { min-height: auto; padding: 18px 20px; }
      .chat-session-panel { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 8px 12px; padding-top: 15px; }
      .chat-session-panel .chat-eyebrow { margin: 0; }
      .chat-session-id { margin: 0; }
      .chat-bridge-status { grid-column: 2; }
      .chat-refresh { grid-column: 3; grid-row: 1 / span 2; margin: 0; }
      .chat-main { min-height: calc(100vh - 126px); }
      .chat-header { padding-top: 22px; }
      .chat-messages { padding-top: 24px; }
      .chat-composer { margin-bottom: 16px; }
    }
  `
  document.head.append(style)
}
