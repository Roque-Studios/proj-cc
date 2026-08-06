import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/inputs/text-field.ts'
import '../components/buttons/button.ts'
import '../components/layouts/card.ts'
import '../components/feedback/alert.ts'
import { api, ApiError, setTokens } from '../lib/api'

/**
 * Login page for the creator (admin) gateway-settings panel.
 *
 * Authenticates with the API and, on success, stores the token pair and emits
 * `aero-login-success` so the app shell can swap in the settings view.
 */
@customElement('roque-creator-login')
export class CreatorLogin extends LitElement {
  @state() private email = ''
  @state() private password = ''
  @state() private error = ''
  @state() private busy = false

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
      margin-top: 16px;
    }
  `

  private _onEmail(e: CustomEvent) {
    this.email = (e.detail?.value ?? '').trim()
  }

  private _onPassword(e: CustomEvent) {
    this.password = e.detail?.value ?? ''
  }

  private async _submit() {
    if (this.busy) return
    this.error = ''
    if (!this.email || !this.password) {
      this.error = 'Enter your email and password to continue.'
      return
    }
    this.busy = true
    try {
      const tokens = await api.login(this.email, this.password)
      setTokens(tokens.access_token, tokens.refresh_token)
      this.dispatchEvent(
        new CustomEvent('aero-login-success', {
          bubbles: true,
          composed: true,
          detail: { email: this.email },
        }),
      )
    } catch (err) {
      this.error =
        err instanceof ApiError
          ? err.message
          : 'Login failed — please try again.'
    } finally {
      this.busy = false
    }
  }

  render() {
    return html`
      <div class="login-wrap">
        <div class="login-card">
          <div class="brand">
            <h1>Creator Gateway Settings</h1>
            <p>Content Creator Engine — admin panel</p>
          </div>

          <roque-card heading="Sign in">
            <div class="form-row">
              <roque-text-field
                label="Email"
                placeholder="you@creator.io"
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

            ${this.error
              ? html`<roque-alert
                  type="error"
                  heading="Cannot sign in"
                  message="${this.error}"
                  @aero-dismiss="${() => (this.error = '')}"
                ></roque-alert>`
              : ''}

            <div class="actions">
              <roque-button
                context="submit"
                buttonId="login-btn"
                @aero-click="${this._submit}"
                >${this.busy ? 'Signing in…' : 'Sign in'}</roque-button
              >
            </div>

            <p class="hint">
              This page is the private admin entry — it is not linked from
              anywhere and only reachable by visiting this URL directly.
              <br />
              Tip: seed the creator account with
              <code>python -m app.seed_creator</code>.
            </p>
          </roque-card>
        </div>
      </div>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-creator-login': CreatorLogin
  }
}
