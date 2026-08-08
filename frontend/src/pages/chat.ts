import { LitElement, html, css, nothing } from 'lit'
import { customElement, property, state, query } from 'lit/decorators.js'

import '../components/data/avatar.ts'
import '../components/layouts/card.ts'
import '../components/buttons/button.ts'
import '../components/inputs/textarea.ts'
import '../components/media/icon.ts'
import '../components/navigation/site-menu.ts'
import '../components/feedback/spinner.ts'
import '../components/feedback/toast.ts'
import { api, ApiError, clearTokens, getAccessToken } from '../lib/api'
import type { ChatMessage, Conversation, MessagesStatus, UserMe } from '../lib/api'

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
  /** Embedded mode (e.g. inside the /admin Conversations tab): hide the
      standalone page chrome — the site menu and the back button — and let the
      tab's own navigation take over. */
  @property({ type: Boolean, reflect: true, attribute: 'embedded' }) embedded = false

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
  /** Photo attachments queued in the composer (sent via the media endpoint). */
  @state() private attach: File[] = []
  /** Whether the composer is set to send one-time paid content. */
  @state() private paidEnabled = false
  /** One-time price in dollars for a paid media message ('' = not set). */
  @state() private paidPrice = ''
  /** Paid messages the user is unlocking (in-flight set by message id). */
  @state() private unlocking = new Set<number>()
  /** The signed-in user for the hamburger menu (null = anonymous). */
  @state() private me: UserMe | null = null
  /** New-conversation compose state (from /chat?recipient={id}&name=…&avatar=…). */
  @state() private compose: {
    recipientId: number
    name: string
    avatar: string
  } | null = null

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
      gap: 8px;
      padding: 8px 6px;
    }

    .brand {
      font-size: 16px;
      font-weight: 600;
      color: var(--cc-heading);
      flex: 1;
      text-align: center;
      min-width: 0;
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
        rgba(var(--cc-tint), 0.18)
      );
      cursor: pointer;
      transition: box-shadow 0.2s ease, border-color 0.2s ease;
    }

    .inbox-item:hover {
      border-color: var(--cc-accent-light);
      box-shadow: 0 0 5px rgba(0, 162, 232, 0.4);
    }

    .inbox-item.active {
      border-color: var(--cc-accent);
      box-shadow: 0 0 0 1px var(--cc-accent);
    }

    .inbox-info {
      flex: 1;
      min-width: 0;
    }

    .inbox-name {
      font-size: 13px;
      font-weight: 600;
      color: var(--cc-text);
    }

    .inbox-preview {
      font-size: 12px;
      color: var(--cc-text-muted);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .inbox-time {
      font-size: 10px;
      color: var(--cc-text-faint);
      white-space: nowrap;
    }

    .empty {
      padding: 26px;
      text-align: center;
      color: var(--cc-text-muted);
      font-size: 13px;
    }

    /* --- New-conversation compose --- */
    .compose-hint {
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .compose-empty {
      max-width: 260px;
      padding: 22px 16px;
      text-align: center;
      color: var(--cc-text-muted);
      font-size: 13px;
      line-height: 1.55;
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
      color: var(--cc-heading);
      flex: 1;
      min-width: 0;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .thread-scroll {
      flex: 1;
      overflow-y: auto;
      padding: 10px;
      display: flex;
      flex-direction: column;
      gap: 5px;
      background: rgba(255, 255, 255, 0.35);
    }

    .older-row {
      text-align: center;
      padding: 4px;
      font-size: 11px;
      color: var(--cc-text-faint);
    }

    .bubble {
      /* Hug the content: a plain block div would stretch to max-width even
         for a short message, producing a giant empty bubble. box-sizing
         keeps max-width capping the whole visual box, padding included. */
      box-sizing: border-box;
      width: fit-content;
      max-width: 70%;
      padding: 4px 9px;
      border-radius: 10px;
      font-size: 12.5px;
      line-height: 1.4;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      box-shadow: 0 1px 1px rgba(0, 0, 0, 0.07);
    }

    .bubble.mine {
      align-self: flex-end;
      background: linear-gradient(to bottom, var(--cc-fill-strong), rgba(var(--cc-tint), 0.55));
      border: 1px solid rgba(var(--cc-tint-deep), 0.55);
      border-bottom-right-radius: 3px;
      color: var(--cc-header-ink);
    }

    .bubble.theirs {
      align-self: flex-start;
      background: var(--cc-client);
      border: 1px solid #d5dce3;
      border-bottom-left-radius: 3px;
      color: #222;
    }

    .bubble-time {
      display: block;
      margin-top: 2px;
      font-size: 9.5px;
      color: var(--cc-text-faint);
      text-align: right;
      white-space: nowrap;
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

    .composer-tools {
      border-top: 1px solid #d0d8e0;
      background: rgba(255, 255, 255, 0.6);
      padding: 6px 12px;
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }

    .attach-btn {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 12px;
      color: var(--cc-accent);
      background: none;
      border: 1px dashed #9fc3dd;
      border-radius: 999px;
      padding: 4px 10px;
      cursor: pointer;
    }

    .attach-btn:hover {
      background: rgba(var(--cc-accent-rgb), 0.08);
    }

    .paid-box {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 12px;
      color: var(--cc-text-secondary);
      cursor: pointer;
      user-select: none;
    }

    .paid-box input {
      accent-color: var(--cc-accent);
    }

    .paid-input {
      width: 72px;
      font-size: 12px;
      padding: 3px 6px;
      border: 1px solid #c3ced9;
      border-radius: 6px;
    }

    .attach-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }

    .attach-thumb {
      position: relative;
      border-radius: 8px;
      overflow: hidden;
      width: 52px;
      height: 52px;
      border: 1px solid #c3ced9;
    }

    .attach-thumb img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
    }

    .attach-x {
      position: absolute;
      top: 1px;
      right: 1px;
      background: rgba(0, 0, 0, 0.65);
      color: var(--cc-client);
      border: none;
      border-radius: 50%;
      width: 16px;
      height: 16px;
      font-size: 10px;
      line-height: 1;
      cursor: pointer;
    }

    .media-img {
      max-width: 220px;
      max-height: 240px;
      border-radius: 10px;
      display: block;
      margin-bottom: 4px;
      cursor: zoom-in;
    }

    .locked-media {
      width: 200px;
      height: 120px;
      border-radius: 10px;
      border: 1px dashed #c9a8a8;
      background: linear-gradient(180deg, #f5ecec, #efe3e3);
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 4px;
      color: #9a5555;
      font-size: 12px;
      margin-bottom: 4px;
    }

    .unlock-row {
      margin-top: 4px;
    }

    .bubble-text {
      white-space: pre-wrap;
      word-break: break-word;
    }

    .disabled-panel {
      padding: 14px 12px;
      border-top: 1px solid #d0d8e0;
      display: flex;
      align-items: flex-start;
      gap: 10px;
      font-size: 12px;
      color: var(--cc-text-secondary);
      background: rgba(254, 240, 185, 0.35);
    }

    .disabled-panel roque-icon {
      flex-shrink: 0;
      margin-top: 1px;
    }

    .back-link {
      display: none;
      font-size: 12px;
      color: var(--cc-accent);
      cursor: pointer;
      text-decoration: underline;
    }

    /* Mobile-first back button in the page header (previous page). */
    .header-back {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      font-size: 13px;
      font-weight: 500;
      color: #2f6ea8;
      cursor: pointer;
      text-decoration: none;
      white-space: nowrap;
      flex-shrink: 0;
      padding: 8px 10px;
      border-radius: 8px;
      transition: background 0.15s ease, transform 0.1s ease;
    }

    .header-back:hover {
      background: rgba(var(--cc-accent-rgb), 0.1);
    }

    .header-back:active {
      transform: scale(0.96);
    }

    /* Connection/live status pill (mobile-first, compact). */
    .status {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 11px;
      font-weight: 500;
      padding: 4px 10px;
      border-radius: 999px;
      white-space: nowrap;
      flex-shrink: 0;
      color: #4a5a6a;
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid #c9d4de;
    }

    .status::before {
      content: '';
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #9aa7b2;
      flex-shrink: 0;
    }

    .status.live {
      color: #17632f;
      border-color: #a9d8b8;
      background: #edf8f1;
    }

    .status.live::before {
      background: #2fbf71;
      box-shadow: 0 0 0 3px rgba(47, 191, 113, 0.16);
    }

    .status.connecting {
      color: #7a6116;
      border-color: #e2cf8f;
      background: #fdf7e2;
    }

    .status.connecting::before {
      background: #d9a916;
    }

    .status.offline {
      color: #6b4f4b;
      border-color: #dcc5c0;
      background: #f9efed;
    }

    .status.offline::before {
      background: #c26a5e;
    }

    .error-box {
      padding: 16px;
      text-align: center;
      color: var(--cc-danger-strong);
      font-size: 13px;
    }

    .spinner-wrap {
      display: flex;
      justify-content: center;
      padding: 40px 0;
    }

    /* Embedded (admin tab): no full-viewport stretch — the tab panel sizes it. */
    :host([embedded]) .app {
      min-height: auto;
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

      /* Mobile-first page chrome: tighter header with a tap-friendly back
         button and the status pill tucked into the corner. */
      .header {
        padding: 6px 8px 10px;
      }

      .brand {
        font-size: 15px;
      }
    }
  `

  connectedCallback() {
    super.connectedCallback()
    this.myUserId = this._decodeUserId()
    this._syncHostClass()
    void this._loadSession()
    void this._loadInbox()
  }

  private async _loadSession() {
    if (!getAccessToken()) return
    try {
      this.me = await api.me()
    } catch {
      // Stale token — request() already cleared it; the menu shows anonymous.
    }
  }

  private _onLogout() {
    const refresh = localStorage.getItem('cc_refresh_token')
    if (refresh) api.logout(refresh).catch(() => undefined)
    clearTokens()
    window.location.href = '/'
  }

  /** Leave the chat back to where the user came from (or the home page). */
  private _goBack() {
    if (window.history.length > 1) {
      window.history.back()
    } else {
      window.location.href = '/'
    }
  }

  updated(changed: Map<string, unknown>) {
    if (changed.has('activeId') || changed.has('compose')) this._syncHostClass()
  }

  /** Toggle the ``thread-open`` host class for the mobile one-pane layout. */
  private _syncHostClass() {
    this.classList.toggle(
      'thread-open',
      this.activeId != null || this.compose != null,
    )
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
    const params = new URLSearchParams(window.location.search)
    const raw = params.get('conversation')
    if (raw && /^\d+$/.test(raw)) {
      const conv = this.conversations.find((c) => c.id === Number(raw))
      if (conv) this._openThread(conv)
      return
    }
    // Start a new conversation with a creator (/chat?recipient={id}&name=…):
    // open the existing thread when there is one, else enter compose mode.
    const rawRecipient = params.get('recipient')
    if (rawRecipient && /^\d+$/.test(rawRecipient)) {
      const recipientId = Number(rawRecipient)
      const existing = this.conversations.find(
        (c) =>
          (this.myUserId === c.creator_id ? c.subscriber_id : c.creator_id) ===
          recipientId,
      )
      if (existing) {
        this._openThread(existing)
        return
      }
      this.compose = {
        recipientId,
        name: params.get('name') || '',
        avatar: params.get('avatar') || '',
      }
      // The creator's DM policy drives the composer gate (follower + setting).
      try {
        this.msgStatus = await api.getMessagesStatus(recipientId)
      } catch {
        // Status unavailable — keep the composer enabled; the backend gate
        // still rejects a blocked send with a clear error.
        this.msgStatus = null
      }
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
    this.compose = null
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

  // ------------------------------------------------------------------ #
  // Composer helpers (photo attachments + paid content)
  // ------------------------------------------------------------------ #

  private _onAttachInput(e: Event) {
    const input = e.target as HTMLInputElement
    const files = Array.from(input.files ?? []).slice(0, 4)
    if (files.length) this.attach = [...this.attach, ...files]
    input.value = ''
  }

  private _removeAttach(index: number) {
    this.attach = this.attach.filter((_, i) => i !== index)
  }

  private _onPaidPrice(e: Event) {
    this.paidPrice = (e.target as HTMLInputElement).value
  }

  /** The composed one-time price in cents (null = free message). */
  private _priceCents(): number | null {
    const v = parseFloat(this.paidPrice)
    if (!this.paidPrice || Number.isNaN(v) || v <= 0) return null
    return Math.round(v * 100)
  }

  private _price(m: ChatMessage): string {
    return `$${((m.price_cents ?? 0) / 100).toFixed(2)}`
  }

  private _mediaUrl(url: string): string {
    const token = getAccessToken()
    if (!token) return url
    return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
  }

  /** A paid message from the other side that hasn't been unlocked yet. */
  private _isPaidLocked(m: ChatMessage): boolean {
    return m.price_cents != null && m.unlocked !== true && m.sender_id !== this.myUserId
  }

  private async _unlockMessage(m: ChatMessage) {
    if (this.unlocking.has(m.id)) return
    this.unlocking = new Set(this.unlocking).add(m.id)
    try {
      const res = await api.unlockMessage(m.id, {
        success_url: window.location.href,
        cancel_url: window.location.href,
      })
      if (res.checkout_url) {
        // Hosted checkout: the subscriber pays on the gateway's page; the
        // payment webhook unlocks the media and the gateway returns them here.
        window.location.assign(res.checkout_url)
        return
      }
      // Already unlocked — refresh the thread so the media renders.
      if (this._activeConversation) await this._openThread(this._activeConversation)
    } catch (e) {
      this._toast(
        e instanceof ApiError ? e.message : 'Unlock failed',
        'Payment failed',
      )
    } finally {
      const next = new Set(this.unlocking)
      next.delete(m.id)
      this.unlocking = next
    }
  }

  /** The composer bar with photo picker + (creator-only) paid controls. */
  private _composerTools() {
    // Only a creator sender can set a one-time price on a media DM.
    const canPrice = this.me?.role === 'creator'
    return html`
      <div class="composer-tools">
        <label class="attach-btn">
          <roque-icon name="image" size="14"></roque-icon>
          Photo
          <input
            type="file"
            accept="image/*"
            multiple
            hidden
            @change="${this._onAttachInput}"
          />
        </label>
        ${canPrice
          ? html`
              <label class="paid-box">
                <input
                  type="checkbox"
                  ?checked="${this.paidEnabled}"
                  @change="${(e: Event) => {
                    const on = (e.target as HTMLInputElement).checked
                    this.paidEnabled = on
                    if (on && !this.paidPrice) this.paidPrice = '5'
                  }}"
                />
                Paid
              </label>
              ${this.paidEnabled
                ? html`<span class="paid-box">$
                    <input
                      class="paid-input"
                      type="number"
                      min="0.01"
                      step="0.01"
                      placeholder="0.00"
                      .value="${this.paidPrice}"
                      @input="${this._onPaidPrice}"
                    />
                  </span>`
                : nothing}
            `
          : nothing}
      </div>
      ${this.attach.length
        ? html`
            <div class="composer-tools">
              <div class="attach-row">
                ${this.attach.map(
                  (f, i) => html`
                    <span class="attach-thumb">
                      <img src="${URL.createObjectURL(f)}" alt="" />
                      <button class="attach-x" @click="${() => this._removeAttach(i)}">
                        ✕
                      </button>
                    </span>
                  `,
                )}
              </div>
            </div>
          `
        : nothing}
    `
  }

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
    const hasAttach = this.attach.length > 0
    if (this.sending || (!this.draft.trim() && !hasAttach)) return
    if (this.msgStatus && !this.msgStatus.can_message) {
      this._toast('Messaging is disabled for this conversation.', 'Messaging blocked')
      return
    }
    // Paid content is a paid *photo*: it needs both a price and an attachment.
    if (this.paidEnabled && !this._priceCents()) {
      this._toast('Enter a price for the paid message.', 'Price required')
      return
    }
    if (this.paidEnabled && !hasAttach) {
      this._toast('Attach a photo to send paid content.', 'Photo required')
      return
    }

    // New conversation: sending the first message creates the thread (REST —
    // there is no WebSocket yet), then switch into the fresh thread view.
    if (this.compose && !this._activeConversation) {
      const recipientId = this.compose.recipientId
      const recipientName = this.compose.name
      const recipientAvatar = this.compose.avatar
      const body = this.draft.trim()
      const price = this._priceCents()
      this.draft = ''
      this.sending = true
      try {
        const saved = hasAttach
          ? await api.sendMessageWithMedia(recipientId, body, this.attach, price)
          : await api.sendMessage(recipientId, body)
        this.attach = []
        this.paidPrice = ''
        this.paidEnabled = false
        this.compose = null
        this.conversations = await api.getConversations()
        // The fresh thread usually surfaces in the refetch; if it doesn't
        // (timing), build it from the saved message so the user still lands
        // in the thread instead of an empty "Select a conversation" state.
        const recipientIsCreator = this.msgStatus?.recipient_is_creator ?? true
        const conv =
          this.conversations.find((c) => c.id === saved.conversation_id) ?? {
            id: saved.conversation_id,
            creator_id: recipientIsCreator ? recipientId : this.myUserId ?? recipientId,
            subscriber_id: recipientIsCreator ? this.myUserId ?? recipientId : recipientId,
            created_at: saved.created_at,
            updated_at: saved.created_at,
            other: { id: recipientId, username: recipientName || null, avatar_url: recipientAvatar || null },
            last_message: saved,
          }
        await this._openThread(conv)
      } catch (e) {
        this._toast(
          e instanceof ApiError ? e.message : 'Message failed to send',
          'Send failed',
        )
      } finally {
        this.sending = false
      }
      return
    }

    if (!this._activeConversation) return
    const recipientId =
      this.myUserId === this._activeConversation.creator_id
        ? this._activeConversation.subscriber_id
        : this._activeConversation.creator_id
    const body = this.draft.trim()
    this.draft = ''
    this.sending = true

    // Media messages (and paid media) travel over REST — the WebSocket is
    // text-only. No optimistic bubble: the multipart POST response is the
    // authoritative message and is ingested once it lands.
    if (hasAttach) {
      try {
        const saved = await api.sendMessageWithMedia(
          recipientId,
          body,
          this.attach,
          this._priceCents(),
        )
        this.attach = []
        this.paidPrice = ''
        this.paidEnabled = false
        this._ingestMessage(saved, { authoritative: true })
      } catch (e) {
        this._toast(
          e instanceof ApiError ? e.message : 'Message failed to send',
          'Send failed',
        )
      } finally {
        this.sending = false
      }
      return
    }

    // Optimistic append (the WS ack or REST response replaces it).
    const optimistic: ChatMessage = {
      id: -Date.now(),
      conversation_id: this._activeConversation.id,
      sender_id: this.myUserId ?? 0,
      recipient_id: recipientId,
      body,
      price_cents: null,
      media: [],
      unlocked: null,
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

  private _otherAvatar(conversation: Conversation): string {
    return conversation.other.avatar_url || ''
  }

  /** Standalone page chrome: hamburger menu + page header (skipped when
      embedded in the admin tab, whose own navigation takes over). */
  private _chrome() {
    const status =
      this.wsState === 'open'
        ? { cls: 'live', label: 'Live' }
        : this.wsState === 'connecting'
          ? { cls: 'connecting', label: 'Connecting…' }
          : { cls: 'offline', label: 'Offline' }
    return html`
      ${this.embedded
        ? nothing
        : html`<roque-site-menu
            .user="${this.me}"
            @aero-logout="${this._onLogout}"
          ></roque-site-menu>`}
      <div class="header">
        ${this.embedded
          ? nothing
          : html`<span
              class="header-back"
              role="button"
              tabindex="0"
              aria-label="Back"
              @click="${this._goBack}"
              @keydown="${(e: KeyboardEvent) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  this._goBack()
                }
              }}"
              >← Back</span
            >`}
        <span class="brand">Messages</span>
        <span
          class="status ${status.cls}"
          role="status"
          title="Realtime connection state"
          >${status.label}</span
        >
      </div>
    `
  }

  render() {
    if (this.loadingInbox) {
      return html`
        ${this._chrome()}
        <div class="spinner-wrap"><roque-spinner size="36" label="Loading chats…"></roque-spinner></div>
      `
    }
    if (this.error && this.conversations.length === 0) {
      return html`
        ${this._chrome()}
        <roque-card><div class="error-box">${this.error}</div></roque-card>
      `
    }

    const active = this._activeConversation

    return html`
      ${this._chrome()}
      <div class="app">

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
                      <roque-avatar
                        src="${this._otherAvatar(c)}"
                        alt="${this._otherName(c)}"
                        size="40"
                      ></roque-avatar>
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
                    <roque-avatar
                      src="${this._otherAvatar(active)}"
                      alt="${this._otherName(active)}"
                      size="36"
                    ></roque-avatar>
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
                    ${this.messages.map((m) => {
                      const mine = m.id < 0 || m.sender_id === this.myUserId
                      const locked = this._isPaidLocked(m)
                      const img = m.media?.[0]
                      return html`
                        <div class="bubble ${mine ? 'mine' : 'theirs'}">
                          ${img
                            ? locked
                              ? html`<div class="locked-media">
                                  <roque-icon name="lock" size="20"></roque-icon>
                                  <span>${this._price(m)} — one-time unlock</span>
                                </div>`
                              : html`<img
                                  class="media-img"
                                  src="${this._mediaUrl(img.media_url)}"
                                  alt=""
                                  loading="lazy"
                                />`
                            : nothing}
                          ${m.body ? html`<span class="bubble-text">${m.body}</span>` : nothing}
                          ${locked
                            ? html`<div class="unlock-row">
                                <roque-button
                                  buttonId="msg-unlock-${m.id}"
                                  @aero-click="${() => this._unlockMessage(m)}"
                                  >${this.unlocking.has(m.id)
                                    ? 'Opening…'
                                    : `Unlock ${this._price(m)}`}</roque-button
                                >
                              </div>`
                            : nothing}
                          <span class="bubble-time">${this._formatTime(m.created_at)}</span>
                        </div>
                      `
                    })}
                  </div>

                  ${this.msgStatus && !this.msgStatus.can_message
                    ? html`
                        <div class="disabled-panel">
                          <roque-icon name="lock" size="14"></roque-icon>
                          <span>${this.msgStatus.reason}</span>
                        </div>
                      `
                    : html`
                        ${this._composerTools()}
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
            : this.compose
              ? html`
                  <roque-card class="thread">
                    <div class="thread-head">
                      <span class="back-link" @click="${this._backToInbox}">← Inbox</span>
                      <roque-avatar
                        src="${this.compose.avatar}"
                        alt="${this.compose.name}"
                        size="36"
                      ></roque-avatar>
                      <span class="thread-name"
                        >${this.compose.name || 'New message'}</span
                      >
                    </div>

                    <div class="thread-scroll compose-hint">
                      <div class="compose-empty">
                        ${this.msgStatus && !this.msgStatus.can_message
                          ? `${this.compose.name || 'This creator'} isn't taking new messages right now.`
                          : `${this.compose.name || 'This creator'}'s DMs are open — send your first message to start the conversation.`}
                      </div>
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
