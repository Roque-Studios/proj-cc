import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/checkout/subscribe-checkout.ts'
import '../components/layouts/card.ts'
import '../components/buttons/button.ts'
import '../components/data/avatar.ts'
import '../components/feedback/spinner.ts'
import { api, ApiError, getAccessToken } from '../lib/api'
import type { CreatorLanding } from '../lib/api'

/**
 * Subscribe / checkout page (`/checkout?creator_id={id}`).
 *
 * Wrapper around the reusable `roque-subscribe-checkout`: resolves the creator
 * id from the URL and shows a compact creator header. Anonymous visitors are
 * sent to the login page (subscribing requires an account).
 */
@customElement('roque-subscribe-checkout-page')
export class SubscribeCheckoutPage extends LitElement {
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
      max-width: 520px;
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
      color: var(--cc-heading);
      font-weight: 600;
    }

    .header-handle {
      margin: 2px 0 0;
      font-size: 12px;
      color: var(--cc-text-secondary);
    }

    .prompt {
      text-align: center;
      padding: 22px 16px;
    }

    .prompt-title {
      margin: 0 0 6px;
      font-size: 14px;
      font-weight: 600;
      color: var(--cc-heading);
    }

    .prompt-sub {
      margin: 0 0 14px;
      font-size: 12px;
      color: var(--cc-text-secondary);
      line-height: 1.5;
    }

    .error-box {
      padding: 18px;
      text-align: center;
      color: var(--cc-danger-strong);
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
    // Subscribing requires an account — send anonymous visitors to sign in,
    // then bring them straight back here (role redirect may take creators to
    // /admin instead).
    if (!getAccessToken()) {
      const next = encodeURIComponent(
        window.location.pathname + window.location.search,
      )
      window.location.href = `/login?next=${next}`
      return
    }
    const params = new URLSearchParams(window.location.search)
    const raw = params.get('creator_id')
    if (raw && /^\d+$/.test(raw)) {
      this.creatorId = Number(raw)
    } else {
      const m = window.location.pathname.match(/\/checkout\/(\d+)\/?$/)
      if (m) this.creatorId = Number(m[1])
    }
    if (this.creatorId === null) {
      this.error = 'Missing creator id — open /checkout?creator_id={id}'
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

    const { profile } = this.landing
    const displayName = profile.display_name || profile.username || 'Creator'

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

        <roque-subscribe-checkout creator-id="${this.creatorId}"></roque-subscribe-checkout>
      </div>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-subscribe-checkout-page': SubscribeCheckoutPage
  }
}
