import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/surfaces/theme-picker.ts'
import './creator-login.ts'
import './creator-gateway-settings.ts'
import './creator-legal-settings.ts'
import './creator-content.ts'
import './creator-subscribers.ts'
import './chat.ts'
import '../components/layouts/tabs.ts'
import { api, clearTokens, getAccessToken } from '../lib/api'

/**
 * App shell for the creator admin panel (`/admin`, alias `/settings.html`).
 *
 * Shows the login page until a valid access token for the creator role is
 * stored, then a tabbed panel: **Settings** (payment gateways + messaging),
 * **Content** (the post/broadcast dashboard) and **Subscribers** (subscriber
 * list + revenue). Non-creator accounts are redirected away. Logging out (or
 * a 401 from the API) swaps back to login.
 */
@customElement('roque-settings-app')
export class CreatorSettingsApp extends LitElement {
  @state() private loggedIn = !!getAccessToken()

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .admin-shell {
      max-width: 960px;
      margin: 0 auto;
      padding: 14px 12px 30px;
      box-sizing: border-box;
    }

    .theme-bar {
      display: flex;
      justify-content: flex-end;
      margin-bottom: 8px;
    }
  `

  connectedCallback() {
    super.connectedCallback()
    void this._verifyRole()
  }

  /** Gate the admin panel to the creator role — everyone else goes home. */
  private async _verifyRole() {
    if (!getAccessToken()) return
    try {
      const me = await api.me()
      if (!me.is_creator) {
        clearTokens()
        window.location.href = '/'
      }
    } catch {
      // Invalid/expired token — request() already cleared it; show login.
      this.loggedIn = false
    }
  }

  private async _onLogin() {
    try {
      const me = await api.me()
      if (!me.is_creator) {
        clearTokens()
        window.location.href = '/'
        return
      }
      this.loggedIn = true
    } catch {
      this.loggedIn = false
    }
  }

  private _onLogout() {
    this.loggedIn = false
  }

  render() {
    return this.loggedIn
      ? html`<div class="admin-shell">
          <div class="theme-bar"><roque-theme-picker></roque-theme-picker></div>
          <roque-tabs>
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
            <roque-legal-settings
              slot="panel"
              label="Legal"
              @aero-logout="${this._onLogout}"
              @aero-unauthorized="${this._onLogout}"
            ></roque-legal-settings>
            <roque-subscribers-manager
              slot="panel"
              label="Subscribers"
              @aero-logout="${this._onLogout}"
              @aero-unauthorized="${this._onLogout}"
            ></roque-subscribers-manager>
            <roque-dm-chat
              embedded
              slot="panel"
              label="Conversations"
              @aero-logout="${this._onLogout}"
              @aero-unauthorized="${this._onLogout}"
            ></roque-dm-chat>
          </roque-tabs>
        </div>`
      : html`<roque-creator-login @aero-login-success="${this._onLogin}"></roque-creator-login>`
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-settings-app': CreatorSettingsApp
  }
}
