import { LitElement, html, css, nothing } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/data/avatar.ts'
import '../components/data/badge.ts'
import '../components/layouts/card.ts'
import '../components/buttons/button.ts'
import '../components/media/icon.ts'
import '../components/media/media-viewer.ts'
import '../components/feedback/spinner.ts'
import '../components/feed/subscriber-feed.ts'
import { api, ApiError } from '../lib/api'
import type { CreatorLanding } from '../lib/api'

/**
 * Public creator landing page — the site root (`/`) and `/creator/{id}`
 * (or `/creator/?creator_id={id}`).
 *
 * Mobile-first, built from the roque-* components. The payload is
 * role-shaped by the backend's `GET /creators/{id}/landing`:
 *
 * - a **hero** — banner image (uploaded in the admin, gradient fallback),
 *   avatar, display name with a green online dot, visible post count, bio,
 *   social accounts, and a role-based CTA: anonymous visitors get a
 *   **"Join free"** button (creates a free account, then subscribe),
 *   registered non-followers a **Subscribe** button opening the hosted
 *   checkout, followers a subscriber welcome;
 * - a **posts grid for every visitor** — followers see the full feed
 *   (watermarked thumbnails), everyone else sees the same posts with
 *   **blurred previews** (server-rendered `PREVIEW` transforms, real bytes
 *   never exposed).
 */
@customElement('roque-creator-landing')
export class CreatorLandingPage extends LitElement {
  @state() private creatorId: number | null = null
  @state() private landing: CreatorLanding | null = null
  @state() private loading = true
  @state() private error = ''
  @state() private missingCreatorId = false
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

    /* --- Hero --- */
    .hero {
      margin-bottom: 14px;
    }

    .hero-banner {
      position: relative;
      height: 160px;
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
      white-space: pre-wrap; /* line breaks render from \n; text stays escaped */
    }

    .hero-cta {
      flex: 1 1 100%;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
      text-align: center;
      padding-top: 10px;
      border-top: 1px dashed #c8d4de;
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
    .cta-sub {
      margin: 0;
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

    .empty-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      padding: 44px 16px;
      text-align: center;
    }

    .empty-state roque-icon {
      color: #9db2c4;
      margin-bottom: 6px;
    }

    .empty-title {
      margin: 0;
      font-size: 18px;
      font-weight: 600;
      color: #1e395b;
    }

    .empty-sub {
      margin: 0;
      font-size: 13px;
      line-height: 1.5;
      color: #5a6a7a;
      max-width: 320px;
    }

    .spinner-wrap {
      display: flex;
      justify-content: center;
      padding: 40px 0;
    }

    /* --- Posts section (everyone sees it; non-followers get blurred teasers) --- */
    .posts-section {
      margin-top: 18px;
    }

    .posts-title {
      margin: 0 0 10px;
      font-size: 15px;
      font-weight: 600;
      color: #1e395b;
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
      // /creator/{id} path form (nginx maps it to index.html).
      const m = window.location.pathname.match(/\/creator\/(\d+)\/?$/)
      if (m) this.creatorId = Number(m[1])
    }
    if (this.creatorId === null) {
      // No creator id in the URL (e.g. the site root `/`) — fall back to the
      // first/seed creator; only show the empty state when none exists yet.
      try {
        const landing = await api.getDefaultLanding()
        this.landing = landing
        this.creatorId = landing.profile.id
      } catch {
        this.missingCreatorId = true
      } finally {
        this.loading = false
      }
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

  private _loginHref(): string {
    // Return the user here after sign-in (creators go to /admin instead).
    const next = encodeURIComponent(window.location.pathname + window.location.search)
    return `/login?next=${next}`
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
    // Bare handles are turned into real profile urls per platform (never a
    // same-site /search fallback).
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
        // "other" (a website) or an unknown platform: best-effort https link.
        return handle ? `https://${handle}` : value
    }
  }

  render() {
    if (this.loading) {
      return html`<div class="spinner-wrap"><roque-spinner size="36" label="Loading…"></roque-spinner></div>`
    }
    if (this.missingCreatorId) {
      return html`
        <div class="page">
          <roque-card>
            <div class="empty-state">
              <roque-icon name="info" size="42"></roque-icon>
              <h2 class="empty-title">Nothing to do here</h2>
              <p class="empty-sub">
                This page isn’t linked to a creator. Check the link and try
                again.
              </p>
            </div>
          </roque-card>
        </div>
      `
    }
    if (this.error || !this.landing) {
      return html`<roque-card><div class="error-box">${this.error || 'Creator not found'}</div></roque-card>`
    }

    const { profile, viewer, gateways } = this.landing
    const displayName = profile.display_name || profile.username || 'Creator'
    const isAnonymous = viewer.level === 'anonymous'
    const isFollower = viewer.level === 'follower'
    const postCount = profile.post_count ?? 0
    const bannerUrl = profile.banner_url || ''

    return html`
      <div class="page">
        <!-- Hero: banner, avatar, name + online dot, post count, bio, CTA -->
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

              <div class="hero-cta">
                ${isAnonymous
                  ? html`
                      <p class="cta-sub">
                        Create a free account, then subscribe to see everything.
                      </p>
                      <roque-button
                        buttonId="landing-join"
                        @aero-click="${() => (window.location.href = this._loginHref())}"
                        >Join free</roque-button
                      >
                    `
                  : isFollower
                    ? html`
                        <span class="cta-account">
                          Subscriber 🎉 — welcome back${viewer.username ? `, ${viewer.username}` : ''}
                        </span>
                        <p class="cta-sub">Full posts below — enjoy the feed.</p>
                      `
                    : html`
                        <p class="cta-sub">Unlock the full feed for one monthly price.</p>
                        ${viewer.username
                          ? html`<span class="cta-account">Logged in as ${viewer.username}</span>`
                          : nothing}
                        <roque-button
                          buttonId="landing-subscribe"
                          @aero-click="${() =>
                            (window.location.href =
                              '/checkout?creator_id=' + this.creatorId)}"
                          >Subscribe</roque-button
                        >
                        ${gateways.length
                          ? html`<div class="gateway-row">
                              ${gateways.map(
                                (g) =>
                                  html`<span class="gateway-chip">Pay with ${g.label}</span>`,
                              )}
                            </div>`
                          : nothing}
                      `}
              </div>
            </div>

            ${this.landing.social_links.length
              ? html`<div class="social-row">
                  ${this.landing.social_links.map(
                    (s) => html`<a
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

        <section class="posts-section">
          <h2 class="posts-title">Posts</h2>
          ${this.creatorId !== null
            ? html`<roque-subscriber-feed
                creator-id="${this.creatorId}"
              ></roque-subscriber-feed>`
            : nothing}
        </section>
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
    'roque-creator-landing': CreatorLandingPage
  }
}
