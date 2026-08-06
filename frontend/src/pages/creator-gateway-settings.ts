import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/inputs/switch.ts'
import '../components/inputs/text-field.ts'
import '../components/inputs/select.ts'
import '../components/buttons/button.ts'
import '../components/layouts/card.ts'
import '../components/layouts/divider.ts'
import '../components/feedback/toast.ts'
import '../components/feedback/alert.ts'
import '../components/feedback/spinner.ts'
import '../components/data/badge.ts'
import { api, ApiError, clearTokens } from '../lib/api'
import type { GatewaySettings } from '../lib/api'

/** Gateways shown in the settings UI (the mock dev gateway stays backend-only). */
const UI_GATEWAYS = ['stripe', 'paypal', 'wompi']

/**
 * Creator gateway-settings view: toggle which payment gateways subscribers can
 * use and enter the per-gateway credentials. A gateway can only be enabled
 * when its required config is complete (the backend enforces the same rule).
 */
@customElement('roque-gateway-settings')
export class CreatorGatewaySettings extends LitElement {
  @state() private settings: GatewaySettings[] = []
  @state() private form: Record<string, Record<string, string>> = {}
  @state() private enabled: Record<string, boolean> = {}
  @state() private messagingOn = false
  @state() private messagingBusy = false
  @state() private loading = true
  @state() private busy = false
  @state() private error = ''
  @state() private toast = ''
  @state() private toastHeading = ''
  @state() private toastVisible = false

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .page {
      max-width: 980px;
      margin: 0 auto;
      padding: 28px 24px 60px;
    }

    .topbar {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 22px;
    }

    .topbar h1 {
      margin: 0;
      font-size: 22px;
      font-weight: normal;
      color: #1e395b;
    }

    .topbar p {
      margin: 4px 0 0;
      font-size: 12px;
      color: #4a5b6e;
      max-width: 560px;
      line-height: 1.5;
    }

    .summary {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      margin-bottom: 20px;
    }

    .summary-label {
      font-size: 12px;
      color: #4a5b6e;
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 20px;
      align-items: start;
    }

    .desc {
      margin: 0 0 14px;
      font-size: 12px;
      color: #4a5b6e;
      line-height: 1.5;
    }

    .field {
      margin-bottom: 10px;
    }

    .field-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .saved-badge {
      font-size: 10px;
      color: #2a7d2a;
      white-space: nowrap;
    }

    .footer-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid #dcdcdc;
    }

    .switch-zone {
      display: flex;
      align-items: center;
    }

    .switch-zone .switch-hint {
      font-size: 10px;
      color: #b04a1f;
      margin-left: 6px;
    }

    .error-zone {
      margin-top: 16px;
    }

    .empty {
      color: #6b7a8a;
      font-size: 13px;
      padding: 30px;
      text-align: center;
    }
  `

  async connectedCallback() {
    super.connectedCallback()
    await this._load()
  }

  private async _load() {
    this.loading = true
    this.error = ''
    try {
      const [settings, messaging] = await Promise.all([
        api.getGatewaySettings(),
        api.getMessagingSettings(),
      ])
      this.messagingOn = messaging.allow_messages_from_all_followers
      this.settings = settings.filter((g) => UI_GATEWAYS.includes(g.gateway))
      this.form = {}
      this.enabled = {}
      for (const g of this.settings) {
        this.form[g.gateway] = {}
        this.enabled[g.gateway] = g.enabled
        for (const f of g.fields) {
          // Pre-select the first option (e.g. sandbox) for choice fields.
          if (f.options.length > 0) {
            this.form[g.gateway][f.name] = f.options[0]
          }
        }
      }
    } catch (err) {
      this._handleError(err)
    } finally {
      this.loading = false
    }
  }

  private _gateway(name: string): GatewaySettings | undefined {
    return this.settings.find((g) => g.gateway === name)
  }

  private _canEnable(gateway: string): boolean {
    const spec = this._gateway(gateway)
    if (!spec) return false
    const form = this.form[gateway] ?? {}
    return spec.fields.every(
      (f) =>
        !f.required ||
        (form[f.name] ?? '').trim() !== '' ||
        f.configured,
    )
  }

  private _onField(gateway: string, field: string, value: string) {
    this.form = {
      ...this.form,
      [gateway]: { ...this.form[gateway], [field]: value },
    }
  }

  private _onToggle(gateway: string, checked: boolean) {
    if (checked && !this._canEnable(gateway)) {
      this.toast = 'Complete the required fields first, then enable this gateway.'
      this.toastHeading = 'Configuration incomplete'
      this._showToast()
      return
    }
    this.enabled = { ...this.enabled, [gateway]: checked }
  }

  private async _save(gateway: string) {
    if (this.busy) return
    const spec = this._gateway(gateway)
    if (!spec) return
    this.busy = true
    this.error = ''
    try {
      const form = this.form[gateway] ?? {}
      const config: Record<string, string> = {}
      for (const f of spec.fields) {
        const value = (form[f.name] ?? '').trim()
        if (value !== '') config[f.name] = value
      }
      const updated = await api.updateGateway(gateway, {
        enabled: this.enabled[gateway] ?? false,
        config,
      })
      this._applyServerState(updated)
      this.toastHeading = `${spec.label} saved`
      this.toast = updated.enabled
        ? `${spec.label} is now enabled for your subscribers.`
        : `${spec.label} settings updated (gateway disabled).`
      this._showToast()
    } catch (err) {
      this._handleError(err)
    } finally {
      this.busy = false
    }
  }

  /** Reflect the server's authoritative state back into local form/switch state. */
  private _applyServerState(updated: GatewaySettings) {
    const idx = this.settings.findIndex((g) => g.gateway === updated.gateway)
    if (idx === -1) return
    const next = [...this.settings]
    next[idx] = updated
    this.settings = next
    this.enabled = { ...this.enabled, [updated.gateway]: updated.enabled }
  }

  private _handleError(err: unknown) {
    if (err instanceof ApiError && err.status === 401) {
      clearTokens()
      this.dispatchEvent(
        new CustomEvent('aero-unauthorized', {
          bubbles: true,
          composed: true,
        }),
      )
      return
    }
    this.error = err instanceof Error ? err.message : 'Unexpected error'
    this.toast = ''
  }

  private _showToast() {
    // Bound as state so re-renders (e.g. the busy flag flipping after a save)
    // can never reset it mid-display.
    this.toastVisible = true
    window.setTimeout(() => {
      this.toastVisible = false
    }, 5000)
  }

  private async _onMessagingToggle(e: CustomEvent) {
    if (this.messagingBusy) return
    const on = e.detail?.checked ?? false
    const previous = this.messagingOn
    this.messagingOn = on
    this.messagingBusy = true
    try {
      const updated = await api.updateMessagingSettings(on)
      this.messagingOn = updated.allow_messages_from_all_followers
      this.toastHeading = 'Messaging settings saved'
      this.toast = on
        ? 'All followers can now start a conversation with you.'
        : 'New conversations are now limited to existing threads.'
      this._showToast()
    } catch (err) {
      this.messagingOn = previous
      this._handleError(err)
    } finally {
      this.messagingBusy = false
    }
  }

  private _onLogout() {
    const refresh = localStorage.getItem('cc_refresh_token')
    if (refresh) {
      api.logout(refresh).catch(() => undefined)
    }
    clearTokens()
    this.dispatchEvent(
      new CustomEvent('aero-logout', { bubbles: true, composed: true }),
    )
  }

  // ------------------------------------------------------------------ #
  // Render
  // ------------------------------------------------------------------ #

  render() {
    if (this.loading) {
      return html`<div class="page">
        <roque-card heading="Loading gateway settings…">
          <roque-spinner size="28" label="Loading…"></roque-spinner>
        </roque-card>
      </div>`
    }

    const enabledCount = this.settings.filter((g) => this.enabled[g.gateway])
      .length

    return html`
      <div class="page">
        <div class="topbar">
          <div>
            <h1>Payment gateway settings</h1>
            <p>
              Choose which payment providers your subscribers can use at
              checkout, and connect each gateway with its credentials.
              Gateways with an incomplete configuration cannot be enabled.
            </p>
          </div>
          <roque-button context="clear" buttonId="logout-btn" @aero-click="${this._onLogout}"
            >Sign out</roque-button
          >
        </div>

        <div class="summary">
          <span class="summary-label">Active for subscribers:</span>
          ${enabledCount === 0
            ? html`<roque-badge context="warning">none yet</roque-badge>`
            : this.settings
                .filter((g) => this.enabled[g.gateway])
                .map(
                  (g) => html`<roque-badge context="success">${g.label}</roque-badge>`,
                )}
        </div>

        ${this.error
          ? html`<roque-alert
              type="error"
              heading="Update failed"
              message="${this.error}"
              @aero-dismiss="${() => (this.error = '')}"
            ></roque-alert>`
          : ''}

        <roque-card heading="Messaging">
          <p class="desc">
            Control who can start a direct-message conversation with you. When
            off, only followers you already have a conversation with can
            message you — new threads are blocked. Toggling takes effect
            immediately and never interrupts existing conversations.
          </p>
          <div class="footer-row">
            <div class="switch-zone">
              <roque-switch
                label="Allow messages from all followers"
                .checked="${this.messagingOn}"
                ?disabled="${this.messagingBusy}"
                @aero-change="${this._onMessagingToggle}"
              ></roque-switch>
              ${this.messagingBusy
                ? html`<span class="switch-hint">saving…</span>`
                : ''}
            </div>
          </div>
        </roque-card>

        <roque-divider orientation="horizontal" spacing="18"></roque-divider>

        <div class="grid">
          ${this.settings.map((g) => this._renderGatewayCard(g))}
        </div>
      </div>

      <roque-toast
        icon="info"
        heading="${this.toastHeading}"
        message="${this.toast}"
        ?visible="${this.toastVisible}"
      ></roque-toast>
    `
  }

  private _renderGatewayCard(g: GatewaySettings) {
    const form = this.form[g.gateway] ?? {}
    const canEnable = this._canEnable(g.gateway)
    const switchDisabled = !g.enabled && !canEnable

    return html`
      <roque-card heading="${g.label}">
        <p class="desc">${g.description}</p>

        ${g.fields.map(
          (f) => html`
            <div class="field">
              ${f.options.length > 0
                ? html`<roque-select
                    label="${f.label}"
                    .options="${f.options.map((o) => ({ value: o, label: o }))}"
                    .value="${form[f.name] ?? ''}"
                    @aero-change="${(e: CustomEvent) =>
                      this._onField(g.gateway, f.name, e.detail?.value ?? '')}"
                  ></roque-select>`
                : html`<roque-text-field
                    label="${f.label}"
                    placeholder="${f.placeholder || (f.secret ? '••••••••' : '')}"
                    .value="${form[f.name] ?? ''}"
                    @aero-input="${(e: CustomEvent) =>
                      this._onField(g.gateway, f.name, e.detail?.value ?? '')}"
                  ></roque-text-field>`}
              ${f.configured
                ? html`<div class="field-head"><span class="saved-badge">✓ saved</span></div>`
                : ''}
            </div>
          `,
        )}

        <div class="footer-row">
          <div class="switch-zone">
            <roque-switch
              label="Enable for subscribers"
              .checked="${this.enabled[g.gateway] ?? false}"
              ?disabled="${switchDisabled}"
              @aero-change="${(e: CustomEvent) =>
                this._onToggle(g.gateway, e.detail?.checked ?? false)}"
            ></roque-switch>
            ${switchDisabled
              ? html`<span class="switch-hint">requires ${g.label} credentials</span>`
              : ''}
          </div>
          <roque-button
            context="submit"
            buttonId="save-${g.gateway}"
            @aero-click="${() => this._save(g.gateway)}"
            >${this.busy ? 'Saving…' : 'Save'}</roque-button
          >
        </div>
      </roque-card>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-gateway-settings': CreatorGatewaySettings
  }
}
