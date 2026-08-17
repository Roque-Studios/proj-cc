import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/inputs/switch.ts'
import '../components/inputs/text-field.ts'
import '../components/inputs/textarea.ts'
import '../components/inputs/select.ts'
import '../components/buttons/button.ts'
import '../components/layouts/card.ts'
import '../components/data/avatar.ts'
import '../components/layouts/divider.ts'
import '../components/feedback/toast.ts'
import '../components/feedback/alert.ts'
import '../components/feedback/spinner.ts'
import '../components/data/badge.ts'
import { api, ApiError, clearTokens } from '../lib/api'
import type { GatewayField, GatewaySettings } from '../lib/api'

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
  // Creating the PayPal billing plan (``POST /creator/gateway-settings/paypal/plan``).
  @state() private planBusy = false
  @state() private error = ''
  @state() private toast = ''
  @state() private toastHeading = ''
  @state() private toastVisible = false
  // Public profile: the landing page hero (name/bio/avatar/banner) + social
  // accounts shown on the creator landing page.
  @state() private displayName = ''
  @state() private bio = ''
  @state() private avatarUrl = ''
  @state() private bannerUrl = ''
  @state() private profileBusy = false
  @state() private bannerBusy = false
  @state() private avatarBusy = false
  @state() private socialForm: Record<string, string> = {}
  @state() private socialBusy = false
  // Monthly subscription price (the creator's own tier price in cents).
  @state() private tierPriceCents: number | null = null
  // Dollars-as-typed in the input (parsed to cents on save).
  @state() private tierPriceInput = '5.00'
  @state() private tierPriceBusy = false

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
      color: var(--cc-heading);
    }

    .topbar p {
      margin: 4px 0 0;
      font-size: 12px;
      color: var(--cc-text-secondary);
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
      color: var(--cc-text-secondary);
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
      color: var(--cc-text-secondary);
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
      color: var(--cc-warning-strong);
      margin-left: 6px;
    }

    .error-zone {
      margin-top: 16px;
    }

    .empty {
      color: var(--cc-text-muted);
      font-size: 13px;
      padding: 30px;
      text-align: center;
    }

    .plan-zone {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
      margin: 4px 0 14px;
      padding: 10px 12px;
      border: 1px solid #c8d2dc;
      border-radius: 4px;
      background: rgba(var(--cc-tint), 0.12);
    }

    .plan-hint {
      flex: 1;
      min-width: 180px;
      font-size: 11px;
      color: var(--cc-text-secondary);
      line-height: 1.4;
    }

    /* --- Public profile (landing hero) --- */
    .avatar-zone {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .avatar-row {
      display: flex;
      align-items: center;
      gap: 12px;
    }

    .avatar-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }

    .banner-zone {
      margin-top: 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .banner-preview {
      width: 100%;
      max-height: 140px;
      border: 1px solid #c8d4de;
      border-radius: 4px;
      overflow: hidden;
      background: var(--cc-fill);
    }

    .banner-preview img {
      display: block;
      width: 100%;
      max-height: 140px;
      object-fit: cover;
    }

    .banner-actions {
      display: flex;
      gap: 8px;
      align-items: center;
    }

    roque-button.danger::part(aero-btn) {
      background: linear-gradient(to bottom, #fdf2f2 0%, #f6c9cc 100%);
      background-color: rgba(220, 90, 90, 0.25);
      outline-color: rgba(160, 40, 40, 0.5);
    }
  `

  async connectedCallback() {
    super.connectedCallback()
    await this._load()
  }

  private async _load() {
    this.loading = true
    this.error = ''
    // Gateway settings drive the whole view — load them first and render even
    // if the auxiliary profile/messaging calls below fail, so the gateway
    // cards can never be hidden by an unrelated error. When this fails (e.g.
    // an expired token), bail out: the auxiliary calls would just 401 again.
    try {
      const settings = await api.getGatewaySettings()
      this.settings = settings.filter((g) => UI_GATEWAYS.includes(g.gateway))
      this.form = {}
      this.enabled = {}
      for (const g of this.settings) {
        this.form[g.gateway] = {}
        this.enabled[g.gateway] = g.enabled
        for (const f of g.fields) {
          // Non-secret stored values are echoed by the API (secrets never are)
          // — pre-fill them so a save never silently resets e.g. the
          // environment select. Choice fields default to their first option.
          this.form[g.gateway][f.name] = f.value || f.options[0] || ''
        }
      }
    } catch (err) {
      this._handleError(err)
      this.loading = false
      return
    }
    // Profile + messaging are auxiliary: their failure must not blank the
    // gateway cards (the page stays fully usable for gateway configuration).
    try {
      const [messaging, profile] = await Promise.all([
        api.getMessagingSettings(),
        api.getCreatorProfile(),
      ])
      this.messagingOn = messaging.allow_messages_from_all_followers
      this.displayName = profile.display_name ?? ''
      this.bio = profile.bio ?? ''
      this.avatarUrl = profile.avatar_url ?? ''
      this.bannerUrl = profile.banner_url ?? ''
      this.socialForm = {
        twitter: profile.social_links?.twitter ?? '',
        instagram: profile.social_links?.instagram ?? '',
        tiktok: profile.social_links?.tiktok ?? '',
        other: profile.social_links?.other ?? '',
      }
      this.tierPriceCents = profile.tier_price_cents
      this.tierPriceInput = ((profile.tier_price_cents ?? 500) / 100).toFixed(2)
    } catch (err) {
      this._handleError(err)
    } finally {
      this.loading = false
    }
  }

  private _gateway(name: string): GatewaySettings | undefined {
    return this.settings.find((g) => g.gateway === name)
  }

  private _paypalPlanField(): GatewayField | undefined {
    return this._gateway('paypal')?.fields.find((f) => f.name === 'plan_id')
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
    // Echo non-secret stored values (e.g. the environment select, the PayPal
    // plan id created via the button) into the form so a save/plan-creation
    // never leaves the field showing a stale blank.
    const form = { ...(this.form[updated.gateway] ?? {}) }
    for (const f of updated.fields) {
      if (!f.secret && f.value != null) form[f.name] = f.value
    }
    this.form = { ...this.form, [updated.gateway]: form }
  }

  private async _createPaypalPlan() {
    if (this.planBusy) return
    this.planBusy = true
    this.error = ''
    try {
      const updated = await api.createGatewayPlan('paypal')
      this._applyServerState(updated)
      const planId = this._paypalPlanField()?.value
      this.toastHeading = 'Billing plan created'
      this.toast = planId
        ? `PayPal plan ${planId} is saved — checkout is ready.`
        : 'The PayPal billing plan was created and saved to your config.'
      this._showToast()
    } catch (err) {
      this._handleError(err)
    } finally {
      this.planBusy = false
    }
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

  private async _saveProfile() {
    if (this.profileBusy) return
    this.profileBusy = true
    this.error = ''
    try {
      // The avatar is server-managed via its own upload endpoint — it is not
      // sent here, so an edit never stomps the uploaded file.
      const updated = await api.updateCreatorProfile({
        display_name: this.displayName.trim() || null,
        bio: this.bio.trim() || null,
      })
      this.bannerUrl = updated.banner_url ?? ''
      this.avatarUrl = updated.avatar_url ?? ''
      this.toastHeading = 'Profile saved'
      this.toast = 'Your public profile is updated on the landing page hero.'
      this._showToast()
    } catch (err) {
      this._handleError(err)
    } finally {
      this.profileBusy = false
    }
  }

  private _pickAvatar() {
    if (this.avatarBusy) return
    const input = this.shadowRoot?.querySelector<HTMLInputElement>('#avatar-file')
    input?.click()
  }

  private async _onAvatarPicked(e: Event) {
    if (this.avatarBusy) return
    const input = e.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = '' // allow re-picking the same file
    if (!file) return
    this.avatarBusy = true
    this.error = ''
    try {
      const updated = await api.uploadCreatorAvatar(file)
      this.avatarUrl = updated.avatar_url ?? ''
      this.toastHeading = 'Avatar uploaded'
      this.toast = 'Your new avatar is live on your public landing page.'
      this._showToast()
    } catch (err) {
      this._handleError(err)
    } finally {
      this.avatarBusy = false
    }
  }

  private async _removeAvatar() {
    if (this.avatarBusy) return
    this.avatarBusy = true
    this.error = ''
    try {
      const updated = await api.deleteCreatorAvatar()
      this.avatarUrl = updated.avatar_url ?? ''
      this.toastHeading = 'Avatar removed'
      this.toast = 'Your landing page now uses the initial-letter fallback.'
      this._showToast()
    } catch (err) {
      this._handleError(err)
    } finally {
      this.avatarBusy = false
    }
  }

  private _pickBanner() {
    if (this.bannerBusy) return
    const input = this.shadowRoot?.querySelector<HTMLInputElement>('#banner-file')
    input?.click()
  }

  private async _onBannerPicked(e: Event) {
    if (this.bannerBusy) return
    const input = e.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = '' // allow re-picking the same file
    if (!file) return
    this.bannerBusy = true
    this.error = ''
    try {
      const updated = await api.uploadCreatorBanner(file)
      this.bannerUrl = updated.banner_url ?? ''
      this.toastHeading = 'Banner uploaded'
      this.toast = 'Your new banner is live on your public landing page.'
      this._showToast()
    } catch (err) {
      this._handleError(err)
    } finally {
      this.bannerBusy = false
    }
  }

  private async _removeBanner() {
    if (this.bannerBusy) return
    this.bannerBusy = true
    this.error = ''
    try {
      const updated = await api.deleteCreatorBanner()
      this.bannerUrl = updated.banner_url ?? ''
      this.toastHeading = 'Banner removed'
      this.toast = 'Your landing page now uses the default gradient banner.'
      this._showToast()
    } catch (err) {
      this._handleError(err)
    } finally {
      this.bannerBusy = false
    }
  }

  private _onSocialField(platform: string, value: string) {
    this.socialForm = { ...this.socialForm, [platform]: value }
  }

  private async _saveTierPrice() {
    if (this.tierPriceBusy) return
    // Dollars -> cents, accepting "5", "5.5", "5.99"… but nothing below $1
    // or above $10,000 (the backend enforces the same bounds).
    const dollars = Number(this.tierPriceInput)
    if (!Number.isFinite(dollars) || dollars < 1 || dollars > 10_000) {
      this.toastHeading = 'Invalid subscription price'
      this.toast = 'Enter a monthly price between $1.00 and $10,000.00.'
      this._showToast()
      return
    }
    const cents = Math.round(dollars * 100)
    this.tierPriceBusy = true
    this.error = ''
    try {
      const updated = await api.updateCreatorProfile({ tier_price_cents: cents })
      this.tierPriceCents = updated.tier_price_cents
      this.tierPriceInput = ((updated.tier_price_cents ?? 500) / 100).toFixed(2)
      this.toastHeading = 'Subscription price saved'
      this.toast = `New subscribers will be charged $${this.tierPriceInput}/month.`
      this._showToast()
    } catch (err) {
      this._handleError(err)
    } finally {
      this.tierPriceBusy = false
    }
  }

  private _resetTierPrice() {
    this.tierPriceInput = ((this.tierPriceCents ?? 500) / 100).toFixed(2)
  }

  private async _saveSocialLinks() {
    if (this.socialBusy) return
    this.socialBusy = true
    try {
      const social_links: Record<string, string> = {}
      for (const [platform, value] of Object.entries(this.socialForm)) {
        if (value.trim()) social_links[platform] = value.trim()
      }
      await api.updateCreatorProfile({ social_links })
      this.toastHeading = 'Profile saved'
      this.toast = 'Your social accounts are now shown on your public landing page.'
      this._showToast()
    } catch (err) {
      this._handleError(err)
    } finally {
      this.socialBusy = false
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

        <roque-card heading="Public profile">
          <p class="desc">
            This is the hero of your public landing page
            (<code>/creator/{id}</code>) — the name, online dot, post count,
            bio and banner are shown to every visitor before they subscribe.
          </p>
          <div style="margin-bottom: 10px">
            <roque-text-field
              label="Display name"
              placeholder="Your name"
              .value="${this.displayName}"
              @aero-input="${(e: CustomEvent) =>
                (this.displayName = e.detail?.value ?? '')}"
            ></roque-text-field>
          </div>
          <div style="margin-bottom: 10px">
            <roque-textarea
              label="Bio"
              rows="3"
              placeholder="Tell visitors who you are"
              .value="${this.bio}"
              @aero-input="${(e: CustomEvent) => (this.bio = e.detail?.value ?? '')}"
            ></roque-textarea>
          </div>

          <div class="avatar-zone">
            <div class="avatar-row">
              <roque-avatar
                src="${this.avatarUrl}"
                alt="${this.displayName || 'Avatar'}"
                size="64"
              ></roque-avatar>
              <div class="avatar-actions">
                <roque-button
                  buttonId="pick-avatar"
                  ?disabled="${this.avatarBusy}"
                  @aero-click="${this._pickAvatar}"
                  >${this.avatarBusy
                    ? 'Uploading…'
                    : this.avatarUrl
                      ? 'Replace avatar'
                      : 'Upload avatar'}</roque-button
                >
                ${this.avatarUrl
                  ? html`<roque-button
                      buttonId="remove-avatar"
                      class="danger"
                      ?disabled="${this.avatarBusy}"
                      @aero-click="${this._removeAvatar}"
                      >Remove</roque-button
                    >`
                  : ''}
              </div>
            </div>
            <p class="desc">
              Your profile picture on the landing page hero. JPG · PNG · WEBP ·
              GIF.
            </p>
            <input
              id="avatar-file"
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              hidden
              @change="${this._onAvatarPicked}"
            />
          </div>

          <div class="banner-zone">
            ${this.bannerUrl
              ? html`<div class="banner-preview">
                  <img src="${this.bannerUrl}" alt="Banner preview" />
                </div>`
              : html`<p class="desc">No banner yet — upload one for your hero.</p>`}
            <div class="banner-actions">
              <roque-button
                buttonId="pick-banner"
                ?disabled="${this.bannerBusy}"
                @aero-click="${this._pickBanner}"
                >${this.bannerBusy
                  ? 'Uploading…'
                  : this.bannerUrl
                    ? 'Replace banner'
                    : 'Upload banner'}</roque-button
              >
              ${this.bannerUrl
                ? html`<roque-button
                    buttonId="remove-banner"
                    class="danger"
                    ?disabled="${this.bannerBusy}"
                    @aero-click="${this._removeBanner}"
                    >Remove</roque-button
                  >`
                : ''}
            </div>
            <input
              id="banner-file"
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              hidden
              @change="${this._onBannerPicked}"
            />
          </div>

          <div class="footer-row">
            <span class="summary-label">Shown on the landing page hero.</span>
            <roque-button
              context="submit"
              buttonId="save-profile"
              ?disabled="${this.profileBusy}"
              @aero-click="${this._saveProfile}"
              >${this.profileBusy ? 'Saving…' : 'Save profile'}</roque-button
            >
          </div>
        </roque-card>

        <roque-card heading="Social links">
          <p class="desc">
            These accounts are shown under your profile for every visitor.
            Leave a field empty to hide that platform.
          </p>
          <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px">
            ${(['twitter', 'instagram', 'tiktok', 'other'] as const).map(
              (platform) => html`
                <roque-text-field
                  label="${platform === 'other' ? 'Website / other' : platform[0].toUpperCase() + platform.slice(1)}"
                  placeholder="${platform === 'other' ? 'https://…' : '@handle'}"
                  .value="${this.socialForm[platform] ?? ''}"
                  @aero-input="${(e: CustomEvent) =>
                    this._onSocialField(platform, e.detail?.value ?? '')}"
                ></roque-text-field>
              `,
            )}
          </div>
          <div class="footer-row">
            <span class="summary-label">Shown on the landing page.</span>
            <roque-button
              context="submit"
              buttonId="save-social"
              ?disabled="${this.socialBusy}"
              @aero-click="${this._saveSocialLinks}"
              >${this.socialBusy ? 'Saving…' : 'Save links'}</roque-button
            >
          </div>
        </roque-card>

        <roque-divider orientation="horizontal" spacing="18"></roque-divider>

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

        <roque-card heading="Subscription price">
          <p class="desc">
            The monthly price subscribers pay to follow you. This replaces the
            platform default ($${((this.tierPriceCents ?? 500) / 100).toFixed(2)}) and is
            charged on the hosted checkout — the amount a subscriber agreed to
            is kept on their subscription for renewals and your revenue report.
          </p>
          <div style="display: flex; align-items: flex-end; gap: 10px; flex-wrap: wrap">
            <div style="flex: 1; min-width: 200px; max-width: 260px">
              <roque-text-field
                label="Monthly price (USD)"
                type="number"
                .value="${this.tierPriceInput}"
                @aero-input="${(e: CustomEvent) =>
                  (this.tierPriceInput = e.detail?.value ?? '')}"
              ></roque-text-field>
            </div>
            <div style="display: flex; gap: 8px; align-items: center">
              <roque-button
                context="submit"
                buttonId="save-tier-price"
                ?disabled="${this.tierPriceBusy}"
                @aero-click="${this._saveTierPrice}"
                >${this.tierPriceBusy
                  ? 'Saving…'
                  : this.tierPriceCents
                    ? 'Update price'
                    : 'Set price'}</roque-button
              >
              ${this.tierPriceCents
                ? html`<roque-button
                    buttonId="reset-tier-price"
                    context="clear"
                    ?disabled="${this.tierPriceBusy}"
                    @aero-click="${this._resetTierPrice}"
                    >Reset</roque-button
                  >`
                : ''}
            </div>
          </div>
          <p class="desc" style="margin-top: 10px">
            <span style="color: var(--cc-text-faint)">
              Note: Stripe and PayPal bill through their gateway plan — for
              those, configure a matching plan price in the gateway dashboard.
              Wompi payment links and the mock gateway charge this exact
              amount.
            </span>
          </p>
        </roque-card>

        <div style="height: 18px"></div>

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
    const hasSavedCredentials = g.fields.some((f) => f.configured)

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
                    type="${f.secret ? 'password' : 'text'}"
                    placeholder="${f.placeholder || (f.secret ? '••••••••' : '')}"
                    autocomplete="${f.secret ? 'new-password' : ''}"
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

        ${hasSavedCredentials
          ? html`<p class="desc">
              Values marked ✓ are saved. Leave a field blank to keep its
              current value; enter a new one to replace it.
            </p>`
          : ''}

        ${g.gateway === 'paypal' &&
        canEnable &&
        !!this._paypalPlanField() &&
        !this._paypalPlanField()!.configured
          ? html`<div class="plan-zone">
              <roque-button
                buttonId="create-paypal-plan"
                context="submit"
                ?disabled="${this.planBusy}"
                @aero-click="${this._createPaypalPlan}"
                >${this.planBusy
                  ? 'Creating…'
                  : 'Create billing plan'}</roque-button
              >
              <span class="plan-hint">
                Creates the monthly P-... billing plan in your PayPal account
                and saves it here automatically, priced at your subscription
                price.
              </span>
            </div>`
          : ''}

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
