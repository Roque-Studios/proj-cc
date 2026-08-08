import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/inputs/textarea.ts'
import '../components/buttons/button.ts'
import '../components/layouts/card.ts'
import '../components/feedback/toast.ts'
import '../components/feedback/spinner.ts'
import { api, ApiError, clearTokens } from '../lib/api'

/**
 * Creator admin tab for the legal documents (Terms of Service + Privacy
 * Policy) shown to subscribers before checkout.
 *
 * The textareas are prefilled with the **effective** documents — the creator's
 * own saved text, or the platform defaults (drafted for AI-generated content)
 * — so a creator can start from the defaults and customize. Saving writes
 * ``CreatorProfile.tos_text`` / ``privacy_text``; blanking a field falls back
 * to the defaults again, so subscribers are never left without a policy.
 */
@customElement('roque-legal-settings')
export class CreatorLegalSettings extends LitElement {
  @state() private tosText = ''
  @state() private privacyText = ''
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
      max-width: 640px;
      line-height: 1.5;
    }

    .desc {
      margin: 0 0 14px;
      font-size: 12px;
      color: #4a5b6e;
      line-height: 1.5;
    }

    .editor {
      margin-bottom: 10px;
    }

    .footer-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-top: 14px;
      padding-top: 14px;
      border-top: 1px solid #dcdcdc;
    }

    .hint {
      font-size: 11px;
      color: #8a97a5;
    }

    .error-zone {
      margin-bottom: 16px;
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
      const profile = await api.getCreatorProfile()
      this.tosText = profile.tos_text ?? ''
      this.privacyText = profile.privacy_text ?? ''
    } catch (err) {
      this._handleError(err)
    } finally {
      this.loading = false
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
  }

  private async _save() {
    if (this.busy) return
    this.busy = true
    this.error = ''
    try {
      await api.updateCreatorProfile({
        tos_text: this.tosText.trim(),
        privacy_text: this.privacyText.trim(),
      })
      this.toastHeading = 'Legal documents saved'
      this.toast =
        'Subscribers now see your Terms of Service and Privacy Policy at checkout.'
      this.toastVisible = true
      window.setTimeout(() => (this.toastVisible = false), 5000)
    } catch (err) {
      this._handleError(err)
    } finally {
      this.busy = false
    }
  }

  render() {
    if (this.loading) {
      return html`<div class="page">
        <roque-card heading="Loading legal documents…">
          <roque-spinner size="28" label="Loading…"></roque-spinner>
        </roque-card>
      </div>`
    }

    return html`
      <div class="page">
        <div class="topbar">
          <h1>Legal documents</h1>
          <p>
            These are the Terms of Service and Privacy Policy shown to
            subscribers before checkout. Your subscribers must confirm they are
            18+ and accept these documents before any payment is taken.
          </p>
        </div>

        ${this.error
          ? html`<div class="error-zone">
              <roque-alert
                type="error"
                heading="Save failed"
                message="${this.error}"
                @aero-dismiss="${() => (this.error = '')}"
              ></roque-alert>
            </div>`
          : ''}

        <roque-card heading="Terms of Service">
          <p class="desc">
            The agreement your subscribers accept before subscribing. The
            current text (shown below) is the platform default drafted for
            AI-generated content — edit it freely. Leaving the field blank
            restores the default.
          </p>
          <div class="editor">
            <roque-textarea
              label="Terms of Service"
              rows="22"
              maxlength="100000"
              placeholder="Paste or write your Terms of Service…"
              .value="${this.tosText}"
              @aero-input="${(e: CustomEvent) =>
                (this.tosText = e.detail?.value ?? '')}"
            ></roque-textarea>
          </div>
          <div class="footer-row">
            <span class="hint"
              >Public at /legal?creator_id=your_id — shown before checkout.</span
            >
            <roque-button
              context="submit"
              buttonId="save-tos"
              ?disabled="${this.busy}"
              @aero-click="${this._save}"
              >${this.busy ? 'Saving…' : 'Save documents'}</roque-button
            >
          </div>
        </roque-card>

        <div style="height: 18px"></div>

        <roque-card heading="Privacy Policy">
          <p class="desc">
            How subscriber data is handled, including AI-generated content,
            payment processors and user rights. Leaving the field blank
            restores the platform default.
          </p>
          <div class="editor">
            <roque-textarea
              label="Privacy Policy"
              rows="22"
              maxlength="100000"
              placeholder="Paste or write your Privacy Policy…"
              .value="${this.privacyText}"
              @aero-input="${(e: CustomEvent) =>
                (this.privacyText = e.detail?.value ?? '')}"
            ></roque-textarea>
          </div>
          <div class="footer-row">
            <span class="hint">
              Saving either document saves both. Blank fields restore the
              platform defaults.
            </span>
            <roque-button
              context="submit"
              buttonId="save-privacy"
              ?disabled="${this.busy}"
              @aero-click="${this._save}"
              >${this.busy ? 'Saving…' : 'Save documents'}</roque-button
            >
          </div>
        </roque-card>
      </div>

      <roque-toast
        icon="info"
        heading="${this.toastHeading}"
        message="${this.toast}"
        ?visible="${this.toastVisible}"
      ></roque-toast>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-legal-settings': CreatorLegalSettings
  }
}
