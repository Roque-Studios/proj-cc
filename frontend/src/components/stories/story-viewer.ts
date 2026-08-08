import { LitElement, html, css, nothing } from 'lit'
import { customElement, property, state } from 'lit/decorators.js'

import '../data/avatar.ts'
import { getAccessToken } from '../../lib/api'
import type { Story } from '../../lib/api'

/**
 * Full-screen 24-hour story viewer.
 *
 * Given a creator's live stories, shows them one media item at a time with:
 * - a progress bar per story (the bar for the current story animates across
 *   its duration, then the viewer auto-advances — classic stories UX);
 * - tapping the left/right thirds navigates prev/next, tapping the middle
 *   pauses/resumes (progress bar + auto-advance stop);
 * - Esc / ✕ closes; ArrowLeft/ArrowRight navigate.
 *
 * Media is fetched through the auth-gated `/stories/{id}/media` endpoint, so
 * every url gets the access token appended (`?token=` — the only way `<img>`
 * tags can authenticate).
 *
 * Emits `aero-close` when dismissed so the host can clear its state.
 */
@customElement('roque-story-viewer')
export class StoryViewer extends LitElement {
  /** The creator's live stories, in display order (newest first). */
  @property({ type: Array }) stories: Story[] = []
  /** Which story to start on. */
  @property({ type: Number }) index = 0
  /** Creator display name for the header. */
  @property({ type: String, attribute: 'creator-name' }) creatorName = ''
  /** Creator avatar url for the header. */
  @property({ type: String, attribute: 'creator-avatar' }) creatorAvatar = ''

  /** Seconds each media item stays on screen before auto-advancing. */
  private static readonly STORY_DURATION = 6000

  @state() private current = 0
  @state() private mediaIndex = 0
  @state() private paused = false

  private _timer: number | null = null
  /** Milliseconds remaining for the current media item (pause/resume). */
  private _remaining = StoryViewer.STORY_DURATION
  private _lastTick = 0
  private _keydown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') this._close()
    else if (e.key === 'ArrowLeft') this._step(-1)
    else if (e.key === 'ArrowRight') this._step(1)
    else if (e.key === ' ') {
      e.preventDefault()
      this._togglePause()
    }
  }

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .overlay {
      position: fixed;
      inset: 0;
      z-index: 10000;
      background: #0b0f14;
      display: flex;
      align-items: center;
      justify-content: center;
      animation: story-fade 0.18s ease-out;
      user-select: none;
      -webkit-user-drag: none;
    }

    @keyframes story-fade {
      from {
        opacity: 0;
      }
      to {
        opacity: 1;
      }
    }

    .stage {
      position: relative;
      width: 100%;
      height: 100%;
      max-width: 560px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      background: #0b0f14;
    }

    /* --- Progress bars --- */
    .progress-row {
      display: flex;
      gap: 4px;
      padding: 12px 12px 6px;
    }

    .progress-seg {
      position: relative;
      flex: 1;
      height: 3px;
      border-radius: 2px;
      background: rgba(255, 255, 255, 0.25);
      overflow: hidden;
    }

    .progress-seg.fill .progress-fill {
      width: 100%;
    }

    .progress-seg.current .progress-fill {
      width: 0%;
    }

    .progress-fill {
      position: absolute;
      inset: 0;
      width: 0%;
      background: #fff;
      border-radius: 2px;
    }

    .progress-seg.current .progress-fill.animate {
      animation: story-progress linear ${StoryViewer.STORY_DURATION}ms forwards;
    }

    /* Pausing the overlay pauses the progress animation exactly where it is,
       in sync with the JS timer's frozen remaining time. */
    .overlay.paused .progress-seg.current .progress-fill.animate {
      animation-play-state: paused;
    }

    @keyframes story-progress {
      from {
        width: 0%;
      }
      to {
        width: 100%;
      }
    }

    /* --- Header --- */
    .story-head {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px 12px;
      color: #fff;
    }

    .story-head .creator {
      display: flex;
      align-items: center;
      gap: 8px;
      min-width: 0;
    }

    .story-head .name {
      font-size: 13px;
      font-weight: 600;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .story-head .time {
      font-size: 11px;
      color: rgba(255, 255, 255, 0.65);
      white-space: nowrap;
    }

    .close-btn {
      margin-left: auto;
      width: 34px;
      height: 34px;
      flex-shrink: 0;
      border: 1px solid rgba(255, 255, 255, 0.3);
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.1);
      color: #fff;
      font-size: 18px;
      line-height: 32px;
      text-align: center;
      cursor: pointer;
      transition: background 0.15s ease, transform 0.15s ease;
    }

    .close-btn:hover {
      background: rgba(200, 60, 60, 0.8);
      transform: scale(1.06);
    }

    /* --- Media --- */
    .media-zone {
      position: relative;
      flex: 1;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }

    .media-zone img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
    }

    .tap-zones {
      position: absolute;
      inset: 0;
      display: flex;
    }

    .tap-zone {
      flex: 1;
      cursor: pointer;
    }

    .tap-zone.prev-zone {
      flex: 1.2;
    }

    .tap-zone.next-zone {
      flex: 1.2;
    }

    .tap-zone.pause-zone {
      flex: 0.8;
      cursor: default;
    }

    /* --- Caption --- */
    .caption {
      margin: 0;
      padding: 10px 16px 16px;
      font-size: 13px;
      line-height: 1.5;
      color: #dfe6ee;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .hint {
      position: absolute;
      bottom: 12px;
      right: 14px;
      color: rgba(255, 255, 255, 0.45);
      font-size: 11px;
    }
  `

  connectedCallback() {
    super.connectedCallback()
    window.addEventListener('keydown', this._keydown)
    document.body.style.overflow = 'hidden'
    this._syncIndex()
    this.mediaIndex = 0
    this._startTimer()
  }

  /**
   * Stories/index can arrive after connect (hosts mount the viewer then hand
   * it data) — keep ``current`` in range whenever they change.
   */
  protected updated(changed: Map<string, unknown>) {
    if (changed.has('stories') || changed.has('index')) {
      this._syncIndex()
      if (changed.has('stories') && this._currentStory()) {
        // Fresh data mid-viewing: restart the auto-advance on the new story.
        this.mediaIndex = 0
        this._remaining = StoryViewer.STORY_DURATION
        this._startTimer()
      }
    }
  }

  private _syncIndex() {
    if (this.stories.length === 0) {
      this.current = 0
      return
    }
    this.current = Math.min(Math.max(this.index, 0), this.stories.length - 1)
  }

  disconnectedCallback() {
    super.disconnectedCallback()
    window.removeEventListener('keydown', this._keydown)
    document.body.style.overflow = ''
    this._clearTimer()
  }

  private _mediaCount(story: Story): number {
    return story.media.length
  }

  private _currentStory(): Story | null {
    return this.stories[this.current] ?? null
  }

  private _clearTimer() {
    if (this._timer !== null) {
      // Freeze the remaining time so a pause/resume cycle never loses time.
      this._remaining -= performance.now() - this._lastTick
      window.clearTimeout(this._timer)
      this._timer = null
    }
  }

  private _startTimer() {
    this._clearTimer()
    if (this.paused || this._remaining <= 0) {
      // A fully-elapsed item (pause at the very end) advances immediately.
      if (!this.paused && this._remaining <= 0) this._step(1)
      return
    }
    const story = this._currentStory()
    if (!story) return
    const mediaCount = this._mediaCount(story)
    this._lastTick = performance.now()
    // One interval per media item: when the item's time elapses, the viewer
    // advances to the next item — or the next story, or closes at the end.
    this._timer = window.setTimeout(() => {
      this._timer = null
      if (this.mediaIndex + 1 < mediaCount) {
        this.mediaIndex += 1
        this._remaining = StoryViewer.STORY_DURATION
        this._startTimer()
      } else {
        this._step(1)
      }
    }, this._remaining)
  }

  private _close() {
    this.dispatchEvent(new CustomEvent('aero-close', { bubbles: true, composed: true }))
  }

  private _step(dir: number) {
    if (this.stories.length === 0) return
    const next = this.current + dir
    if (next < 0 || next >= this.stories.length) {
      if (dir > 0) this._close() // reached the end — close
      return
    }
    this.current = next
    this.mediaIndex = 0
    this._remaining = StoryViewer.STORY_DURATION
    this.paused = false
    this._startTimer()
  }

  private _togglePause() {
    this.paused = !this.paused
    if (this.paused) this._clearTimer()
    else this._startTimer()
  }

  private _onTap(e: MouseEvent) {
    const zone = (e.target as HTMLElement).classList
    if (zone.contains('prev-zone')) this._step(-1)
    else if (zone.contains('next-zone')) this._step(1)
    else if (zone.contains('pause-zone')) this._togglePause()
  }

  private _mediaUrl(url: string): string {
    const token = getAccessToken()
    if (!token) return url
    return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`
  }

  render() {
    const story = this._currentStory()
    if (!story) return nothing
    const mediaCount = this._mediaCount(story)
    if (mediaCount === 0) return nothing
    const media = story.media[Math.min(this.mediaIndex, mediaCount - 1)]
    const created = new Date(story.created_at)
    const timeLabel = created.toLocaleTimeString(undefined, {
      hour: 'numeric',
      minute: '2-digit',
    })

    return html`
      <div
        class="overlay ${this.paused ? 'paused' : ''}"
        role="dialog"
        aria-modal="true"
        aria-label="Story viewer"
      >
        <div class="stage">
          <div class="progress-row">
            ${story.media.map(
              (_, i) => html`<div
                class="progress-seg ${i < this.mediaIndex
                  ? 'fill'
                  : i === this.mediaIndex
                    ? 'current'
                    : ''}"
              >
                <div
                  class="progress-fill ${i === this.mediaIndex ? 'animate' : ''}"
                ></div>
              </div>`,
            )}
          </div>

          <div class="story-head">
            <div class="creator">
              <roque-avatar src="${this.creatorAvatar}" alt="${this.creatorName}" size="34"></roque-avatar>
              <span class="name">${this.creatorName}</span>
              <span class="time">${timeLabel}</span>
            </div>
            <button class="close-btn" aria-label="Close story" @click="${this._close}"
              >×</button
            >
          </div>

          <div class="media-zone">
            ${media && media.media_url
              ? html`<img src="${this._mediaUrl(media.media_url)}" alt="Story media" />`
              : nothing}
            <div class="tap-zones" @click="${this._onTap}">
              <div class="tap-zone prev-zone" title="Previous"></div>
              <div class="tap-zone pause-zone" title="Tap to pause"></div>
              <div class="tap-zone next-zone" title="Next"></div>
            </div>
          </div>

          ${story.caption
            ? html`<p class="caption">${story.caption}</p>`
            : html`<div class="hint">← → to navigate · Esc to close</div>`}
        </div>
      </div>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-story-viewer': StoryViewer
  }
}
