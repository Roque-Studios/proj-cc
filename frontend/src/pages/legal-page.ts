import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/layouts/tabs.ts'
import '../components/layouts/card.ts'
import '../components/data/avatar.ts'
import '../components/feedback/spinner.ts'
import '../components/feedback/alert.ts'
import { api, ApiError } from '../lib/api'
import type { CreatorLanding } from '../lib/api'

/**
 * Terms of Service / Privacy Policy page (`/legal?creator_id={id}`).
 *
 * Renders the creator's **effective** legal documents (their own text, or the
 * platform defaults drafted for AI-generated content) as two tabs — Terms of
 * Service and Privacy Policy. Public: no login required, since subscribers
 * must be able to read the documents before deciding to subscribe. The
 * checkout links here from its consent checkboxes.
 */
@customElement('roque-legal-page')
export class LegalPage extends LitElement {
  @state() private creatorId: number | null = null
  @state() private landing: CreatorLanding | null = null
  @state() private loading = true
  @state() private error = ''
  /** 0 = Terms of Service, 1 = Privacy Policy (deep-linked via ?tab=privacy). */
  @state() private initialTab = 0

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .page {
      max-width: 760px;
      margin: 0 auto;
      padding: 20px 16px 60px;
    }

    .header {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 16px;
    }

    .header-info {
      flex: 1;
      min-width: 0;
    }

    .header-name {
      margin: 0;
      font-size: 18px;
      color: #1e395b;
      font-weight: 600;
    }

    .header-handle {
      margin: 2px 0 0;
      font-size: 12px;
      color: #5a6a7a;
    }

    .legal-doc {
      font-size: 13px;
      line-height: 1.65;
      color: #2c2c2c;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .spinner-wrap {
      display: flex;
      justify-content: center;
      padding: 60px 0;
    }

    .error-box {
      padding: 18px;
      text-align: center;
      color: #721c24;
      font-size: 13px;
    }

    .back-link {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin-bottom: 12px;
      font-size: 12px;
      color: #1e5f9e;
      cursor: pointer;
      text-decoration: none;
    }

    .back-link:hover {
      color: #3c7fb1;
      text-decoration: underline;
    }
  `

  connectedCallback() {
    super.connectedCallback()
    void this._resolve()
  }

  private _backHref(): string {
    return this.creatorId ? `/creator/${this.creatorId}` : '/'
  }

  private async _resolve() {
    const params = new URLSearchParams(window.location.search)
    const raw = params.get('creator_id')
    let creatorId: number | null = null
    if (raw && /^\d+$/.test(raw)) {
      creatorId = Number(raw)
    } else {
      const m = window.location.pathname.match(/\/legal\/(\d+)\/?$/)
      if (m) creatorId = Number(m[1])
    }
    this.creatorId = creatorId
    // Honor ?tab=privacy so the checkout consent links can deep-link the
    // privacy document.
    if (params.get('tab') === 'privacy') {
      this.initialTab = 1
    }
    try {
      this.landing = creatorId
        ? await api.getCreatorLanding(creatorId)
        : await api.getDefaultLanding()
      // The landing resolves the creator's id even when not passed in the url
      // (default creator) — store it for the back link.
      this.creatorId = this.landing.profile.id
    } catch (e) {
      this.error = e instanceof ApiError ? e.message : 'Could not load these documents'
    } finally {
      this.loading = false
    }
  }

  render() {
    if (this.loading) {
      return html`<div class="spinner-wrap">
        <roque-spinner size="36" label="Loading…"></roque-spinner>
      </div>`
    }
    if (this.error || !this.landing) {
      return html`<div class="page">
        <roque-card><div class="error-box">${this.error || 'Creator not found'}</div></roque-card>
      </div>`
    }

    const { profile } = this.landing
    const displayName = profile.display_name || profile.username || 'Creator'

    return html`
      <div class="page">
        <a class="back-link" href="${this._backHref()}">← Back to ${displayName}</a>
        <div class="header">
          <roque-avatar
            src="${profile.avatar_url || ''}"
            alt="${displayName}"
            size="52"
          ></roque-avatar>
          <div class="header-info">
            <h1 class="header-name">Terms &amp; Privacy</h1>
            <p class="header-handle">
              Legal documents for @${profile.username || 'creator'} — last
              reviewed 2026-08-08
            </p>
          </div>
        </div>

        <roque-tabs .activeTab="${this.initialTab}">
          <div slot="panel" label="Terms of Service">
            <roque-card heading="Terms of Service">
              <div class="legal-doc">${profile.tos_text || ''}</div>
            </roque-card>
          </div>
          <div slot="panel" label="Privacy Policy">
            <roque-card heading="Privacy Policy">
              <div class="legal-doc">${profile.privacy_text || ''}</div>
            </roque-card>
          </div>
        </roque-tabs>
      </div>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-legal-page': LegalPage
  }
}
