import { LitElement, html, css, nothing } from 'lit'
import { customElement, property, state } from 'lit/decorators.js'

import '../feedback/spinner.ts'
import '../feedback/toast.ts'
import '../media/icon.ts'
import '../media/media-viewer.ts'
import '../buttons/button.ts'
import { api, ApiError, getAccessToken } from '../../lib/api'
import type { MediaGallery as MediaGalleryPage } from '../../lib/api'
import type { MediaGalleryItem } from '../../lib/api'

/**
 * Media gallery: a flat grid of a creator's full content (the MEDIA tab).
 *
 * Consumes the paginated flat gallery endpoint (`GET /creators/{id}/media`)
 * — every visible post's media in one stream, newest post first. Each tile
 * renders by its access state, mirroring the feed:
 *
 * - **accessible media** (follower/owner on free + unlocked content) — the
 *   real watermarked image (auth-gated via ``?token=``), clicking opens the
 *   full-screen viewer;
 * - **locked paid broadcast** — a blurred preview plus the one-time price
 *   and a lock badge; clicking **Unlock** runs the same hosted-checkout
 *   unlock flow as the feed;
 * - **non-follower teaser** — every tile is a blurred preview (``teaser``
 *   mode), the gallery note explains what subscribing unlocks.
 *
 * A "Load more" button appends the next page (the flat endpoint is the
 * pagination cursor); no duplicates are ever appended.
 */
@customElement('roque-media-gallery')
export class MediaGallery extends LitElement {
  /** The creator whose content is shown. */
  @property({ type: Number, attribute: 'creator-id' }) creatorId = 0
  /** How many media items per page request. */
  @property({ type: Number, attribute: 'page-size' }) pageSize = 30
  /** True when the MEDIA tab is the active tab (drives first load). */
  @property({ type: Boolean, reflect: true, attribute: 'active' }) active = false

  @state() private items: MediaGalleryItem[] = []
  @state() private teaser = false
  @state() private loading = true
  @state() private loadingMore = false
  @state() private hasMore = false
  @state() private total = 0
  @state() private error = ''
  @state() private unlocking = new Set<number>()
  @state() private toastMessage = ''
  @state() private toastHeading = ''
  /** Full-screen viewer state: urls + index, or null when closed. */
  @state() private viewer: { urls: string[]; index: number } | null = null

  private _page = 0
  private _pagesLoaded = new Set<number>()
  private _loaded = false

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    /* --- Grid --- */
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(104px, 1fr));
      gap: 6px;
    }

    /* One grid cell = one media item (the tile + its optional unlock row). */
    .cell {
      display: flex;
      flex-direction: column;
    }

    .tile {
      position: relative;
      aspect-ratio: 1 / 1;
      border-radius: 4px;
      overflow: hidden;
      border: 1px solid #cdd7e0;
      background: #0e1621;
      cursor: zoom-in;
      transition: box-shadow 0.15s ease, transform 0.15s ease;
    }

    .tile:hover {
      box-shadow: 0 0 8px rgba(60, 127, 177, 0.55);
      transform: translateY(-1px);
    }

    .tile-img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    /* Blurred (locked paid broadcast / non-follower teaser) tile. */
    .tile-img.blurred {
      filter: blur(12px) brightness(0.75);
      transform: scale(1.08);
    }

    /* Lock badge (paid broadcast not yet unlocked). */
    .tile-lock {
      position: absolute;
      right: 4px;
      bottom: 4px;
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 2px 6px;
      font-size: 10px;
      font-weight: 700;
      color: #eef4fa;
      background: rgba(12, 20, 28, 0.78);
      border: 1px solid rgba(255, 255, 255, 0.28);
      border-radius: 3px;
      letter-spacing: 0.2px;
    }

    .tile-lock roque-icon {
      color: #ffd66b;
    }

    /* Unlock CTA shown under a locked tile, inside the same grid cell. */
    .tile-unlock-row {
      margin-top: 6px;
      text-align: center;
    }

    /* --- Teaser note (non-follower) --- */
    .teaser-note {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      margin-bottom: 12px;
      padding: 10px;
      font-size: 12px;
      color: #5a6a7a;
      background: rgba(255, 255, 255, 0.55);
      border: 1px dashed #b8c4d0;
      border-radius: 4px;
    }

    /* --- Footer --- */
    .gallery-footer {
      display: flex;
      justify-content: center;
      padding: 14px 0 4px;
      font-size: 12px;
      color: #7a8794;
    }

    .load-more {
      font-family: inherit;
      font-size: 12px;
      color: #3c7fb1;
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.9),
        rgba(222, 234, 243, 0.75)
      );
      border: 1px solid rgba(90, 130, 165, 0.4);
      border-radius: 3px;
      padding: 6px 16px;
      cursor: pointer;
      transition: box-shadow 0.15s ease, transform 0.15s ease;
    }

    .load-more:hover:not(:disabled) {
      box-shadow: 0 0 5px rgba(60, 127, 177, 0.55);
      transform: translateY(-1px);
    }

    .load-more:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }

    .error-box {
      padding: 16px;
      text-align: center;
      color: #721c24;
      font-size: 13px;
    }

    .empty {
      padding: 22px 10px;
      text-align: center;
      font-size: 12px;
      color: #7a8794;
    }
  `

  connectedCallback() {
    super.connectedCallback()
    if (this.active) void this._load()
  }

  protected updated(changed: Map<string, unknown>) {
    // Lazy first load when the MEDIA tab becomes active.
    if (changed.has('active') && this.active && !this._loaded) void this._load()
    if (changed.has('creatorId') && this.creatorId) {
      // A different creator resets the whole gallery.
      this.items = []
      this._page = 0
      this._pagesLoaded.clear()
      this._loaded = false
      void this._load()
    }
  }

  private async _load() {
    if (!this.creatorId || this._loaded) return
    this._loaded = true
    this.loading = true
    this.error = ''
    this._page = 0
    this._pagesLoaded.clear()
    try {
      const data = await api.getCreatorMedia(this.creatorId, 1, this.pageSize)
      this._applyPage(1, data)
    } catch (e) {
      this.error = e instanceof ApiError ? e.message : 'Could not load the media'
    } finally {
      this.loading = false
    }
  }

  private async _loadMore() {
    if (this.loading || this.loadingMore || !this.hasMore) return
    this.loadingMore = true
    try {
      const next = this._page + 1
      const data = await api.getCreatorMedia(this.creatorId, next, this.pageSize)
      this._applyPage(next, data)
    } catch {
      /* silent — the Load more button stays available for a retry */
    } finally {
      this.loadingMore = false
    }
  }

  private _applyPage(page: number, data: MediaGalleryPage) {
    if (this._pagesLoaded.has(page)) return
    this._pagesLoaded.add(page)
    this._page = page
    this.teaser = data.teaser
    this.total = data.total
    this.hasMore = data.has_more
    const seen = new Set(this.items.map((i) => i.media_id))
    const fresh = data.items.filter((i) => !seen.has(i.media_id))
    this.items = [...this.items, ...fresh]
  }

  private async _refreshPost() {
    try {
      const data = await api.getCreatorMedia(this.creatorId, 1, this.pageSize)
      const freshById = new Map(data.items.map((i) => [i.media_id, i]))
      this.items = this.items.map((i) => freshById.get(i.media_id) ?? i)
      this.teaser = data.teaser
      this.total = data.total
    } catch {
      /* best-effort — the gallery still shows the tile on a later reload */
    }
  }

  private async _unlock(item: MediaGalleryItem) {
    if (this.unlocking.has(item.post_id)) return
    this.unlocking = new Set(this.unlocking).add(item.post_id)
    try {
      const res = await api.unlockBroadcast(item.post_id, {
        success_url: window.location.href,
        cancel_url: window.location.href,
      })
      if (res.checkout_url) {
        // Hosted checkout: pay on the gateway's page; the payment webhook
        // activates the unlock and the gateway returns here where the
        // reloaded gallery shows the tile unlocked.
        window.location.assign(res.checkout_url)
        return
      }
      // Already unlocked (paid on a previous visit): refresh in place.
      this._toast('info', 'Unlocked! Enjoy the full content.', 'Broadcast unlocked')
      await this._refreshPost()
    } catch (e) {
      this._toast(
        'error',
        e instanceof ApiError ? e.message : 'Unlock failed',
        'Payment failed',
      )
    } finally {
      const next = new Set(this.unlocking)
      next.delete(item.post_id)
      this.unlocking = next
    }
  }

  private _toast(_type: 'info' | 'error', message: string, heading: string) {
    this.toastMessage = message
    this.toastHeading = heading
    window.setTimeout(() => (this.toastMessage = ''), 5000)
  }

  private _mediaUrl(url: string): string {
    const token = getAccessToken()
    if (!token) return url
    return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
  }

  /** The clickable url for a tile (real when accessible, preview otherwise). */
  private _tileUrl(item: MediaGalleryItem): string {
    const raw = item.media_url ?? item.preview_url ?? ''
    return item.media_url ? this._mediaUrl(raw) : raw
  }

  private _openViewer(item: MediaGalleryItem) {
    const url = this._tileUrl(item)
    if (!url) return
    this.viewer = { urls: [url], index: 0 }
  }

  private _price(item: MediaGalleryItem): string {
    return `$${((item.broadcast_price_cents ?? 0) / 100).toFixed(2)}`
  }

  private _isLocked(item: MediaGalleryItem): boolean {
    return (
      item.broadcast_price_cents != null &&
      item.unlocked !== true &&
      !this.teaser
    )
  }

  render() {
    if (this.loading) {
      return html`<div class="gallery-footer">
        <roque-spinner size="28" label="Loading media…"></roque-spinner>
      </div>`
    }
    if (this.error && this.items.length === 0) {
      return html`<div class="error-box">${this.error}</div>`
    }
    if (this.items.length === 0) {
      return html`<div class="empty">No media yet — the gallery fills as the creator posts.</div>`
    }

    return html`
      ${this.teaser
        ? html`<div class="teaser-note">
            <roque-icon name="lock" size="12"></roque-icon>
            Preview — subscribe to this creator to see the full gallery.
          </div>`
        : nothing}

      <div class="grid">
        ${this.items.map(
          (item) => html`
            <div class="cell">
              <div class="tile" @click="${() => this._openViewer(item)}">
                <img
                  class="tile-img ${item.media_url ? '' : 'blurred'}"
                  src="${this._tileUrl(item)}"
                  alt="${item.post_caption ?? 'Creator media'}"
                  loading="lazy"
                />
                ${this._isLocked(item)
                  ? html`<span class="tile-lock">
                      <roque-icon name="lock" size="12"></roque-icon>
                      ${this._price(item)}
                    </span>`
                  : nothing}
              </div>
              ${this._isLocked(item)
                ? html`<div class="tile-unlock-row">
                    <roque-button
                      buttonId="gallery-unlock-${item.post_id}"
                      @aero-click="${() => this._unlock(item)}"
                      >${this.unlocking.has(item.post_id)
                        ? 'Unlocking…'
                        : `Unlock ${this._price(item)}`}</roque-button
                    >
                  </div>`
                : nothing}
            </div>
          `,
        )}
      </div>

      <div class="gallery-footer">
        ${this.hasMore
          ? html`<button
              class="load-more"
              ?disabled="${this.loadingMore}"
              @click="${this._loadMore}"
              >${this.loadingMore
                ? 'Loading…'
                : `Load more · ${this.items.length} of ${this.total}`}</button
            >`
          : html`<span
              >${this.total} media item${this.total === 1 ? '' : 's'}</span
            >`}
      </div>

      ${this.toastMessage
        ? html`<roque-toast
            icon="info"
            heading="${this.toastHeading}"
            message="${this.toastMessage}"
            visible
          ></roque-toast>`
        : nothing}
      ${this.viewer
        ? html`<roque-media-viewer
            .urls="${this.viewer.urls}"
            .index="${this.viewer.index}"
            @aero-close="${() => (this.viewer = null)}"
          ></roque-media-viewer>`
        : nothing}
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-media-gallery': MediaGallery
  }
}
