import { LitElement, html, css, nothing } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/data/avatar.ts'
import '../components/data/badge.ts'
import '../components/layouts/card.ts'
import '../components/buttons/button.ts'
import '../components/media/icon.ts'
import '../components/feedback/spinner.ts'
import '../components/feed/subscriber-feed.ts'
import { api, ApiError } from '../lib/api'
import type { CreatorLanding } from '../lib/api'

/**
 * Public creator landing page (`/creator/{id}` or `/creator/?creator_id={id}`).
 *
 * Mobile-first, built from the roque-* components. The payload is
 * role-shaped by the backend's `GET /creators/{id}/landing`:
 *
 * - **anonymous** — the profile + social accounts + a "log in to subscribe"
 *   prompt (subscribing needs an account);
 * - **registered (non-follower)** — the same, plus account context ("logged in
 *   as ...") and a subscribe button that opens the hosted checkout;
 * - **follower** — the profile plus the full feed (posts with watermarked
 *   thumbnails; paid broadcasts show a locked badge until unlocked).
 */
@customElement('roque-creator-landing')
export class CreatorLandingPage extends LitElement {
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

    .page-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 12px;
    }

    .brand {
      font-size: 15px;
      font-weight: 600;
      color: #1e395b;
      text-shadow: 0 0 6px rgba(255, 255, 255, 0.9);
    }

    /* --- Profile header --- */
    .profile-card {
      display: flex;
      gap: 14px;
      align-items: flex-start;
    }

    .profile-info {
      flex: 1;
      min-width: 0;
    }

    .profile-name {
      margin: 0;
      font-size: 20px;
      color: #1e395b;
      font-weight: 600;
      line-height: 1.25;
    }

    .profile-handle {
      margin: 2px 0 0;
      font-size: 12px;
      color: #5a6a7a;
    }

    .profile-bio {
      margin: 10px 0 0;
      font-size: 13px;
      line-height: 1.55;
      color: #333;
      white-space: pre-wrap; /* line breaks render from \n; text stays escaped */
    }

    .social-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
    }

    .social-chip {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 5px 10px;
      font-size: 12px;
      color: #1e395b;
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.75),
        rgba(173, 216, 230, 0.35)
      );
      border: 1px solid rgba(0, 0, 0, 0.2);
      border-radius: 999px;
      text-decoration: none;
      transition: box-shadow 0.2s ease, transform 0.2s ease;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);
    }

    .social-chip:hover {
      box-shadow: 0 0 6px rgba(0, 162, 232, 0.55);
      transform: translateY(-1px);
    }

    .social-chip roque-icon {
      color: inherit;
    }

    /* --- Subscribe CTA --- */
    .cta {
      margin-top: 14px;
      padding: 14px;
      text-align: center;
    }

    .cta-title {
      margin: 0 0 4px;
      font-size: 14px;
      font-weight: 600;
      color: #1e395b;
    }

    .cta-sub {
      margin: 0 0 12px;
      font-size: 12px;
      color: #5a6a7a;
    }

    .cta-account {
      display: inline-block;
      margin-bottom: 10px;
      font-size: 12px;
      color: #0c5460;
      background: #d1ecf1;
      border: 1px solid #bee5eb;
      border-radius: 3px;
      padding: 4px 8px;
    }

    .gateway-row {
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      gap: 6px;
      margin-top: 10px;
    }

    .gateway-chip {
      font-size: 11px;
      color: #555;
      background: #f0f0f0;
      border: 1px solid #ccc;
      border-radius: 3px;
      padding: 2px 8px;
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

  async connectedCallback() {
    super.connectedCallback()
    await this._resolveCreatorId()
  }

  private async _resolveCreatorId() {
    const params = new URLSearchParams(window.location.search)
    const raw = params.get('creator_id')
    if (raw && /^\d+$/.test(raw)) {
      this.creatorId = Number(raw)
    } else {
      // /creator/{id} path form (nginx maps it to landing.html).
      const m = window.location.pathname.match(/\/creator\/(\d+)\/?$/)
      if (m) this.creatorId = Number(m[1])
    }
    if (this.creatorId === null) {
      this.error = 'Missing creator id — open /creator/{id}'
      this.loading = false
      return
    }
    await this._load()
  }

  private async _load() {
    if (this.creatorId === null) return
    try {
      this.landing = await api.getCreatorLanding(this.creatorId)
    } catch (e) {
      this.error =
        e instanceof ApiError ? e.message : 'Could not load this creator'
    } finally {
      this.loading = false
    }
  }

  private _socialIcon(platform: string): string {
    switch (platform) {
      case 'twitter':
      case 'x':
        return 'x'
      case 'instagram':
        return 'instagram'
      case 'tiktok':
        return 'tiktok'
      default:
        return 'link'
    }
  }

  private _socialHref(value: string): string {
    // Only ever navigate to http(s) urls — anything else (javascript:, data:,
    // vbscript:) is never reachable, so a stored social link can't be an XSS
    // vector even if the backend accepted an odd value.
    if (/^https?:\/\//i.test(value)) return value
    if (value.startsWith('@')) {
      const handle = value.slice(1)
      // Best-effort profile url per platform; unknown -> search.
      const host = window.location.host
      return `https://${host}/search?q=${encodeURIComponent(handle)}`
    }
    return `https://${value}`
  }

  render() {
    if (this.loading) {
      return html`<div class="spinner-wrap"><roque-spinner size="36" label="Loading…"></roque-spinner></div>`
    }
    if (this.error || !this.landing) {
      return html`<roque-card><div class="error-box">${this.error || 'Creator not found'}</div></roque-card>`
    }

    const { profile, viewer, gateways } = this.landing
    const displayName = profile.display_name || profile.username || 'Creator'
    const isAnonymous = viewer.level === 'anonymous'
    const isFollower = viewer.level === 'follower'

    return html`
      <div class="page">
        <div class="page-header">
          <span class="brand">Creator Landing</span>
        </div>

        <roque-card>
          <div class="profile-card">
            <roque-avatar
              src="${profile.avatar_url || ''}"
              alt="${displayName}"
              size="72"
            ></roque-avatar>
            <div class="profile-info">
              <h1 class="profile-name">${displayName}</h1>
              <p class="profile-handle">@${profile.username || ''}</p>
              ${profile.bio
                ? html`<p class="profile-bio">${profile.bio}</p>`
                : nothing}
            </div>
          </div>

          ${this.landing.social_links.length
            ? html`<div class="social-row">
                ${this.landing.social_links.map(
                  (s) => html`<a
                    class="social-chip"
                    href="${this._socialHref(s.value)}"
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label="${s.label}"
                  >
                    <roque-icon name="${this._socialIcon(s.platform)}" size="14"></roque-icon>
                    <span>${s.label}</span>
                  </a>`,
                )}
              </div>`
            : nothing}
        </roque-card>

        <roque-card class="cta">              ${isAnonymous
            ? html`
                <p class="cta-title">Subscribe to ${displayName}</p>
                <p class="cta-sub">Exclusive posts for followers. Log in to subscribe.</p>
                <roque-button
                  buttonId="landing-login"
                  @aero-click="${() => (window.location.href = '/settings.html')}"
                  >Log in to subscribe</roque-button
                >
              `
            : isFollower
              ? html`
                  <p class="cta-title">You're a subscriber 🎉</p>
                  <p class="cta-sub">
                    Welcome back${viewer.username ? `, ${viewer.username}` : ''} — full feed below.
                  </p>
                `
              : html`
                  <p class="cta-title">Subscribe to ${displayName}</p>
                  <p class="cta-sub">Unlock the full feed for one monthly price.</p>
                  ${viewer.username
                    ? html`<span class="cta-account">Logged in as ${viewer.username}</span>`
                    : nothing}
                  <div>
                    <roque-button
                      buttonId="landing-subscribe"
                      @aero-click="${() =>
                        (window.location.href =
                          '/checkout?creator_id=' + this.creatorId)}"
                      >Subscribe</roque-button
                    >
                  </div>
                  ${gateways.length
                    ? html`<div class="gateway-row">
                        ${gateways.map(
                          (g) => html`<span class="gateway-chip">Pay with ${g.label}</span>`,
                        )}
                      </div>`
                    : nothing}
                `}
        </roque-card>

        ${isFollower
          ? html`<roque-subscriber-feed
              creator-id="${this.creatorId}"
            ></roque-subscriber-feed>`
          : nothing}
      </div>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-creator-landing': CreatorLandingPage
  }
}
