import { LitElement, html, css, nothing } from 'lit'
import { customElement, property, state } from 'lit/decorators.js'

import '../layouts/card.ts'
import '../inputs/radio.ts'
import '../data/badge.ts'
import '../buttons/button.ts'
import '../media/icon.ts'
import '../feedback/spinner.ts'
import '../feedback/toast.ts'
import { api, ApiError } from '../../lib/api'
import type {
  CreatorLanding,
  LandingGateway,
  SubscribeStatus,
} from '../../lib/api'

const PENDING_KEY = 'cc_pending_checkout' // creatorId -> checkout url (JSON)

/**
 * Subscribe / checkout UI for a creator.
 *
 * Shows only the **creator's enabled** gateways (from the backend — the
 * checkout list endpoint and the landing payload both expose exactly the set
 * a subscriber may pay with), lets the user pick one, and starts the
 * subscription (``POST /subscribe`` with the chosen provider). The hosted
 * checkout url is stored locally and the user is redirected; when they return,
 * the page polls ``GET /subscribe/status`` to reconcile the final state —
 * a webhook-driven status transition (``incomplete`` → ``active``/``trialing``)
 * shows success; a still-``incomplete`` row (or a terminal state) shows a
 * clear pending/failure message with the option to try again.
 *
 * States handled:
 * - already a follower — success panel (no payment form);
 * - pending payment (incomplete row with a checkout url) — resume + status;
 * - no gateways enabled — clear error (nothing to pay with);
 * - payment started — redirect; return-reconcile via polling.
 */
@customElement('roque-subscribe-checkout')
export class SubscribeCheckout extends LitElement {
  /** The creator to subscribe to. */
  @property({ type: Number, attribute: 'creator-id' }) creatorId = 0

  @state() private landing: CreatorLanding | null = null
  @state() private gateways: LandingGateway[] = []
  @state() private selected: string | null = null
  @state() private status: SubscribeStatus | null = null
  @state() private loading = true
  @state() private subscribing = false
  @state() private error = ''
  @state() private toastMessage = ''
  @state() private toastHeading = ''
  @state() private toastType: 'info' | 'error' = 'info'
  /** True while polling for the final state after a checkout redirect. */
  @state() private reconciling = false

  private _pollTimer: ReturnType<typeof setInterval> | null = null

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .panel {
      padding: 6px 0;
    }

    .price-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 14px;
      padding-bottom: 12px;
      border-bottom: 1px dashed #c4ccd4;
    }

    .price-label {
      font-size: 12px;
      color: #5a6a7a;
    }

    .price-value {
      font-size: 18px;
      font-weight: 700;
      color: #1e395b;
    }

    .gateway-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .gateway-option {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      border: 1px solid #c8d2dc;
      border-radius: 4px;
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.8),
        rgba(173, 216, 230, 0.18)
      );
      cursor: pointer;
      transition: box-shadow 0.2s ease, border-color 0.2s ease;
    }

    .gateway-option:hover {
      border-color: #5b9ed6;
      box-shadow: 0 0 5px rgba(0, 162, 232, 0.4);
    }

    .gateway-option.selected {
      border-color: #3c7fb1;
      box-shadow:
        0 0 0 1px #3c7fb1,
        0 0 6px rgba(60, 127, 177, 0.35);
    }

    .gateway-label {
      flex: 1;
      font-size: 13px;
      color: #1e1e1e;
      font-weight: 600;
    }

    .gateway-secure {
      font-size: 11px;
      color: #2a7d2a;
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }

    .actions {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid #dcdcdc;
    }

    .status-panel {
      text-align: center;
      padding: 20px 12px;
    }

    .status-icon {
      font-size: 30px;
      margin-bottom: 6px;
    }

    .status-title {
      margin: 0 0 6px;
      font-size: 15px;
      font-weight: 600;
      color: #1e395b;
    }

    .status-sub {
      margin: 0 auto 14px;
      font-size: 12px;
      color: #5a6a7a;
      max-width: 420px;
      line-height: 1.5;
    }

    .resume-note {
      font-size: 11px;
      color: #8a97a5;
      margin-top: 8px;
    }

    .error-box {
      padding: 16px;
      text-align: center;
      color: #721c24;
      font-size: 13px;
    }

    .spinner-wrap {
      display: flex;
      justify-content: center;
      padding: 40px 0;
    }

    .reconciling-note {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 12px;
      color: #5a6a7a;
      margin-top: 10px;
    }
  `

  connectedCallback() {
    super.connectedCallback()
    void this._load()
  }

  disconnectedCallback() {
    super.disconnectedCallback()
    this._stopPolling()
  }

  private async _load() {
    if (!this.creatorId) return
    try {
      const [landing, gateways, status] = await Promise.all([
        api.getCreatorLanding(this.creatorId),
        api.getCreatorGateways(this.creatorId),
        api.getSubscribeStatus(this.creatorId),
      ])
      this.landing = landing
      this.gateways = gateways
      this.status = status
      if (gateways.length > 0 && !this.selected) {
        this.selected = gateways[0].gateway
      }
      this._restorePending()
    } catch (e) {
      this.error = e instanceof ApiError ? e.message : 'Could not load checkout'
    } finally {
      this.loading = false
    }
  }

  private get _isFollower(): boolean {
    return this.status?.subscription?.status === 'active'
  }

  private get _isTrialing(): boolean {
    return this.status?.subscription?.status === 'trialing'
  }

  private get _isPending(): boolean {
    return this.status?.subscription?.status === 'incomplete'
  }

  private _price(): string {
    const cents = this.status?.tier_price_cents ?? 500
    return `$${(cents / 100).toFixed(2)}`
  }

  // ------------------------------------------------------------------ #
  // Pending-checkout restore + return reconciliation
  // ------------------------------------------------------------------ #

  private _pendingKey(): string {
    return `${PENDING_KEY}_${this.creatorId}`
  }

  private _restorePending() {
    // A stale marker (payment completed while the user was away) is cleared
    // whenever the row is no longer pending — otherwise markers accumulate.
    if (!this._isPending) {
      try {
        localStorage.removeItem(this._pendingKey())
      } catch {
        /* ignore */
      }
      return
    }
    try {
      if (!localStorage.getItem(this._pendingKey())) return
    } catch {
      return
    }
    // The user just returned from the hosted checkout with the row still
    // incomplete — the webhook may be in flight. Poll the status endpoint to
    // reconcile the final state (active/trialing = success) for up to ~30 s.
    this.reconciling = true
    this._startPolling()
  }

  private _stopPolling() {
    if (this._pollTimer) {
      clearInterval(this._pollTimer)
      this._pollTimer = null
    }
  }

  private _startPolling() {
    this._stopPolling()
    let attempts = 0
    this._pollTimer = setInterval(async () => {
      attempts += 1
      try {
        const status = await api.getSubscribeStatus(this.creatorId)
        this.status = status
        const s = status.subscription?.status
        if (s === 'active' || s === 'trialing') {
          this._finishReconcile('success')
        } else if (s === 'canceled' || s === 'expired') {
          this._finishReconcile('failed')
        }
      } catch {
        /* transient — keep polling */
      }
      if (attempts >= 15) {
        // ~30 s elapsed and the webhook still hasn't landed — surface the
        // still-pending state and let the user retry or resume.
        this._stopPolling()
        this.reconciling = false
      }
    }, 2000)
  }

  private _finishReconcile(outcome: 'success' | 'failed') {
    this._stopPolling()
    this.reconciling = false
    try {
      localStorage.removeItem(this._pendingKey())
    } catch {
      /* ignore */
    }
    this._toast(
      outcome === 'success' ? 'info' : 'error',
      outcome === 'success'
        ? 'Payment received — welcome aboard!'
        : 'The payment was not completed.',
      outcome === 'success' ? 'Subscribed' : 'Payment not completed',
    )
  }

  // ------------------------------------------------------------------ #
  // Actions
  // ------------------------------------------------------------------ #

  private async _subscribe() {
    if (this.subscribing || this.creatorId === 0 || !this.selected) return
    this.subscribing = true
    this.error = ''
    try {
      const result = await api.subscribe(this.creatorId, this.selected)
      this.status = {
        viewer_level: 'registered',
        subscription: result.subscription,
        tier_price_cents: this.status?.tier_price_cents ?? 500,
      }
      const checkoutUrl = result.checkout_url
      if (checkoutUrl) {
        // Remember the pending checkout so the return path can reconcile.
        try {
          localStorage.setItem(this._pendingKey(), String(Date.now()))
        } catch {
          /* ignore */
        }
        // Success path: the hosted checkout completes the payment; webhooks
        // reconcile the subscription (the status poll handles the return).
        window.location.href = checkoutUrl
        return
      }
      this._toast('info', 'Subscription started — check your email to complete payment.', 'Almost there')
    } catch (e) {
      this.error = e instanceof ApiError ? e.message : 'Subscription failed'
      this._toast('error', this.error, 'Subscription failed')
    } finally {
      this.subscribing = false
    }
  }

  private _toast(type: 'info' | 'error', message: string, heading: string) {
    this.toastType = type
    this.toastMessage = message
    this.toastHeading = heading
    window.setTimeout(() => (this.toastMessage = ''), 6000)
  }

  // ------------------------------------------------------------------ #
  // Render
  // ------------------------------------------------------------------ #

  render() {
    if (this.loading) {
      return html`<div class="spinner-wrap"><roque-spinner size="36" label="Loading checkout…"></roque-spinner></div>`
    }
    if (this.error && !this.landing) {
      return html`<roque-card><div class="error-box">${this.error}</div></roque-card>`
    }

    const profile = this.landing?.profile
    const displayName = profile?.display_name || profile?.username || 'Creator'

    // Already subscribed — success state.
    if (this._isFollower || this._isTrialing) {
      return html`
        <roque-card>
          <div class="status-panel">
            <div class="status-icon">🎉</div>
            <p class="status-title">You're subscribed to ${displayName}</p>
            <p class="status-sub">
              Your subscription is ${this._isTrialing ? 'trialing' : 'active'}.
              Head to the feed to see the full content.
            </p>
            <roque-button
              buttonId="checkout-to-feed"
              @aero-click="${() =>
                (window.location.href = '/feed?creator_id=' + this.creatorId)}"
              >Open the feed</roque-button
            >
          </div>
        </roque-card>
      `
    }

    // Reconciliation state (returning from the hosted checkout).
    if (this.reconciling) {
      return html`
        <roque-card>
          <div class="status-panel">
            <div class="status-icon">⏳</div>
            <p class="status-title">Confirming your payment…</p>
            <p class="status-sub">
              You returned from the checkout page. We're waiting for the
              payment confirmation to finalize your subscription — this takes
              a moment.
            </p>
            <div class="reconciling-note">
              <roque-spinner size="16"></roque-spinner>
              <span>Checking payment status…</span>
            </div>
          </div>
        </roque-card>
      `
    }

    // No gateways enabled.
    if (this.gateways.length === 0) {
      return html`
        <roque-card>
          <div class="status-panel">
            <div class="status-icon">🔒</div>
            <p class="status-title">Subscriptions are unavailable</p>
            <p class="status-sub">
              ${displayName} hasn't enabled any payment methods yet. Check back
              soon.
            </p>
          </div>
        </roque-card>
      `
    }

    // Pending (incomplete) payment that never completed — offer to retry.
    if (this._isPending) {
      return html`
        <roque-card>
          <div class="status-panel">
            <div class="status-icon">💳</div>
            <p class="status-title">Payment not completed</p>
            <p class="status-sub">
              You have a pending subscription to ${displayName}. Pick a payment
              method below to try again — you won't be charged twice for a
              completed payment.
            </p>
            <roque-button
              buttonId="checkout-resume"
              context="clear"
              @aero-click="${() => {
                const url = this.status?.subscription?.checkout_url
                if (url) window.location.href = url
              }}"
              >${this.status?.subscription?.checkout_url
                ? 'Resume checkout'
                : nothing}</roque-button
            >
            <div class="panel">
              ${this._renderGatewayForm(displayName)}
            </div>
          </div>
        </roque-card>
      `
    }

    return html`${this._renderGatewayForm(displayName)}
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

  private _renderGatewayForm(displayName: string) {
    return html`
      <roque-card heading="Subscribe to ${displayName}">
        <div class="panel">
          <div class="price-row">
            <span class="price-label">Monthly subscription</span>
            <span class="price-value">${this._price()}<span style="font-size:11px;color:#7a8794"> /mo</span></span>
          </div>

          <p style="font-size:12px;color:#5a6a7a;margin:0 0 10px">Choose a payment method:</p>
          <div class="gateway-list">
            ${this.gateways.map(
              (g) => html`
                <label
                  class="gateway-option ${this.selected === g.gateway ? 'selected' : ''}"
                  @click="${() => (this.selected = g.gateway)}"
                >
                  <roque-radio
                    name="gateway"
                    value="${g.gateway}"
                    label=""
                    .checked="${this.selected === g.gateway}"
                    @aero-change="${() => (this.selected = g.gateway)}"
                  ></roque-radio>
                  <span class="gateway-label">${g.label}</span>
                  <span class="gateway-secure">
                    <roque-icon name="lock" size="11"></roque-icon> secure
                  </span>
                </label>
              `,
            )}
          </div>

          ${this.error
            ? html`<div class="error-box">${this.error}</div>`
            : nothing}

          <div class="actions">
            <span class="resume-note">You'll be redirected to ${this.selected ?? 'the gateway'} to complete payment.</span>
            <roque-button
              buttonId="checkout-submit"
              @aero-click="${this._subscribe}"
              >${this.subscribing ? 'Opening checkout…' : 'Subscribe'}</roque-button
            >
          </div>
        </div>
      </roque-card>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-subscribe-checkout': SubscribeCheckout
  }
}
