import { LitElement, html } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import './creator-login.ts'
import './creator-gateway-settings.ts'
import './creator-content.ts'
import './creator-subscribers.ts'
import '../components/layouts/tabs.ts'
import { getAccessToken } from '../lib/api'

/**
 * App shell for the creator admin panel (`/settings.html`).
 *
 * Shows the login page until a valid access token is stored, then a tabbed
 * panel: **Settings** (payment gateways + messaging), **Content** (the
 * post/broadcast dashboard) and **Subscribers** (subscriber list + revenue).
 * Logging out (or a 401 from the API) swaps back to login.
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
      ? html`<roque-tabs>
          <roque-gateway-settings
            slot="panel"
            label="Settings"
            @aero-logout="${this._onLogout}"
            @aero-unauthorized="${this._onLogout}"
          ></roque-gateway-settings>
          <roque-content-manager
            slot="panel"
            label="Content"
            @aero-logout="${this._onLogout}"
            @aero-unauthorized="${this._onLogout}"
          ></roque-content-manager>
          <roque-subscribers-manager
            slot="panel"
            label="Subscribers"
            @aero-logout="${this._onLogout}"
            @aero-unauthorized="${this._onLogout}"
          ></roque-subscribers-manager>
        </roque-tabs>`
      : html`<roque-creator-login @aero-login-success="${this._onLogin}"></roque-creator-login>`
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-settings-app': CreatorSettingsApp
  }
}
