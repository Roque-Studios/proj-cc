import { LitElement, html, css, nothing } from 'lit'
import { customElement, property, query, state } from 'lit/decorators.js'

import '../layouts/card.ts'
import '../data/badge.ts'
import '../buttons/button.ts'
import '../media/icon.ts'
import '../feedback/spinner.ts'
import '../feedback/toast.ts'
import { api, ApiError, getAccessToken } from '../../lib/api'
import type { FeedPost, FeedResponse } from '../../lib/api'

/**
 * Subscriber feed view: a creator's posts, newest first, with infinite scroll.
 *
 * Consumes the follower-gated feed endpoint (`GET /creators/{id}/posts`,
 * paginated). Each post renders by its access state:
 *
 * - **regular post / unlocked broadcast** — the full watermarked media, each
 *   `<img>` fetched through the secure content endpoint with the access token
 *   as `?token=` (the only way `<img>` tags can authenticate);
 * - **locked paid broadcast** — a blurred preview of the media (no urls are
 *   ever leaked by the feed) plus the one-time price and an **unlock CTA**
 *   that calls the unlock endpoint and re-renders the post unlocked.
 *
 * When the viewer isn't a follower (anonymous or registered), the feed shows
 * only the teaser posts the endpoint allows (captions + media counts, urls
 * withheld) with a subscribe prompt — the parent page decides where that
 * prompt leads.
 *
 * Infinite scroll: a sentinel at the bottom is observed with an
 * IntersectionObserver; when it enters the viewport the next page is loaded
 * and appended (no duplicates — page keys are tracked).
 */
@customElement('roque-subscriber-feed')
export class SubscriberFeed extends LitElement {
  /** The creator whose feed is shown. */
  @property({ type: Number, attribute: 'creator-id' }) creatorId = 0
  /** How many posts per page request. */
  @property({ type: Number, attribute: 'page-size' }) pageSize = 10

  @state() private posts: FeedPost[] = []
  @state() private teaser = false
  @state() private loading = true
  @state() private loadingMore = false
  @state() private hasMore = false
  @state() private total = 0
  @state() private error = ''
  @state() private unlocking = new Set<number>()
  @state() private toastMessage = ''
  @state() private toastHeading = ''
  @state() private toastType: 'info' | 'error' = 'info'

  private _page = 0
  private _pagesLoaded = new Set<number>()
  private _observer: IntersectionObserver | null = null
  private _observedSentinel: Element | null = null
  private _retryCooldownUntil = 0

  @query('.feed-sentinel') private _sentinelEl!: HTMLElement | null

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .feed {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .post-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 10px;
    }

    .post-date {
      font-size: 11px;
      color: #6b7a8a;
    }

    .post-caption {
      margin: 0 0 10px;
      font-size: 13px;
      color: #222;
      line-height: 1.55;
      white-space: pre-wrap;
    }

    /* --- Media --- */
    .media-stack {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .media-item {
      position: relative;
      border-radius: 5px;
      overflow: hidden;
      border: 1px solid #d0d0d0;
      background: #0e1621;
    }

    .media-img {
      display: block;
      width: 100%;
      height: auto;
      max-height: 480px;
      object-fit: cover;
    }

    /* Locked preview: blurred media (metadata only, never the real url). */
    .locked-preview {
      position: relative;
      height: 220px;
      display: flex;
      align-items: center;
      justify-content: center;
      background:
        radial-gradient(circle at 30% 20%, rgba(173, 216, 230, 0.35), transparent 60%),
        linear-gradient(160deg, #33404f 0%, #1b2633 100%);
      overflow: hidden;
    }

    .locked-preview::before {
      content: '';
      position: absolute;
      inset: 0;
      background: repeating-linear-gradient(
        45deg,
        rgba(255, 255, 255, 0.05) 0 12px,
        rgba(0, 0, 0, 0.08) 12px 24px
      );
    }

    .lock-badge {
      position: relative;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      padding: 18px 26px;
      background: rgba(15, 23, 32, 0.82);
      border: 1px solid rgba(255, 255, 255, 0.22);
      border-radius: 8px;
      color: #e8eef5;
      box-shadow: 0 6px 18px rgba(0, 0, 0, 0.45);
      backdrop-filter: blur(2px);
    }

    .lock-price {
      font-size: 15px;
      font-weight: 700;
      letter-spacing: 0.3px;
    }

    .lock-hint {
      font-size: 11px;
      color: #aeb9c6;
    }

    /* --- Unlock CTA --- */
    .unlock-row {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      margin-top: 12px;
    }

    .unlocked-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      color: #1e4b21;
      background: #d4edd6;
      border: 1px solid #92cf94;
      border-radius: 3px;
      padding: 2px 8px;
    }

    /* --- Non-follower teaser --- */
    .teaser-note {
      font-size: 12px;
      color: #5a6a7a;
      padding: 10px;
      text-align: center;
      background: rgba(255, 255, 255, 0.55);
      border: 1px dashed #b8c4d0;
      border-radius: 4px;
      margin-bottom: 12px;
    }

    /* --- Feed footer --- */
    .feed-footer {
      display: flex;
      justify-content: center;
      padding: 18px 0 8px;
      font-size: 12px;
      color: #7a8794;
    }

    .error-box {
      padding: 16px;
      text-align: center;
      color: #721c24;
      font-size: 13px;
    }
  `

  connectedCallback() {
    super.connectedCallback()
    this._observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) this._loadMore()
        }
      },
      { rootMargin: '240px 0px' },
    )
    void this._load()
  }

  disconnectedCallback() {
    super.disconnectedCallback()
    this._observer?.disconnect()
    this._observer = null
  }

  protected updated() {
    // The sentinel is re-rendered on every page append — re-assert observation
    // so the IntersectionObserver keeps tracking the live element.
    const el = this._sentinelEl
    if (el && this._observer) {
      if (this._observedSentinel !== el) {
        this._observer.disconnect()
        this._observer.observe(el)
        this._observedSentinel = el
      }
    }
  }

  private async _load() {
    if (!this.creatorId) return
    this.loading = true
    this.error = ''
    this._page = 0
    this._pagesLoaded.clear()
    try {
      const data = await api.getCreatorFeed(this.creatorId, 1, this.pageSize)
      this._applyPage(1, data)
    } catch (e) {
      this.error = e instanceof ApiError ? e.message : 'Could not load the feed'
    } finally {
      this.loading = false
    }
  }

  private async _loadMore() {
    if (
      this.loading ||
      this.loadingMore ||
      !this.hasMore ||
      this.creatorId === 0
    ) {
      return
    }
    // Backoff on failure: a persistent outage would otherwise keep the
    // sentinel in view and re-fire this in a tight loop against the API.
    if (Date.now() < this._retryCooldownUntil) return
    this.loadingMore = true
    try {
      const next = this._page + 1
      const data = await api.getCreatorFeed(this.creatorId, next, this.pageSize)
      this._applyPage(next, data)
      this._retryCooldownUntil = 0
    } catch {
      // A failed background page load is silent — the sentinel stays in view
      // and a later scroll triggers another attempt (after the cooldown).
      this._retryCooldownUntil = Date.now() + 3000
    } finally {
      this.loadingMore = false
    }
  }

  private _applyPage(page: number, data: FeedResponse) {
    if (this._pagesLoaded.has(page)) return
    this._pagesLoaded.add(page)
    this._page = page
    this.teaser = data.teaser
    this.total = data.total
    this.hasMore = data.has_more
    const seen = new Set(this.posts.map((p) => p.id))
    const fresh = data.posts.filter((p) => !seen.has(p.id))
    this.posts = [...this.posts, ...fresh]
  }

  /** Replace one post with its fresh feed shape after an unlock (no refetch). */
  private _replacePost(postId: number, fresh: FeedPost) {
    this.posts = this.posts.map((p) => (p.id === postId ? fresh : p))
  }

  private async _unlock(post: FeedPost) {
    if (this.unlocking.has(post.id)) return
    this.unlocking = new Set(this.unlocking).add(post.id)
    try {
      await api.unlockBroadcast(post.id)
      this._toast('info', 'Unlocked! Enjoy the full broadcast.', 'Broadcast unlocked')
      // The unlock response carries no media urls; refetch the feed's first
      // page (newest posts) and swap in the fresh post object wholesale — it
      // already carries every secure media url, so multi-media broadcasts
      // render fully (no partial patches).
      await this._reloadPost(post.id)
    } catch (e) {
      this._toast(
        'error',
        e instanceof ApiError ? e.message : 'Unlock failed',
        'Payment failed',
      )
    } finally {
      const next = new Set(this.unlocking)
      next.delete(post.id)
      this.unlocking = next
    }
  }

  private async _reloadPost(postId: number) {
    // Fetch the feed's first page and replace the matching post in place so
    // scroll position is untouched. The fresh object is authoritative: it
    // carries every media url (and the unlocked flag) for the whole post.
    try {
      const data = await api.getCreatorFeed(this.creatorId, 1, this.pageSize)
      const match = data.posts.find((p) => p.id === postId)
      if (match) this._replacePost(postId, match)
    } catch {
      /* best-effort refresh — the feed still shows the post on a later reload */
    }
  }

  private _toast(type: 'info' | 'error', message: string, heading: string) {
    this.toastType = type
    this.toastMessage = message
    this.toastHeading = heading
    window.setTimeout(() => (this.toastMessage = ''), 5000)
  }

  private _mediaUrl(url: string): string {
    const token = getAccessToken()
    if (!token) return url
    return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
  }

  private _formatDate(iso: string): string {
    try {
      return new Date(iso).toLocaleDateString(undefined, {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
      })
    } catch {
      return ''
    }
  }

  private _price(post: FeedPost): string {
    return `$${((post.broadcast_price_cents ?? 0) / 100).toFixed(2)}`
  }

  private _renderMedia(post: FeedPost) {
    const isLocked = post.broadcast_price_cents != null && post.unlocked !== true

    // Locked broadcast: metadata only — the feed never sends urls for locked
    // content, so the preview is a styled lock panel (nothing to blur).
    if (isLocked) {
      return html`
        <div class="media-stack">
          <div class="locked-preview">
            <div class="lock-badge">
              <roque-icon name="lock" size="26"></roque-icon>
              <span class="lock-price">${this._price(post)}</span>
              <span class="lock-hint">One-time unlock for full access</span>
            </div>
          </div>
          <div class="unlock-row">
            <roque-button
              buttonId="unlock-${post.id}"
              @aero-click="${() => this._unlock(post)}"
              >${this.unlocking.has(post.id) ? 'Unlocking…' : 'Unlock'}</roque-button
            >
          </div>
        </div>
      `
    }

    // Unlocked / regular: render the full watermarked media via the secure
    // content endpoint.
    return html`
      <div class="media-stack">
        ${post.media.map(
          (m) =>
            m.media_url
              ? html`<div class="media-item">
                  <img
                    class="media-img"
                    src="${this._mediaUrl(m.media_url)}"
                    alt="Post media"
                    loading="lazy"
                  />
                </div>`
              : nothing,
        )}
        ${post.broadcast_price_cents != null && post.unlocked === true
          ? html`<div class="unlock-row">
              <span class="unlocked-badge">✓ Unlocked</span>
            </div>`
          : nothing}
      </div>
    `
  }

  render() {
    if (this.loading) {
      return html`<div class="feed-footer">
        <roque-spinner size="28" label="Loading feed…"></roque-spinner>
      </div>`
    }
    if (this.error && this.posts.length === 0) {
      return html`<div class="error-box">${this.error}</div>`
    }

    return html`
      <div class="feed">
        ${this.teaser && this.posts.length > 0
          ? html`<div class="teaser-note">
              <roque-icon name="lock" size="12"></roque-icon>
              Preview — subscribe to this creator to see the full feed.
            </div>`
          : nothing}

        ${this.posts.map(
          (post) => html`
            <roque-card>
              <div class="post-head">
                ${post.broadcast_price_cents != null
                  ? html`<roque-badge context="${post.unlocked ? 'success' : 'warning'}"
                      >${post.unlocked ? 'unlocked' : 'paid'}</roque-badge
                    >`
                  : nothing}
                <span class="post-date">${this._formatDate(post.created_at)}</span>
              </div>
              ${post.caption
                ? html`<p class="post-caption">${post.caption}</p>`
                : nothing}
              ${this._renderMedia(post)}
            </roque-card>
          `,
        )}

        <div class="feed-footer feed-sentinel">
          ${this.loadingMore
            ? html`<roque-spinner size="20" label="Loading more…"></roque-spinner>`
            : this.hasMore
              ? nothing
              : this.posts.length > 0
                ? html`<span>End of feed · ${this.total} post${this.total === 1 ? '' : 's'}</span>`
                : html`<span>No posts yet.</span>`}
        </div>
      </div>

      ${this.toastMessage
        ? html`<roque-toast
            icon="${this.toastType === 'error' ? 'info' : 'info'}"
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
    'roque-subscriber-feed': SubscriberFeed
  }
}
