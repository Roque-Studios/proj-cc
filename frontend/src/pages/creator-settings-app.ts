import { LitElement, html } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import './creator-login.ts'
import './creator-gateway-settings.ts'
import { getAccessToken } from '../lib/api'

/**
 * App shell for the creator gateway-settings page (`/settings.html`).
 *
 * Shows the login page until a valid access token is stored, then the gateway
 * settings view. Logging out (or a 401 from the API) swaps back to login.
 */
@customElement('roque-settings-app')
export class CreatorSettingsApp extends LitElement {
  @state() private loggedIn = !!getAccessToken()

  private _onLogin() {
    this.loggedIn = true
  }

  private _onLogout() {
    this.loggedIn = false
  }

  render() {
    return this.loggedIn
      ? html`<roque-gateway-settings
          @aero-logout="${this._onLogout}"
          @aero-unauthorized="${this._onLogout}"
        ></roque-gateway-settings>`
      : html`<roque-creator-login @aero-login-success="${this._onLogin}"></roque-creator-login>`
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-settings-app': CreatorSettingsApp
  }
}
