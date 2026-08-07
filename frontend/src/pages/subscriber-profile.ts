import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/layouts/card.ts'
import '../components/data/badge.ts'
import '../components/media/icon.ts'
import '../components/inputs/text-field.ts'
import '../components/buttons/button.ts'
import '../components/feedback/alert.ts'
import '../components/feedback/spinner.ts'
import { api, ApiError, clearTokens, getAccessToken } from '../lib/api'
import type { MySubscription, UserMe } from '../lib/api'

/**
 * Subscriber profile page (`/profile`).
 *
 * Account details (email / username / role), the user's subscriptions with
 * **days left** in the current billing period (from
 * `GET /me/subscriptions`), and a change-password form
 * (`POST /auth/change-password` — verifies the current password, enforces the
 * same complexity rules as registration). After a successful password change
 * the user is signed out and taken back to `/login` to re-authenticate with
 * the new password.
 */
@customElement('roque-subscriber-profile')
export class SubscriberProfile extends LitElement {
  @state() private me: UserMe | null = null
  @state() private subscriptions: MySubscription[] = []
  @state() private loading = true
  @state() private error = ''

  @state() private current = ''
  @state() private next = ''
  @state() private confirm = ''
  @state() private pwError = ''
  @state() private saving = false
  @state() private saved = false

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .page {
      max-width: 560px;
      margin: 0 auto;
      padding: 16px 12px 48px;
    }

    .page h1 {
      margin: 0 0 14px;
      font-size: 22px;
      font-weight: 600;
      color: #1e395b;
    }

    .page-head {
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 14px;
    }

    .page-head h1 {
      margin: 0;
    }

    .section {
      margin-bottom: 16px;
    }

    .info-row {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 7px 0;
      font-size: 13px;
      border-bottom: 1px dashed #d3dde6;
    }

    .info-row:last-child {
      border-bottom: none;
    }

    .info-label {
      color: #5a6a7a;
    }

    .info-value {
      color: #1e2a38;
      font-weight: 600;
      text-align: right;
      word-break: break-all;
    }

    .sub-empty {
      padding: 16px;
      text-align: center;
      font-size: 13px;
      color: #6b7a8a;
    }

    .sub-row {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 0;
      border-bottom: 1px dashed #d3dde6;
    }

    .sub-row:last-of-type {
      border-bottom: none;
    }

    .sub-info {
      flex: 1;
      min-width: 0;
    }

    .sub-name {
      font-size: 14px;
      font-weight: 600;
      color: #1e2a38;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .sub-meta {
      font-size: 11px;
      color: #6b7a8a;
      margin-top: 2px;
    }

    .days {
      text-align: center;
      flex-shrink: 0;
    }

    .days-num {
      font-size: 20px;
      font-weight: 700;
      color: #1e395b;
      line-height: 1.1;
    }

    .days-label {
      font-size: 10px;
      color: #6b7a8a;
    }

    .pw-note {
      font-size: 11px;
      color: #6b7a8a;
      margin: 0 0 12px;
      line-height: 1.5;
    }

    .form-row {
      margin-bottom: 12px;
    }

    .pw-actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 14px;
    }

    .spinner-wrap {
      display: flex;
      justify-content: center;
      padding: 48px 0;
    }

    .error-box {
      padding: 18px;
      text-align: center;
      color: #721c24;
      font-size: 13px;
    }
  `

  connectedCallback() {
    super.connectedCallback()
    void this._load()
  }

  private async _load() {
    if (!getAccessToken()) {
      window.location.href = '/login?next=' + encodeURIComponent('/profile')
      return
    }
    try {
      const [me, subs] = await Promise.all([
        api.me(),
        api.getMySubscriptions(),
      ])
      this.me = me
      this.subscriptions = subs.items
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        window.location.href = '/login?next=' + encodeURIComponent('/profile')
        return
      }
      this.error = e instanceof ApiError ? e.message : 'Could not load your profile'
    } finally {
      this.loading = false
    }
  }

  private _onCurrent(e: CustomEvent) {
    this.current = e.detail?.value ?? ''
  }

  private _onNext(e: CustomEvent) {
    this.next = e.detail?.value ?? ''
  }

  private _onConfirm(e: CustomEvent) {
    this.confirm = e.detail?.value ?? ''
  }

  private async _changePassword() {
    if (this.saving) return
    this.pwError = ''
    this.saved = false

    if (!this.current || !this.next || !this.confirm) {
      this.pwError = 'Fill in every field to change your password.'
      return
    }
    if (this.next !== this.confirm) {
      this.pwError = 'New passwords do not match.'
      return
    }
    if (this.next.length < 8) {
      this.pwError = 'The new password needs at least 8 characters.'
      return
    }

    this.saving = true
    try {
      await api.changePassword(this.current, this.next)
      this.saved = true
      // Tokens stay valid after the change; sign out so the next sign-in
      // uses the new password.
      const refresh = localStorage.getItem('cc_refresh_token')
      if (refresh) api.logout(refresh).catch(() => undefined)
      clearTokens()
      window.setTimeout(() => {
        window.location.href = '/login?changed=1'
      }, 900)
    } catch (e) {
      this.pwError = e instanceof ApiError ? e.message : 'Could not change your password'
    } finally {
      this.saving = false
    }
  }

  private _goBack() {
    // The feed is where subscribers browse; the profile is reached from its
    // menu, so navigating straight back there is always correct.
    window.location.href = '/feed'
  }

  private _statusBadge(status: string) {
    const ctx =
      status === 'active'
        ? 'success'
        : status === 'trialing'
          ? 'info'
          : status === 'past_due'
            ? 'warning'
            : 'error'
    return html`<roque-badge context="${ctx}">${status}</roque-badge>`
  }

  private _header() {
    return html`<div class="page-head">
      <roque-button context="clear" @aero-click="${this._goBack}">
        <roque-icon name="back" size="14" style="margin-right:6px"></roque-icon>
        Back to feed
      </roque-button>
      <h1>My profile</h1>
    </div>`
  }

  render() {
    if (this.loading) {
      return html`<div class="page">
        ${this._header()}
        <div class="spinner-wrap">
          <roque-spinner size="36" label="Loading profile…"></roque-spinner>
        </div>
      </div>`
    }
    if (this.error && !this.me) {
      return html`<div class="page">
        ${this._header()}
        <roque-card><div class="error-box">${this.error}</div></roque-card>
      </div>`
    }
    const me = this.me!

    return html`
      <div class="page">
        ${this._header()}

        <div class="section">
          <roque-card heading="Account">
            <div class="info-row">
              <span class="info-label">Email</span>
              <span class="info-value">${me.email}</span>
            </div>
            <div class="info-row">
              <span class="info-label">Username</span>
              <span class="info-value">${me.username ?? '—'}</span>
            </div>
            <div class="info-row">
              <span class="info-label">Role</span>
              <span class="info-value">${me.is_creator ? 'Creator' : 'Member'}</span>
            </div>
          </roque-card>
        </div>

        <div class="section">
          <roque-card heading="My subscriptions">
            ${this.subscriptions.length === 0
              ? html`<div class="sub-empty">
                  You're not subscribed to any creators yet.
                  <div style="margin-top:10px">
                    <roque-button buttonId="profile-browse" @aero-click="${() => (window.location.href = '/')}"
                      >Browse creators</roque-button
                    >
                  </div>
                </div>`
              : html`${this.subscriptions.map(
                  (sub) => html`
                    <div class="sub-row">
                      <div class="sub-info">
                        <div class="sub-name">
                          ${sub.creator_display_name || sub.creator_username || `Creator #${sub.creator_id}`}
                        </div>
                        <div class="sub-meta">
                          @${sub.creator_username ?? sub.creator_id} ·
                          ${this._statusBadge(sub.status)}${sub.cancel_at_period_end ? ' · renews off' : ''}
                        </div>
                      </div>
                      ${sub.days_left != null
                        ? html`<div class="days">
                            <div class="days-num">${sub.days_left}</div>
                            <div class="days-label">days left</div>
                          </div>`
                        : html`<div class="days">
                            <div class="days-num" style="font-size:12px;color:#8a97a5">—</div>
                          </div>`}
                    </div>
                  `,
                )}`}
          </roque-card>
        </div>

        <div class="section">
          <roque-card heading="Change password">
            <p class="pw-note">
              Verify your current password, then pick a new one (at least 8
              characters, with a lowercase, an uppercase and a digit). You'll
              be signed out afterwards.
            </p>
            <div class="form-row">
              <roque-text-field
                type="password"
                label="Current password"
                placeholder="••••••••"
                .value="${this.current}"
                @aero-input="${this._onCurrent}"
              ></roque-text-field>
            </div>
            <div class="form-row">
              <roque-text-field
                type="password"
                label="New password"
                placeholder="••••••••"
                .value="${this.next}"
                @aero-input="${this._onNext}"
              ></roque-text-field>
            </div>
            <div class="form-row">
              <roque-text-field
                type="password"
                label="Confirm new password"
                placeholder="••••••••"
                .value="${this.confirm}"
                @aero-input="${this._onConfirm}"
              ></roque-text-field>
            </div>
            ${this.pwError
              ? html`<roque-alert
                  type="error"
                  heading="Cannot change password"
                  message="${this.pwError}"
                  @aero-dismiss="${() => (this.pwError = '')}"
                ></roque-alert>`
              : ''}
            ${this.saved
              ? html`<roque-alert
                  type="success"
                  heading="Password updated"
                  message="Signing you out — please sign in again with your new password."
                ></roque-alert>`
              : ''}
            <div class="pw-actions">
              <roque-button
                context="submit"
                buttonId="pw-save"
                @aero-click="${this._changePassword}"
                >${this.saving ? 'Saving…' : 'Change password'}</roque-button
              >
            </div>
          </roque-card>
        </div>
      </div>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-subscriber-profile': SubscriberProfile
  }
}
