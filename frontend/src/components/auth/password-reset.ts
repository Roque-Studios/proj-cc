import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../inputs/text-field.ts'
import '../buttons/button.ts'
import '../feedback/alert.ts'
import { api, ApiError } from '../../lib/api'
import { makePowProof, type PowProof } from '../../lib/pow'
import type { AntiBot } from '../../lib/api'

/**
 * Shared "forgot password" flow for every role.
 *
 * Two steps inside one component:
 *  1. Enter the account email → `POST /auth/forgot-password`. When no SMTP
 *     server is configured (dev/mock) the API returns the reset code as
 *     `dev_token`, which is surfaced here so the flow works without mail.
 *  2. Enter the reset code + a new password → `POST /auth/reset-password`.
 *
 * Emits `aero-password-reset-success` when the password was changed, so the
 * host login page can flip back to the sign-in form.
 */
@customElement('roque-password-reset')
export class PasswordReset extends LitElement {
  @state() private step: 'email' | 'code' = 'email'
  @state() private email = ''
  @state() private token = ''
  @state() private newPassword = ''
  @state() private confirm = ''
  @state() private error = ''
  @state() private notice = ''
  @state() private busy = false
  // Honeypot: hidden field bots auto-fill; humans never see it.
  @state() private website = ''

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .form-row {
      margin-bottom: 12px;
    }

    .actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 16px;
    }

    .back {
      background: none;
      border: none;
      padding: 0;
      font: inherit;
      color: var(--cc-accent-strong);
      cursor: pointer;
      text-decoration: underline;
    }

    .back:hover {
      color: var(--cc-accent-deep);
    }

    .notice {
      font-size: 12px;
      color: #3a6b35;
      background: #eef7ec;
      border: 1px solid #cfe6cb;
      border-radius: 4px;
      padding: 10px 12px;
      margin: 0 0 12px;
      line-height: 1.5;
    }

    .dev-code {
      font-weight: 600;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 13px;
      word-break: break-all;
      color: var(--cc-heading);
    }

    /* Honeypot — off-screen, unfocusable, invisible to humans. */
    .hp-field {
      position: absolute;
      left: -9999px;
      width: 1px;
      height: 1px;
      opacity: 0;
      overflow: hidden;
      pointer-events: none;
    }
  `

  private _reset() {
    this.step = 'email'
    this.token = ''
    this.newPassword = ''
    this.confirm = ''
    this.error = ''
    this.notice = ''
  }

  private _onEmail(e: CustomEvent) {
    this.email = (e.detail?.value ?? '').trim()
  }

  private _onToken(e: CustomEvent) {
    this.token = (e.detail?.value ?? '').trim()
  }

  private _onNewPassword(e: CustomEvent) {
    this.newPassword = e.detail?.value ?? ''
  }

  private _onConfirm(e: CustomEvent) {
    this.confirm = e.detail?.value ?? ''
  }

  private _onWebsite(e: Event) {
    this.website = (e.target as HTMLInputElement).value ?? ''
  }

  private async _requestCode() {
    if (this.busy) return
    this.error = ''
    this.notice = ''
    if (!this.email) {
      this.error = 'Enter the email for your account.'
      return
    }
    this.busy = true
    try {
      // Degrade to no proof if the challenge fetch fails — the server 403s
      // with a clear message when PoW is actually required.
      let pow: PowProof | null = null
      try {
        pow = await makePowProof(() => api.getPowChallenge())
      } catch {
        pow = null
      }
      const antiBot: AntiBot = {
        website: this.website.trim() || undefined,
        pow,
      }
      const res = await api.forgotPassword(this.email, antiBot)
      if (res.dev_token) {
        // No SMTP configured — dev/mock mode hands the code back directly.
        this.notice = ''
        this.step = 'code'
        this.notice = `Your reset code: ${res.dev_token} — enter it below with a new password.`
      } else {
        this.step = 'code'
        this.notice = 'If that email has an account, a reset code is on its way. Enter it below with a new password.'
      }
    } catch (err) {
      this.error =
        err instanceof ApiError
          ? err.message
          : 'Could not request a reset code — please try again.'
    } finally {
      this.busy = false
    }
  }

  private async _submitReset() {
    if (this.busy) return
    this.error = ''
    if (!this.token || !this.newPassword) {
      this.error = 'Enter the reset code and a new password.'
      return
    }
    if (this.newPassword !== this.confirm) {
      this.error = 'Passwords do not match.'
      return
    }
    if (this.newPassword.length < 8) {
      this.error = 'Passwords need at least 8 characters.'
      return
    }
    this.busy = true
    try {
      await api.resetPassword(this.token, this.newPassword)
      this.dispatchEvent(
        new CustomEvent('aero-password-reset-success', {
          bubbles: true,
          composed: true,
          detail: { email: this.email },
        }),
      )
    } catch (err) {
      this.error =
        err instanceof ApiError
          ? err.message
          : 'Could not reset your password — please try again.'
    } finally {
      this.busy = false
    }
  }

  render() {
    const requesting = this.step === 'email'

    return html`
      <input
        class="hp-field"
        type="text"
        name="website"
        tabindex="-1"
        autocomplete="off"
        aria-hidden="true"
        .value="${this.website}"
        @input="${this._onWebsite}"
      />
      <div class="form-row">
        <roque-text-field
          label="Account email"
          placeholder="you@example.com"
          type="email"
          .value="${this.email}"
          @aero-input="${this._onEmail}"
        ></roque-text-field>
      </div>

      ${requesting
        ? html`<div class="actions">
            <roque-button
              context="submit"
              buttonId="reset-request-btn"
              @aero-click="${this._requestCode}"
              >${this.busy ? 'Sending…' : 'Send reset code'}</roque-button
            >
          </div>`
        : html`
            ${this.notice ? html`<p class="notice">${this.notice}</p>` : ''}
            <div class="form-row">
              <roque-text-field
                label="Reset code"
                placeholder="Paste the code from your email"
                .value="${this.token}"
                @aero-input="${this._onToken}"
              ></roque-text-field>
            </div>
            <div class="form-row">
              <roque-text-field
                type="password"
                label="New password"
                placeholder="••••••••"
                .value="${this.newPassword}"
                @aero-input="${this._onNewPassword}"
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
            <p class="notice" style="color:var(--cc-text-secondary);background:#f1f5f9;border-color:#dbe4ee">
              Passwords need at least 8 characters, one lowercase, one
              uppercase and one digit. The code expires in 30 minutes.
            </p>
            <div class="actions">
              <button class="back" @click="${this._reset}">Start over</button>
              <roque-button
                context="submit"
                buttonId="reset-submit-btn"
                @aero-click="${this._submitReset}"
                >${this.busy ? 'Resetting…' : 'Reset password'}</roque-button
              >
            </div>
          `}

      ${this.error
        ? html`<roque-alert
            type="error"
            heading="Password reset"
            message="${this.error}"
            @aero-dismiss="${() => (this.error = '')}"
          ></roque-alert>`
        : ''}
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-password-reset': PasswordReset
  }
}
