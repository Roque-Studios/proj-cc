import { LitElement, html, css, nothing } from 'lit'
import { customElement, state, query } from 'lit/decorators.js'

import '../components/data/avatar.ts'
import '../components/layouts/card.ts'
import '../components/buttons/button.ts'
import '../components/inputs/textarea.ts'
import '../components/feedback/spinner.ts'
import '../components/feedback/toast.ts'
import { api, ApiError, getAccessToken } from '../lib/api'
import type { ChatMessage, Conversation, MessagesStatus } from '../lib/api'

const PAGE_SIZE = 50

/**
 * DM chat page (`/chat`): creator <-> subscriber messaging.
 *
 * Mobile-first, roque-* components. Layout:
 * - the **inbox** lists the user's conversations (other party + last-message
 *   preview), most recent first;
 * - opening a thread loads the **paginated** message history (newest page
 *   first; scrolling to the top loads older pages via the id cursor) and opens
 *   a **WebSocket** (`/ws/dms?token=…`) so new messages appear instantly for
 *   an online counterparty — the same frame protocol the backend uses
 *   (``send``/``ack``/``message``/``error``/``ping``/``pong``).
 * - the **composer** only renders when messaging is allowed (``can_message``
 *   from ``GET /messages/status``); otherwise a disabled-messaging panel
 *   explains why (e.g. the creator turned messaging off and there's no
 *   existing thread).
 *
 * Sends go through the WebSocket when connected (instant, same gate as REST);
 * a send before the socket is open falls back to ``POST /messages``. Messages
 * are deduped by id, so a REST push and the WS relay can never double-render.
 */
@customElement('roque-dm-chat')
export class DmChat extends LitElement {
  @state() private conversations: Conversation[] = []
  @state() private activeId: number | null = null
  @state() private messages: ChatMessage[] = []
  @state() private beforeId: number | null = null
  @state() private hasMore = false
  @state() private loadingInbox = true
  @state() private loadingThread = false
  @state() private loadingOlder = false
  @state() private sending = false
  @state() private error = ''
  @state() private toastMessage = ''
  @state() private toastHeading = ''
  @state() private wsState: 'connecting' | 'open' | 'closed' = 'connecting'
  @state() private msgStatus: MessagesStatus | null = null
  @state() private myUserId: number | null = null
  @state() private draft = ''

  private _ws: WebSocket | null = null
  private _activeConversation: Conversation | null = null

  @query('.thread-scroll') private _threadEl!: HTMLElement | null

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .app {
      max-width: 960px;
      margin: 0 auto;
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 12px;
      padding: 12px;
      min-height: calc(100vh - 24px);
    }

    .header {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .brand {
      font-size: 16px;
      font-weight: 600;
      color: #1e395b;
    }

    /* --- Inbox --- */
    .inbox {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .inbox-item {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      border: 1px solid #c8d2dc;
      border-radius: 4px;
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.8),
        rgba(173, 216, 230, 0.18)
      );
      cursor: pointer;
      transition: box-shadow 0.2s ease, border-color 0.2s ease;
    }

    .inbox-item:hover {
      border-color: #5b9ed6;
      box-shadow: 0 0 5px rgba(0, 162, 232, 0.4);
    }

    .inbox-item.active {
      border-color: #3c7fb1;
      box-shadow: 0 0 0 1px #3c7fb1;
    }

    .inbox-info {
      flex: 1;
      min-width: 0;
    }

    .inbox-name {
      font-size: 13px;
      font-weight: 600;
      color: #1e1e1e;
    }

    .inbox-preview {
      font-size: 12px;
      color: #6b7a8a;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .inbox-time {
      font-size: 10px;
      color: #8a97a5;
      white-space: nowrap;
    }

    .empty {
      padding: 26px;
      text-align: center;
      color: #6b7a8a;
      font-size: 13px;
    }

    /* --- Thread --- */
    .thread {
      display: flex;
      flex-direction: column;
      height: calc(100vh - 90px);
      min-height: 420px;
    }

    .thread-head {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 12px;
      border-bottom: 1px solid #d0d8e0;
    }

    .thread-name {
      font-size: 14px;
      font-weight: 600;
      color: #1e395b;
      flex: 1;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .thread-scroll {
      flex: 1;
      overflow-y: auto;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      background: rgba(255, 255, 255, 0.35);
    }

    .older-row {
      text-align: center;
      padding: 4px;
      font-size: 11px;
      color: #7a8794;
    }

    .bubble {
      max-width: 78%;
      padding: 8px 12px;
      border-radius: 12px;
      font-size: 13px;
      line-height: 1.45;
      word-wrap: break-word;
      white-space: pre-wrap;
      box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
    }

    .bubble.mine {
      align-self: flex-end;
      background: linear-gradient(to bottom, #e2f0fb, #c9e3f5);
      border: 1px solid #9ec4e0;
      border-bottom-right-radius: 3px;
      color: #10324f;
    }

    .bubble.theirs {
      align-self: flex-start;
      background: #ffffff;
      border: 1px solid #d5dce3;
      border-bottom-left-radius: 3px;
      color: #222;
    }

    .bubble-time {
      display: block;
      margin-top: 4px;
      font-size: 10px;
      color: #7a8794;
      text-align: right;
    }

    /* --- Composer / disabled state --- */
    .composer {
      display: flex;
      gap: 8px;
      padding: 10px 12px;
      border-top: 1px solid #d0d8e0;
      background: rgba(255, 255, 255, 0.6);
    }

    .composer roque-textarea {
      flex: 1;
    }

    .disabled-panel {
      padding: 14px 12px;
      border-top: 1px solid #d0d8e0;
      display: flex;
      align-items: flex-start;
      gap: 10px;
      font-size: 12px;
      color: #5a6a7a;
      background: rgba(254, 240, 185, 0.35);
    }

    .disabled-panel roque-icon {
      flex-shrink: 0;
      margin-top: 1px;
    }

    .back-link {
      display: none;
      font-size: 12px;
      color: #3c7fb1;
      cursor: pointer;
      text-decoration: underline;
    }

    .error-box {
      padding: 16px;
      text-align: center;
      color: #721c24;
      font-size: 13px;
    }

    .spinner-wrap {
      display: flex;
      justify-content: center;
      padding: 40px 0;
    }

    /* Mobile: one pane at a time — the host class 'thread-open' is toggled
       from updated() so the CSS reacts to the active thread (static styles
       cannot read instance state). */
    @media (max-width: 720px) {
      .app {
        grid-template-columns: 1fr;
        padding: 8px;
      }

      :host(.thread-open) .inbox-pane {
        display: none;
      }

      :host(:not(.thread-open)) .thread-pane {
        display: none;
      }

      .back-link {
        display: inline-block;
      }

      .thread {
        height: calc(100vh - 120px);
      }
    }
  `

  connectedCallback() {
    super.connectedCallback()
    this.myUserId = this._decodeUserId()
    this._syncHostClass()
    void this._loadInbox()
  }

  updated(changed: Map<string, unknown>) {
    if (changed.has('activeId')) this._syncHostClass()
  }

  /** Toggle the ``thread-open`` host class for the mobile one-pane layout. */
  private _syncHostClass() {
    this.classList.toggle('thread-open', this.activeId != null)
  }

  disconnectedCallback() {
    super.disconnectedCallback()
    this._closeWs()
  }

  private _decodeUserId(): number | null {
    const token = getAccessToken()
    if (!token) return null
    try {
      const payload = JSON.parse(atob(token.split('.')[1]))
      return Number(payload.sub)
    } catch {
      return null
    }
  }

  private async _loadInbox() {
    try {
      this.conversations = await api.getConversations()
    } catch (e) {
      this.error = e instanceof ApiError ? e.message : 'Could not load conversations'
    } finally {
      this.loadingInbox = false
    }
    // Open a thread directly when the url says so (/chat?conversation={id}).
    const raw = new URLSearchParams(window.location.search).get('conversation')
    if (raw && /^\d+$/.test(raw)) {
      const conv = this.conversations.find((c) => c.id === Number(raw))
      if (conv) this._openThread(conv)
    }
  }

  // ------------------------------------------------------------------ #
  // Thread
  // ------------------------------------------------------------------ #

  private async _openThread(conversation: Conversation) {
    this._activeConversation = conversation
    this.activeId = conversation.id
    this.messages = []
    this.beforeId = null
    this.hasMore = false
    this.msgStatus = null
    this.draft = ''
    this.loadingThread = true
    this.error = ''
    try {
      const [page, status] = await Promise.all([
        api.getConversationMessages(conversation.id, PAGE_SIZE),
        // The counterparty's policy state drives the composer gate.
        api.getMessagesStatus(
          this.myUserId === conversation.creator_id
            ? conversation.subscriber_id
            : conversation.creator_id,
        ),
      ])
      this.messages = page.messages
      this.beforeId = page.before_id
      this.hasMore = page.has_more
      this.msgStatus = status
      await this._openWs()
      await this._scrollToBottom(false)
    } catch (e) {
      this.error = e instanceof ApiError ? e.message : 'Could not load the conversation'
    } finally {
      this.loadingThread = false
    }
  }

  private _backToInbox() {
    this._closeWs()
    this._activeConversation = null
    this.activeId = null
    this.messages = []
    this.msgStatus = null
    if (window.history.replaceState) {
      window.history.replaceState(null, '', '/chat')
    }
  }

  private async _loadOlder() {
    if (this.loadingOlder || !this.hasMore || !this._activeConversation) return
    this.loadingOlder = true
    const scroller = this._threadEl
    const prevHeight = scroller?.scrollHeight ?? 0
    try {
      const page = await api.getConversationMessages(
        this._activeConversation.id,
        PAGE_SIZE,
        this.beforeId ?? undefined,
      )
      this.messages = [...page.messages, ...this.messages]
      this.beforeId = page.before_id
      this.hasMore = page.has_more
      // Preserve the scroll anchor (newer messages stay put).
      if (scroller) scroller.scrollTop = scroller.scrollHeight - prevHeight
    } catch {
      /* transient — the next scroll retries */
    } finally {
      this.loadingOlder = false
    }
  }

  private _onThreadScroll() {
    const scroller = this._threadEl
    if (scroller && scroller.scrollTop < 40) this._loadOlder()
  }

  private async _scrollToBottom(animate = true) {
    await new Promise((r) => requestAnimationFrame(r))
    const scroller = this._threadEl
    if (scroller) {
      scroller.scrollTop = scroller.scrollHeight
      void animate
    }
  }

  // ------------------------------------------------------------------ #
  // WebSocket
  // ------------------------------------------------------------------ #

  private _wsUrl(): string {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const token = encodeURIComponent(getAccessToken() ?? '')
    return `${proto}://${window.location.host}/api/ws/dms?token=${token}`
  }

  private _openWs() {
    if (this._ws && this._ws.readyState <= WebSocket.OPEN) return
    try {
      const ws = new WebSocket(this._wsUrl())
      this._ws = ws
      this.wsState = 'connecting'
      ws.onopen = () => {
        this.wsState = 'open'
        // Keepalive so proxies don't drop the idle socket.
        this._pingTimer = window.setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'ping' }))
        }, 30000)
      }
      ws.onmessage = (ev) => this._onWsFrame(ev.data)
      ws.onclose = () => {
        this.wsState = 'closed'
        if (this._pingTimer) {
          clearInterval(this._pingTimer)
          this._pingTimer = null
        }
        // Auto-reconnect (best-effort) so a blip doesn't kill realtime.
        if (this._activeConversation) {
          window.setTimeout(() => {
            if (this._activeConversation) this._openWs()
          }, 3000)
        }
      }
      ws.onerror = () => ws.close()
    } catch {
      this.wsState = 'closed'
    }
  }

  private _closeWs() {
    if (this._pingTimer) {
      clearInterval(this._pingTimer)
      this._pingTimer = null
    }
    if (this._ws) {
      this._ws.onclose = null
      this._ws.close()
      this._ws = null
    }
    this.wsState = 'closed'
  }

  private _pingTimer: ReturnType<typeof setInterval> | null = null

  private _onWsFrame(raw: string) {
    let frame: Record<string, unknown>
    try {
      frame = JSON.parse(raw)
    } catch {
      return
    }
    const type = frame['type']
    if (type === 'message') {
      const message = frame['message'] as ChatMessage
      this._ingestMessage(message)
    } else if (type === 'ack') {
      // Our own send echoed back — the message is already appended
      // optimistically; replace it with the authoritative persisted shape.
      const message = frame['message'] as ChatMessage
      this._ingestMessage(message, { authoritative: true })
    } else if (type === 'error') {
      // The rejected send never happened — drop its optimistic bubble.
      this._dropLastOptimistic()
      this._toast(String(frame['detail'] ?? 'Message rejected'), 'Messaging blocked')
    }
  }

  private _ingestMessage(message: ChatMessage, opts: { authoritative?: boolean } = {}) {
    const conv = this._activeConversation
    if (!conv) return
    if (message.conversation_id !== conv.id) return

    if (opts.authoritative) {
      // Drop the optimistic copy (client-generated negative id) for this
      // conversation first — the persisted message has a positive DB id, so
      // an id-match could never find it; without this the same message would
      // render twice (the phantom + the real one).
      this.messages = this.messages.filter(
        (m) => !(m.id < 0 && m.conversation_id === message.conversation_id),
      )
    }

    const existing = this.messages.some((m) => m.id === message.id)
    if (existing) {
      if (opts.authoritative) {
        // Replace the optimistic copy with the persisted one.
        this.messages = this.messages.map((m) =>
          m.id === message.id ? message : m,
        )
      }
      return
    }
    const wasAtBottom = this._isAtBottom()
    this.messages = [...this.messages, message]
    this._bumpInboxPreview(message)
    if (wasAtBottom) void this._scrollToBottom()
  }

  /** Remove the most recent optimistic (unsent) bubble, if any. */
  private _dropLastOptimistic() {
    let dropped = false
    this.messages = this.messages.filter((m) => {
      if (dropped) return true
      if (m.id < 0) {
        dropped = true
        return false
      }
      return true
    })
  }

  private _isAtBottom(): boolean {
    const scroller = this._threadEl
    if (!scroller) return true
    return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80
  }

  private _bumpInboxPreview(message: ChatMessage) {
    this.conversations = this.conversations
      .map((c) =>
        c.id === message.conversation_id ? { ...c, last_message: message } : c,
      )
      .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))
  }

  // ------------------------------------------------------------------ #
  // Sending
  // ------------------------------------------------------------------ #

  private _onDraftInput(e: CustomEvent) {
    this.draft = e.detail?.value ?? ''
  }

  private _onComposerKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void this._send()
    }
  }

  private async _send() {
    if (this.sending || !this._activeConversation || !this.draft.trim()) return
    if (this.msgStatus && !this.msgStatus.can_message) {
      this._toast('Messaging is disabled for this conversation.', 'Messaging blocked')
      return
    }
    const recipientId =
      this.myUserId === this._activeConversation.creator_id
        ? this._activeConversation.subscriber_id
        : this._activeConversation.creator_id
    const body = this.draft.trim()
    this.draft = ''
    this.sending = true

    // Optimistic append (the WS ack or REST response replaces it).
    const optimistic: ChatMessage = {
      id: -Date.now(),
      conversation_id: this._activeConversation.id,
      sender_id: this.myUserId ?? 0,
      recipient_id: recipientId,
      body,
      read_at: null,
      created_at: new Date().toISOString(),
    }
    this.messages = [...this.messages, optimistic]
    void this._scrollToBottom()

    try {
      if (this._ws && this._ws.readyState === WebSocket.OPEN) {
        // Live path: the server persists through the same gate, acks, and
        // pushes to the recipient's sockets (and our own relay copy).
        this._ws.send(JSON.stringify({ type: 'send', recipient_id: recipientId, body }))
      } else {
        // Fallback: REST (works even without a socket; still pushes live).
        const saved = await api.sendMessage(recipientId, body)
        this._ingestMessage(saved, { authoritative: true })
      }
    } catch (e) {
      // The send failed — remove the optimistic copy so no phantom bubble
      // stays in the thread.
      this._dropLastOptimistic()
      this._toast(
        e instanceof ApiError ? e.message : 'Message failed to send',
        'Send failed',
      )
    } finally {
      this.sending = false
    }
  }

  // ------------------------------------------------------------------ #
  // Render
  // ------------------------------------------------------------------ #

  private _toast(message: string, heading: string) {
    this.toastMessage = message
    this.toastHeading = heading
    window.setTimeout(() => (this.toastMessage = ''), 6000)
  }

  private _formatTime(iso: string): string {
    try {
      return new Date(iso).toLocaleTimeString(undefined, {
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      return ''
    }
  }

  private _formatDay(iso: string): string {
    try {
      return new Date(iso).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
      })
    } catch {
      return ''
    }
  }

  private _otherName(conversation: Conversation): string {
    return conversation.other.username || `User ${conversation.other.id}`
  }

  render() {
    if (this.loadingInbox) {
      return html`<div class="spinner-wrap"><roque-spinner size="36" label="Loading chats…"></roque-spinner></div>`
    }
    if (this.error && this.conversations.length === 0) {
      return html`<roque-card><div class="error-box">${this.error}</div></roque-card>`
    }

    const active = this._activeConversation

    return html`
      <div class="app">
        <div class="header">
          <span class="brand">Messages</span>
          <span class="brand" style="font-size:11px;color:#5a6a7a;font-weight:400">
            ${this.wsState === 'open'
              ? '● live'
              : this.wsState === 'connecting'
                ? 'connecting…'
                : 'offline (reconnecting)'}
          </span>
        </div>

        <div class="inbox-pane">
          <div class="inbox">
            ${this.conversations.length === 0
              ? html`<roque-card><div class="empty">No conversations yet.</div></roque-card>`
              : this.conversations.map(
                  (c) => html`
                    <div
                      class="inbox-item ${this.activeId === c.id ? 'active' : ''}"
                      @click="${() => this._openThread(c)}"
                      @keydown="${(e: KeyboardEvent) => {
                        if (e.key === 'Enter') {
                          e.preventDefault()
                          this._openThread(c)
                        }
                      }}"
                      role="button"
                      tabindex="0"
                    >
                      <roque-avatar alt="${this._otherName(c)}" size="40"></roque-avatar>
                      <div class="inbox-info">
                        <div class="inbox-name">${this._otherName(c)}</div>
                        <div class="inbox-preview">${c.last_message?.body ?? '—'}</div>
                      </div>
                      <div class="inbox-time">
                        ${c.last_message
                          ? this._formatDay(c.last_message.created_at)
                          : ''}
                      </div>
                    </div>
                  `,
                )}
          </div>
        </div>

        <div class="thread-pane">
          ${active
            ? html`
                <roque-card class="thread">
                  <div class="thread-head">
                    <span class="back-link" @click="${this._backToInbox}">← Inbox</span>
                    <roque-avatar alt="${this._otherName(active)}" size="36"></roque-avatar>
                    <span class="thread-name">${this._otherName(active)}</span>
                  </div>

                  <div class="thread-scroll" @scroll="${this._onThreadScroll}">
                    ${this.loadingOlder
                      ? html`<div class="older-row">loading older…</div>`
                      : this.hasMore
                        ? html`<div class="older-row">scroll for older messages</div>`
                        : nothing}
                    ${this.loadingThread
                      ? html`<div class="older-row">loading…</div>`
                      : nothing}
                    ${this.messages.map(
                      (m) => html`
                        <div class="bubble ${m.id < 0 || m.sender_id === this.myUserId ? 'mine' : 'theirs'}">
                          ${m.body}
                          <span class="bubble-time">${this._formatTime(m.created_at)}</span>
                        </div>
                      `,
                    )}
                  </div>

                  ${this.msgStatus && !this.msgStatus.can_message
                    ? html`
                        <div class="disabled-panel">
                          <roque-icon name="lock" size="14"></roque-icon>
                          <span>${this.msgStatus.reason}</span>
                        </div>
                      `
                    : html`
                        <div class="composer">
                          <roque-textarea
                            class="composer-input"
                            rows="1"
                            placeholder="Write a message…"
                            .value="${this.draft}"
                            @aero-input="${this._onDraftInput}"
                            @keydown="${this._onComposerKeydown}"
                          ></roque-textarea>
                          <roque-button
                            buttonId="chat-send"
                            @aero-click="${this._send}"
                            >${this.sending ? 'Sending…' : 'Send'}</roque-button
                          >
                        </div>
                      `}
                </roque-card>
              `
            : html`<roque-card><div class="empty">Select a conversation to start chatting.</div></roque-card>`}
        </div>
      </div>

      ${this.toastMessage
        ? html`<roque-toast
            icon="info"
            heading="${this.toastHeading}"
            message="${this.toastMessage}"
            visible
          ></roque-toast>`
        : nothing}
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-dm-chat': DmChat
  }
}
