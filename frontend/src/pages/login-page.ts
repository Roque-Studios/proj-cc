import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/inputs/text-field.ts'
import '../components/buttons/button.ts'
import '../components/layouts/card.ts'
import '../components/feedback/alert.ts'
import '../components/feedback/spinner.ts'
import '../components/auth/password-reset.ts'
import { api, ApiError, getAccessToken, setTokens } from '../lib/api'
import { makePowProof, type PowProof } from '../lib/pow'
import type { AntiBot } from '../lib/api'

/**
 * Shared sign-in page for every role (`/login`).
 *
 * One account system: creators and followers sign in here. After a successful
 * sign-in the user is redirected by role — creators to the `/admin`
 * dashboard, everyone else back to the page they came from (`?next=`, or the
 * site root). A small "create account" flow is built in (followers can't
 * subscribe without an account, and there was no registration UI anywhere).
 */
@customElement('roque-login-page')
export class LoginPage extends LitElement {
  @state() private mode: 'login' | 'register' | 'reset' = 'login'
  @state() private email = ''
  @state() private username = ''
  @state() private password = ''
  @state() private confirm = ''
  @state() private error = ''
  @state() private busy = false
  @state() private checking = !!getAccessToken()
  // Honeypot: a visually-hidden field real users never see — bots auto-fill it.
  @state() private website = ''

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .login-wrap {
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 30px;
      box-sizing: border-box;
    }

    .login-card {
      width: 100%;
      max-width: 420px;
    }

    .brand {
      text-align: center;
      margin-bottom: 4px;
    }

    .brand h1 {
      margin: 0;
      font-size: 20px;
      font-weight: normal;
      color: #1e395b;
    }

    .brand p {
      margin: 4px 0 0;
      font-size: 12px;
      color: #4a5b6e;
    }

    .form-row {
      margin-bottom: 12px;
    }

    .hint {
      font-size: 11px;
      color: #6b7a8a;
      margin: 14px 0 0;
      line-height: 1.5;
    }

    .actions {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 16px;
    }

    .mode-switch {
      margin-top: 14px;
      text-align: center;
      font-size: 12px;
      color: #4a5b6e;
    }

    .mode-switch button {
      background: none;
      border: none;
      padding: 0;
      font: inherit;
      color: #1e6fb4;
      cursor: pointer;
      text-decoration: underline;
    }

    .mode-switch button:hover {
      color: #165a92;
    }

    .spinner-wrap {
      display: flex;
      justify-content: center;
      padding: 60px 0;
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

  connectedCallback() {
    super.connectedCallback()
    void this._redirectIfSignedIn()
  }

  /** Safe `?next=` target: same-origin relative paths only.
   *
   * Rejects protocol-relative (`//host`), scheme (`javascript:`) and
   * backslash tricks (`/\host` — browsers treat a leading backslash as a
   * slash, which would navigate cross-origin).
   */
  private _safeNext(): string | null {
    const next = new URLSearchParams(window.location.search).get('next')
    if (next && /^\/(?!\/)(?!\\)/.test(next) && !next.includes('\\')) return next
    return null
  }

  private async _redirectIfSignedIn() {
    if (!getAccessToken()) {
      this.checking = false
      return
    }
    try {
      const me = await api.me()
      this._go(me.is_creator ? '/admin' : this._safeNext() ?? '/')
    } catch {
      // Stored token invalid/expired — the request() helper already cleared it.
      this.checking = false
    }
  }

  private _go(url: string) {
    window.location.href = url
  }

  private async _afterAuth() {
    // Role-based redirect: creators -> admin dashboard, members -> back.
    // The tokens are already stored — a failed role lookup must not strand
    // the user on the login page, so it degrades to the member redirect.
    let isCreator = false
    try {
      const me = await api.me()
      isCreator = me.is_creator
    } catch {
      // Best-effort: login succeeded, the next page re-checks with the token.
    }
    this._go(isCreator ? '/admin' : this._safeNext() ?? '/')
  }

  private async _antiBot(): Promise<AntiBot> {
    // Solve the server's proof-of-work (skipped when disabled) and include the
    // honeypot value — both together cost bots time and catch naive scrapers.
    // A transient challenge-fetch failure degrades to no proof: when PoW is
    // actually enabled the server answers 403 with a clear retry message.
    let pow: PowProof | null = null
    try {
      pow = await makePowProof(() => api.getPowChallenge())
    } catch {
      pow = null
    }
    return {
      website: this.website.trim() || undefined,
      pow,
    }
  }

  private async _submit() {
    if (this.busy) return
    this.error = ''

    const email = this.email.trim()
    if (this.mode === 'register') {
      if (!email || !this.password || !this.confirm) {
        this.error = 'Fill in every field to create your account.'
        return
      }
      if (this.password !== this.confirm) {
        this.error = 'Passwords do not match.'
        return
      }
    } else if (!email || !this.password) {
      this.error = 'Enter your email and password to continue.'
      return
    }

    this.busy = true
    try {
      const antiBot = await this._antiBot()
      if (this.mode === 'register') {
        await api.register(email, this.password, this.username.trim() || undefined, antiBot)
        // Registration doesn't return tokens — sign in right after.
      }
      const tokens = await api.login(email, this.password, antiBot)
      setTokens(tokens.access_token, tokens.refresh_token)
      await this._afterAuth()
    } catch (err) {
      this.error =
        err instanceof ApiError
          ? err.message
          : this.mode === 'register'
            ? 'Could not create your account — please try again.'
            : 'Login failed — please try again.'
      this.busy = false
    }
  }

  private _onWebsite(e: Event) {
    this.website = (e.target as HTMLInputElement).value ?? ''
  }

  private _toggleMode() {
    this.error = ''
    this.mode = this.mode === 'login' ? 'register' : 'login'
  }

  private _showReset() {
    this.error = ''
    this.mode = 'reset'
  }

  private _onResetSuccess() {
    // Back to the sign-in form with a pointer to the new flow.
    this.error = ''
    this.mode = 'login'
    this.password = ''
    this.confirm = ''
  }

  private _onEmail(e: CustomEvent) {
    this.email = (e.detail?.value ?? '').trim()
  }

  private _onUsername(e: CustomEvent) {
    this.username = e.detail?.value ?? ''
  }

  private _onPassword(e: CustomEvent) {
    this.password = e.detail?.value ?? ''
  }

  private _onConfirm(e: CustomEvent) {
    this.confirm = e.detail?.value ?? ''
  }

  render() {
    if (this.checking) {
      return html`<div class="spinner-wrap"><roque-spinner size="36" label="Checking…"></roque-spinner></div>`
    }

    const isRegister = this.mode === 'register'
    const isReset = this.mode === 'reset'

    return html`
      <div class="login-wrap">
        <div class="login-card">
          <div class="brand">
            <h1>${isReset
              ? 'Reset your password'
              : isRegister
                ? 'Create your account'
                : 'Welcome back'}</h1>
            <p>${isReset
              ? 'Enter your email to receive a reset code'
              : 'Sign in to subscribe to creators'}</p>
          </div>

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

          <roque-card heading="${isReset
            ? 'Password reset'
            : isRegister
              ? 'Create account'
              : 'Sign in'}">
            ${isReset
              ? html`<roque-password-reset
                  @aero-password-reset-success="${this._onResetSuccess}"
                ></roque-password-reset>
                <p class="mode-switch">
                  Remembered it?
                  <button @click="${this._toggleMode}">Back to sign in</button>
                </p>`
              : html`
                  ${isRegister
                    ? html`<div class="form-row">
                        <roque-text-field
                          label="Username (optional)"
                          placeholder="yourname"
                          .value="${this.username}"
                          @aero-input="${this._onUsername}"
                        ></roque-text-field>
                      </div>`
                    : ''}

                  <div class="form-row">
                    <roque-text-field
                      label="Email"
                      placeholder="you@example.com"
                      .value="${this.email}"
                      @aero-input="${this._onEmail}"
                    ></roque-text-field>
                  </div>

                  <div class="form-row">
                    <roque-text-field
                      type="password"
                      label="Password"
                      placeholder="••••••••"
                      .value="${this.password}"
                      @aero-input="${this._onPassword}"
                    ></roque-text-field>
                  </div>

                  ${isRegister
                    ? html`<div class="form-row">
                        <roque-text-field
                          type="password"
                          label="Confirm password"
                          placeholder="••••••••"
                          .value="${this.confirm}"
                          @aero-input="${this._onConfirm}"
                        ></roque-text-field>
                      </div>`
                    : ''}

                  ${!isRegister
                    ? html`<p class="mode-switch" style="margin-top:0;text-align:right">
                        <button @click="${this._showReset}">Forgot password?</button>
                      </p>`
                    : ''}

                  ${this.error
                    ? html`<roque-alert
                        type="error"
                        heading="${isRegister ? 'Cannot create account' : 'Cannot sign in'}"
                        message="${this.error}"
                        @aero-dismiss="${() => (this.error = '')}"
                      ></roque-alert>`
                    : ''}

                  <div class="actions">
                    <roque-button
                      context="clear"
                      buttonId="login-home"
                      @aero-click="${() => this._go('/')}"
                      >Back to site</roque-button
                    >
                    <roque-button
                      context="submit"
                      buttonId="login-btn"
                      @aero-click="${this._submit}"
                      >${this.busy
                        ? isRegister
                          ? 'Creating account…'
                          : 'Signing in…'
                        : isRegister
                          ? 'Create account'
                          : 'Sign in'}</roque-button
                    >
                  </div>

                  <p class="mode-switch">
                    ${isRegister
                      ? html`Already have an account?
                          <button @click="${this._toggleMode}">Sign in</button>`
                      : html`New here?
                          <button @click="${this._toggleMode}">Create an account</button>`}
                  </p>

                  ${isRegister
                    ? html`<p class="hint">
                        Passwords need at least 8 characters, one lowercase,
                        one uppercase and one digit.
                      </p>`
                    : ''}
                `}
          </roque-card>
        </div>
      </div>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-login-page': LoginPage
  }
}
