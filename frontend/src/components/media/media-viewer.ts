import { LitElement, html, css, nothing } from 'lit'
import { customElement, property, state } from 'lit/decorators.js'

/**
 * Full-screen media viewer (lightbox) for post media and avatars.
 *
 * Shows one url at a time with a dark overlay, optional prev/next navigation
 * when a list of urls is provided, a counter ("2 / 5"), click-outside /
 * Esc / ✕ to close, and Arrow keys to navigate. Supports zoom-in on image
 * click (and pinch-free tap for touch) — the image fills the viewport with
 * `object-fit: contain`.
 *
 * Emits `aero-close` when dismissed so the host can clear its state.
 */
@customElement('roque-media-viewer')
export class RoqueMediaViewer extends LitElement {
  /** Urls to show. When more than one, prev/next arrows + a counter render. */
  @property({ type: Array }) urls: string[] = []
  /** Initial index into `urls`. */
  @property({ type: Number }) index = 0

  @state() private current = 0
  /** Toggle image zoom (scale 1 -> 2). */
  @state() private zoomed = false

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .overlay {
      position: fixed;
      inset: 0;
      z-index: 10000;
      background: rgba(0, 0, 0, 0.92);
      display: flex;
      align-items: center;
      justify-content: center;
      animation: media-fade 0.18s ease-out;
    }

    @keyframes media-fade {
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
      display: flex;
      align-items: center;
      justify-content: center;
    }

    img {
      max-width: 94vw;
      max-height: 92vh;
      object-fit: contain;
      border-radius: 3px;
      box-shadow: 0 8px 40px rgba(0, 0, 0, 0.6);
      cursor: zoom-in;
      transition: transform 0.22s cubic-bezier(0.2, 0.8, 0.3, 1);
      user-select: none;
      -webkit-user-drag: none;
    }

    img.zoomed {
      transform: scale(2);
      cursor: zoom-out;
    }

    .close-btn {
      position: absolute;
      top: 14px;
      right: 14px;
      width: 40px;
      height: 40px;
      border: 1px solid rgba(255, 255, 255, 0.35);
      border-radius: 50%;
      background: rgba(0, 0, 0, 0.45);
      color: var(--cc-client);
      font-size: 22px;
      line-height: 38px;
      text-align: center;
      cursor: pointer;
      transition: background 0.15s ease, transform 0.15s ease;
    }

    .close-btn:hover {
      background: rgba(180, 40, 40, 0.85);
      transform: scale(1.08);
    }

    .nav-btn {
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      width: 46px;
      height: 46px;
      border: 1px solid rgba(255, 255, 255, 0.3);
      border-radius: 50%;
      background: rgba(0, 0, 0, 0.4);
      color: var(--cc-client);
      font-size: 24px;
      line-height: 44px;
      text-align: center;
      cursor: pointer;
      transition: background 0.15s ease, opacity 0.15s ease;
      user-select: none;
    }

    .nav-btn:hover {
      background: rgba(255, 255, 255, 0.25);
    }

    .nav-btn.prev {
      left: 12px;
    }

    .nav-btn.next {
      right: 12px;
    }

    .nav-btn.hidden {
      opacity: 0;
      pointer-events: none;
    }

    .counter {
      position: absolute;
      bottom: 18px;
      left: 50%;
      transform: translateX(-50%);
      padding: 4px 12px;
      border-radius: 999px;
      background: rgba(0, 0, 0, 0.55);
      color: #e6edf4;
      font-size: 12px;
      letter-spacing: 0.4px;
    }

    .hint {
      position: absolute;
      bottom: 18px;
      right: 18px;
      color: rgba(255, 255, 255, 0.5);
      font-size: 11px;
    }
  `

  connectedCallback() {
    super.connectedCallback()
    this.current = Math.min(Math.max(this.index, 0), this.urls.length - 1)
    window.addEventListener('keydown', this._onKeydown)
    document.body.style.overflow = 'hidden'
  }

  disconnectedCallback() {
    super.disconnectedCallback()
    window.removeEventListener('keydown', this._onKeydown)
    document.body.style.overflow = ''
  }

  private _onKeydown = (e: KeyboardEvent) => {
    if (e.key === 'Escape') this._close()
    else if (e.key === 'ArrowLeft') this._prev()
    else if (e.key === 'ArrowRight') this._next()
  }

  private _close() {
    this.dispatchEvent(new CustomEvent('aero-close', { bubbles: true, composed: true }))
  }

  private _prev() {
    if (this.urls.length < 2) return
    this.zoomed = false
    this.current = (this.current - 1 + this.urls.length) % this.urls.length
  }

  private _next() {
    if (this.urls.length < 2) return
    this.zoomed = false
    this.current = (this.current + 1) % this.urls.length
  }

  private _toggleZoom() {
    this.zoomed = !this.zoomed
  }

  render() {
    if (this.urls.length === 0 || this.current < 0) return nothing
    const url = this.urls[this.current]
    const multi = this.urls.length > 1

    return html`
      <div class="overlay" role="dialog" aria-modal="true" aria-label="Media viewer">
        <div class="stage" @click="${(e: MouseEvent) => {
          if ((e.target as HTMLElement).classList.contains('stage')) this._close()
        }}">
          <img
            src="${url}"
            alt="Full-screen media"
            class="${this.zoomed ? 'zoomed' : ''}"
            @click="${this._toggleZoom}"
          />
          ${multi
            ? html`
                <button
                  class="nav-btn prev ${this.urls.length < 2 ? 'hidden' : ''}"
                  aria-label="Previous"
                  @click="${(e: MouseEvent) => {
                    e.stopPropagation()
                    this._prev()
                  }}"
                  >‹</button
                >
                <button
                  class="nav-btn next ${this.urls.length < 2 ? 'hidden' : ''}"
                  aria-label="Next"
                  @click="${(e: MouseEvent) => {
                    e.stopPropagation()
                    this._next()
                  }}"
                  >›</button
                >
              `
            : nothing}
        </div>
        <button class="close-btn" aria-label="Close" @click="${this._close}">×</button>
        ${multi ? html`<div class="counter">${this.current + 1} / ${this.urls.length}</div>` : nothing}
        <div class="hint">Click to zoom · Esc to close</div>
      </div>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-media-viewer': RoqueMediaViewer
  }
}
