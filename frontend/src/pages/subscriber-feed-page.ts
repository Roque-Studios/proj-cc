import { LitElement, html, css, nothing } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/feed/subscriber-feed.ts'
import '../components/layouts/card.ts'
import '../components/buttons/button.ts'
import '../components/data/avatar.ts'
import '../components/media/icon.ts'
import '../components/media/media-viewer.ts'
import '../components/navigation/site-menu.ts'
import '../components/feedback/spinner.ts'
import { api, ApiError, clearTokens, getAccessToken } from '../lib/api'
import type { CreatorLanding, SocialLink, UserMe } from '../lib/api'

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
  /** The signed-in user for the hamburger menu (null = anonymous). */
  @state() private me: UserMe | null = null
  /** Full-screen viewer state: urls + index, or null when closed. */
  @state() private viewer: { urls: string[]; index: number } | null = null

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

    /* --- Creator hero (mirrors the landing page: banner, avatar, name,
       post count, bio, social links) --- */
    .hero {
      margin-bottom: 14px;
    }

    .hero-banner {
      position: relative;
      height: 150px;
      border: 1px solid rgba(0, 0, 0, 0.35);
      border-bottom: none;
      border-radius: 5px 5px 0 0;
      overflow: hidden;
      background:
        radial-gradient(circle at 20% 20%, rgba(173, 216, 230, 0.55), transparent 55%),
        radial-gradient(circle at 80% 60%, rgba(120, 160, 200, 0.4), transparent 50%),
        linear-gradient(135deg, #2c3e50 0%, #1e395b 55%, #14212f 100%);
    }

    .hero-banner-img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .hero-body {
      display: flex;
      flex-wrap: wrap;
      align-items: flex-start;
      gap: 12px 14px;
    }

    .hero-avatar {
      position: relative;
      z-index: 2;
      margin-top: -44px;
      cursor: zoom-in;
      transition: transform 0.2s ease;
    }

    .hero-avatar:hover {
      transform: scale(1.04);
    }

    .hero-info {
      flex: 1 1 260px;
      min-width: 0;
      padding-top: 8px;
    }

    .name-row {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .hero-name {
      margin: 0;
      font-size: 20px;
      color: #1e395b;
      font-weight: 600;
      line-height: 1.25;
    }

    /* Online status indicator (static green dot, creator-platform style). */
    .online-dot {
      flex-shrink: 0;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #35c759;
      border: 2px solid #fff;
      box-shadow: 0 0 0 0 rgba(53, 199, 89, 0.55);
      animation: online-pulse 2.2s ease-out infinite;
    }

    @keyframes online-pulse {
      0% {
        box-shadow: 0 0 0 0 rgba(53, 199, 89, 0.5);
      }
      70% {
        box-shadow: 0 0 0 7px rgba(53, 199, 89, 0);
      }
      100% {
        box-shadow: 0 0 0 0 rgba(53, 199, 89, 0);
      }
    }

    .hero-handle {
      margin: 3px 0 0;
      font-size: 12px;
      color: #5a6a7a;
    }

    .hero-posts {
      display: inline-block;
      margin-left: 8px;
      padding: 1px 8px;
      font-size: 11px;
      color: #1e395b;
      background: rgba(173, 216, 230, 0.35);
      border: 1px solid rgba(90, 130, 165, 0.4);
      border-radius: 999px;
    }

    .hero-bio {
      margin: 10px 0 0;
      font-size: 13px;
      line-height: 1.55;
      color: #333;
      white-space: pre-wrap;
    }

    .social-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 12px;
      padding: 0 14px 14px;
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
    void this._loadSession()
    void this._resolve()
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
    this.me = null
    window.location.href = '/'
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

  private _socialHref(platform: string, value: string): string {
    // Only ever navigate to http(s) urls — anything else (javascript:, data:,
    // vbscript:) is never reachable, so a stored social link can't be an XSS
    // vector even if the backend accepted an odd value.
    if (/^https?:\/\//i.test(value)) return value
    // Bare handles are turned into real profile urls per platform.
    const handle = value.replace(/^@+/, '')
    switch (platform) {
      case 'twitter':
      case 'x':
        return handle ? `https://x.com/${handle}` : value
      case 'instagram':
        return handle ? `https://www.instagram.com/${handle}` : value
      case 'tiktok':
        return handle ? `https://www.tiktok.com/@${handle}` : value
      default:
        return handle ? `https://${handle}` : value
    }
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
      // No creator id in the URL (e.g. the profile page's "Back to feed"
      // button) — fall back to the first/seed creator, the same site-root
      // default the home page uses, so /feed always has a creator to show.
      try {
        const landing = await api.getDefaultLanding()
        this.creatorId = landing.profile.id
      } catch {
        this.error = 'No creator is configured yet — nothing to show here.'
        this.loading = false
        return
      }
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
    const postCount = profile.post_count ?? 0
    const bannerUrl = profile.banner_url || ''

    return html`
      <roque-site-menu .user="${this.me}" @aero-logout="${this._onLogout}"></roque-site-menu>
      <div class="page">
        <!-- Creator hero: banner, avatar, name + online dot, post count, bio, social links -->
        <div class="hero">
          <div class="hero-banner">
            ${bannerUrl
              ? html`<img class="hero-banner-img" src="${bannerUrl}" alt="" />`
              : nothing}
          </div>

          <roque-card>
            <div class="hero-body">
              <div
                class="hero-avatar"
                title="View full size"
                @click="${() =>
                  profile.avatar_url
                    ? (this.viewer = { urls: [profile.avatar_url], index: 0 })
                    : null}"
              >
                <roque-avatar
                  src="${profile.avatar_url || ''}"
                  alt="${displayName}"
                  size="84"
                ></roque-avatar>
              </div>

              <div class="hero-info">
                <div class="name-row">
                  <h1 class="hero-name">${displayName}</h1>
                  <span class="online-dot" title="Online"></span>
                </div>
                <p class="hero-handle">
                  @${profile.username || ''}
                  <span class="hero-posts">
                    ${postCount} post${postCount === 1 ? '' : 's'}
                  </span>
                </p>
                ${profile.bio
                  ? html`<p class="hero-bio">${profile.bio}</p>`
                  : nothing}
              </div>
            </div>

            ${this.landing.social_links.length
              ? html`<div class="social-row">
                  ${this.landing.social_links.map(
                    (s: SocialLink) => html`<a
                      class="social-chip"
                      href="${this._socialHref(s.platform, s.value)}"
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
                        @aero-click="${() =>
                          (window.location.href =
                            '/login?next=' +
                            encodeURIComponent(
                              window.location.pathname + window.location.search,
                            ))}"
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
    'roque-subscriber-feed-page': SubscriberFeedPage
  }
}
