import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/feed/subscriber-feed.ts'
import '../components/layouts/card.ts'
import '../components/buttons/button.ts'
import '../components/data/avatar.ts'
import '../components/feedback/spinner.ts'
import { api, ApiError } from '../lib/api'
import type { CreatorLanding } from '../lib/api'

/**
 * Subscriber feed page (`/feed?creator_id={id}`).
 *
 * Thin wrapper around the reusable `roque-subscriber-feed`: resolves the
 * creator id from the URL, shows a compact creator header, and handles the
 * non-follower states (anonymous → login prompt; registered → subscribe
 * prompt). Followers get the feed with infinite scroll and locked-state
 * rendering.
 */
@customElement('roque-subscriber-feed-page')
export class SubscriberFeedPage extends LitElement {
  @state() private creatorId: number | null = null
  @state() private landing: CreatorLanding | null = null
  @state() private loading = true
  @state() private error = ''

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .page {
      max-width: 640px;
      margin: 0 auto;
      padding: 12px;
    }

    .header {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 14px;
    }

    .header-info {
      flex: 1;
      min-width: 0;
    }

    .header-name {
      margin: 0;
      font-size: 17px;
      color: #1e395b;
      font-weight: 600;
    }

    .header-handle {
      margin: 2px 0 0;
      font-size: 12px;
      color: #5a6a7a;
    }

    .prompt {
      text-align: center;
      padding: 22px 16px;
    }

    .prompt-title {
      margin: 0 0 6px;
      font-size: 14px;
      font-weight: 600;
      color: #1e395b;
    }

    .prompt-sub {
      margin: 0 0 14px;
      font-size: 12px;
      color: #5a6a7a;
      line-height: 1.5;
    }

    .error-box {
      padding: 18px;
      text-align: center;
      color: #721c24;
      font-size: 13px;
    }

    .spinner-wrap {
      display: flex;
      justify-content: center;
      padding: 40px 0;
    }
  `

  connectedCallback() {
    super.connectedCallback()
    void this._resolve()
  }

  private async _resolve() {
    const params = new URLSearchParams(window.location.search)
    const raw = params.get('creator_id')
    if (raw && /^\d+$/.test(raw)) {
      this.creatorId = Number(raw)
    } else {
      // /feed/{id} path form.
      const m = window.location.pathname.match(/\/feed\/(\d+)\/?$/)
      if (m) this.creatorId = Number(m[1])
    }
    if (this.creatorId === null) {
      this.error = 'Missing creator id — open /feed?creator_id={id}'
      this.loading = false
      return
    }
    try {
      this.landing = await api.getCreatorLanding(this.creatorId)
    } catch (e) {
      this.error = e instanceof ApiError ? e.message : 'Could not load this creator'
    } finally {
      this.loading = false
    }
  }

  render() {
    if (this.loading) {
      return html`<div class="spinner-wrap"><roque-spinner size="36" label="Loading…"></roque-spinner></div>`
    }
    if (this.error || !this.landing || this.creatorId === null) {
      return html`<roque-card><div class="error-box">${this.error || 'Creator not found'}</div></roque-card>`
    }

    const { profile, viewer } = this.landing
    const displayName = profile.display_name || profile.username || 'Creator'
    const isFollower = viewer.level === 'follower'
    const isAnonymous = viewer.level === 'anonymous'

    return html`
      <div class="page">
        <div class="header">
          <roque-avatar
            src="${profile.avatar_url || ''}"
            alt="${displayName}"
            size="52"
          ></roque-avatar>
          <div class="header-info">
            <h1 class="header-name">${displayName}</h1>
            <p class="header-handle">@${profile.username || ''}</p>
          </div>
        </div>

        ${isFollower
          ? html`<roque-subscriber-feed creator-id="${this.creatorId}"></roque-subscriber-feed>`
          : html`<roque-card>
              <div class="prompt">
                ${isAnonymous
                  ? html`
                      <p class="prompt-title">This feed is for subscribers</p>
                      <p class="prompt-sub">
                        Create an account and subscribe to see the full feed.
                      </p>
                      <roque-button
                        buttonId="feed-login"
                        @aero-click="${() => (window.location.href = '/settings.html')}"
                        >Log in / Sign up</roque-button
                      >
                    `
                  : html`
                      <p class="prompt-title">Subscribe to see the full feed</p>
                      <p class="prompt-sub">
                        You're logged in${viewer.username ? ` as ${viewer.username}` : ''} — this
                        creator's posts are for active followers.
                      </p>
                      <roque-button
                        buttonId="feed-subscribe"
                        @aero-click="${() =>
                          (window.location.href =
                            '/checkout?creator_id=' + this.creatorId)}"
                        >Subscribe</roque-button
                      >
                    `}
              </div>
            </roque-card>`}
      </div>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-subscriber-feed-page': SubscriberFeedPage
  }
}
