import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/buttons/button.ts'
import '../components/inputs/switch.ts'
import '../components/inputs/textarea.ts'
import '../components/layouts/card.ts'
import '../components/feedback/dialog.ts'
import '../components/feedback/toast.ts'
import '../components/feedback/alert.ts'
import '../components/feedback/spinner.ts'
import '../components/data/badge.ts'
import { api, ApiError, clearTokens, getAccessToken } from '../lib/api'
import type { CreatorMedia, CreatorPost } from '../lib/api'

/**
 * Creator content dashboard: every post/broadcast with its engagement stats
 * (views, unlock count), plus edit-caption, delete, and visibility controls.
 * Mobile-first: cards stack full-width on small screens and form a two-column
 * grid on wider ones.
 */
@customElement('roque-content-manager')
export class CreatorContentManager extends LitElement {
  @state() private posts: CreatorPost[] = []
  @state() private loading = true
  @state() private busy = false
  @state() private error = ''
  @state() private toastHeading = ''
  @state() private toast = ''
  @state() private toastVisible = false
  @state() private editing: CreatorPost | null = null
  @state() private editCaption = ''
  @state() private editVisible = true
  @state() private deleting: CreatorPost | null = null

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .page {
      max-width: 980px;
      margin: 0 auto;
      padding: 20px 16px 60px;
    }

    .topbar {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
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

    .stats-bar {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
      margin-bottom: 20px;
    }

    .stat {
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.85) 0%,
        rgba(235, 245, 250, 0.7) 100%
      );
      border: 1px solid rgba(90, 130, 165, 0.35);
      border-radius: 4px;
      padding: 10px 12px;
      text-align: center;
    }

    .stat .value {
      font-size: 20px;
      font-weight: 600;
      color: #1e395b;
    }

    .stat .label {
      font-size: 10px;
      color: #4a5b6e;
      margin-top: 2px;
    }

    .grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 16px;
      align-items: start;
    }

    @media (min-width: 720px) {
      .grid {
        grid-template-columns: repeat(2, 1fr);
      }
    }

    .thumbs {
      display: flex;
      gap: 6px;
      overflow: hidden;
      margin-bottom: 12px;
      border-radius: 4px;
    }

    .thumbs img {
      width: 72px;
      height: 72px;
      object-fit: cover;
      border: 1px solid #c8d4de;
      border-radius: 4px;
      background: #eef3f7;
    }

    .caption {
      margin: 0 0 8px;
      font-size: 14px;
      color: #1e2a38;
      line-height: 1.45;
      white-space: pre-wrap;
      word-break: break-word;
      max-height: 4.4em;
      overflow: hidden;
    }

    .caption.empty {
      color: #8a97a5;
      font-style: italic;
    }

    .badges {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 10px;
    }

    .meta {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }

    .meta .chip {
      font-size: 11px;
      color: #3a5268;
      background: #eef3f8;
      border: 1px solid #d3dde6;
      border-radius: 3px;
      padding: 2px 7px;
    }

    .footer-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      padding-top: 12px;
      border-top: 1px solid #dcdcdc;
    }

    .switch-zone {
      display: flex;
      align-items: center;
    }

    .actions {
      display: flex;
      gap: 8px;
    }

    /* Destructive action: red tint on the button's exposed part. */
    roque-button.danger::part(aero-btn) {
      background: linear-gradient(to bottom, #fdf2f2 0%, #f6c9cc 100%);
      background-color: rgba(220, 90, 90, 0.25);
      outline-color: rgba(160, 40, 40, 0.5);
    }

    roque-button.danger::part(aero-btn):hover {
      background-color: rgba(230, 110, 110, 0.35);
      outline-color: rgba(160, 40, 40, 0.7);
    }

    .edit-body {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .dialog-note {
      font-size: 12px;
      color: #4a5b6e;
      line-height: 1.5;
      margin: 0;
    }

    .empty {
      color: #6b7a8a;
      font-size: 13px;
      padding: 30px;
      text-align: center;
    }

    .error-zone {
      margin-bottom: 16px;
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
      this.posts = await api.getCreatorContent()
    } catch (err) {
      this._handleError(err)
    } finally {
      this.loading = false
    }
  }

  // ------------------------------------------------------------------ #
  // Helpers
  // ------------------------------------------------------------------ #

  private _setPost(updated: CreatorPost) {
    this.posts = this.posts.map((p) => (p.id === updated.id ? updated : p))
  }

  private _thumbUrl(media: CreatorMedia): string {
    const url = media.media_url
    if (!url) return ''
    const token = getAccessToken()
    return token ? `${url}&token=${encodeURIComponent(token)}` : url
  }

  private _price(p: CreatorPost): string {
    return `$${((p.broadcast_price_cents ?? 0) / 100).toFixed(2)}`
  }

  private _date(p: CreatorPost): string {
    return new Date(p.created_at).toLocaleDateString()
  }

  private _toast(heading: string, message: string) {
    this.toastHeading = heading
    this.toast = message
    this.toastVisible = true
    window.setTimeout(() => {
      this.toastVisible = false
    }, 5000)
  }

  private _handleError(err: unknown) {
    if (err instanceof ApiError && err.status === 401) {
      clearTokens()
      this.dispatchEvent(
        new CustomEvent('aero-unauthorized', { bubbles: true, composed: true }),
      )
      return
    }
    this.error = err instanceof Error ? err.message : 'Unexpected error'
    this.toast = ''
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
  // Edit dialog
  // ------------------------------------------------------------------ #

  private _openEdit(p: CreatorPost) {
    this.editing = p
    this.editCaption = p.caption ?? ''
    this.editVisible = p.is_visible
  }

  private _closeEdit() {
    this.editing = null
  }

  private async _saveEdit() {
    if (!this.editing || this.busy) return
    const id = this.editing.id
    const trimmed = this.editCaption.trim()
    this.busy = true
    this.error = ''
    try {
      const updated = await api.updateCreatorPost(id, {
        caption: trimmed === '' ? null : trimmed,
        is_visible: this.editVisible,
      })
      this._setPost(updated)
      this.editing = null
      this._toast(
        'Post updated',
        updated.is_visible
          ? 'Your changes are live for followers.'
          : 'Post saved and hidden from followers.',
      )
    } catch (err) {
      this._handleError(err)
    } finally {
      this.busy = false
    }
  }

  // ------------------------------------------------------------------ #
  // Delete flow
  // ------------------------------------------------------------------ #

  private _askDelete(p: CreatorPost) {
    this.deleting = p
  }

  private _closeDelete() {
    this.deleting = null
  }

  private async _confirmDelete() {
    if (!this.deleting || this.busy) return
    const id = this.deleting.id
    this.busy = true
    this.error = ''
    try {
      await api.deleteCreatorPost(id)
      this.posts = this.posts.filter((p) => p.id !== id)
      this.deleting = null
      this._toast('Post deleted', 'The post and its media were permanently removed.')
    } catch (err) {
      this._handleError(err)
    } finally {
      this.busy = false
    }
  }

  // ------------------------------------------------------------------ #
  // Visibility toggle (immediate save, optimistic with revert)
  // ------------------------------------------------------------------ #

  private async _onVisibilityToggle(e: CustomEvent, p: CreatorPost) {
    if (this.busy) return
    const checked = e.detail?.checked ?? false
    const previous = p.is_visible
    // Busy guards against double-toggles (out-of-order PATCHes) and keeps the
    // switch disabled mid-request; the revert uses the pre-toggle snapshot.
    this.busy = true
    this._setPost({ ...p, is_visible: checked }) // optimistic
    try {
      const updated = await api.updateCreatorPost(p.id, { is_visible: checked })
      this._setPost(updated)
      this._toast(
        'Visibility updated',
        checked
          ? 'This post is visible to followers again.'
          : 'This post is hidden from followers (you can still see it here).',
      )
    } catch (err) {
      this._setPost({ ...p, is_visible: previous })
      this._handleError(err)
    } finally {
      this.busy = false
    }
  }

  // ------------------------------------------------------------------ #
  // Render
  // ------------------------------------------------------------------ #

  render() {
    if (this.loading) {
      return html`<div class="page">
        <roque-card heading="Loading content…">
          <roque-spinner size="28" label="Loading…"></roque-spinner>
        </roque-card>
      </div>`
    }

    const totalViews = this.posts.reduce((sum, p) => sum + p.view_count, 0)
    const totalUnlocks = this.posts.reduce((sum, p) => sum + p.unlock_count, 0)

    return html`
      <div class="page">
        <div class="topbar">
          <div>
            <h1>Content</h1>
            <p>
              Every post and broadcast you've published, with engagement stats.
              Edit captions, hide content from followers, or delete it entirely.
            </p>
          </div>
          <roque-button context="clear" buttonId="content-logout-btn" @aero-click="${this._onLogout}"
            >Sign out</roque-button
          >
        </div>

        <div class="stats-bar">
          <div class="stat">
            <div class="value">${this.posts.length}</div>
            <div class="label">Posts</div>
          </div>
          <div class="stat">
            <div class="value">${totalViews}</div>
            <div class="label">Views</div>
          </div>
          <div class="stat">
            <div class="value">${totalUnlocks}</div>
            <div class="label">Unlocks</div>
          </div>
        </div>

        ${this.error
          ? html`<div class="error-zone">
              <roque-alert
                type="error"
                heading="Update failed"
                message="${this.error}"
                @aero-dismiss="${() => (this.error = '')}"
              ></roque-alert>
            </div>`
          : ''}

        ${this.posts.length === 0
          ? html`<div class="empty">
              No posts yet — publish your first post or broadcast from the app.
            </div>`
          : html`<div class="grid">
              ${this.posts.map((p) => this._renderCard(p))}
            </div>`}
      </div>

      ${this._renderEditDialog()}
      ${this._renderDeleteDialog()}

      <roque-toast
        icon="info"
        heading="${this.toastHeading}"
        message="${this.toast}"
        ?visible="${this.toastVisible}"
      ></roque-toast>
    `
  }

  private _renderCard(p: CreatorPost) {
    return html`
      <roque-card>
        ${p.media.length > 0
          ? html`<div class="thumbs">
              ${p.media.slice(0, 3).map(
                (m) => html`<img
                  src="${this._thumbUrl(m)}"
                  alt=""
                  loading="lazy"
                />`,
              )}
            </div>`
          : ''}

        <p class="caption ${p.caption ? '' : 'empty'}">
          ${p.caption ?? 'No caption'}
        </p>

        <div class="badges">
          ${p.broadcast_price_cents != null
            ? html`<roque-badge context="warning">Paid · ${this._price(p)}</roque-badge>`
            : html`<roque-badge context="default">Free post</roque-badge>`}
          ${!p.is_visible
            ? html`<roque-badge context="error">Hidden</roque-badge>`
            : ''}
          <roque-badge context="info">${this._date(p)}</roque-badge>
        </div>

        <div class="meta">
          <span class="chip">👁 ${p.view_count} views</span>
          ${p.broadcast_price_cents != null
            ? html`<span class="chip">🔓 ${p.unlock_count} unlocks</span>`
            : ''}
          <span class="chip">🖼 ${p.media_count} media</span>
        </div>

        <div class="footer-row">
          <div class="switch-zone">
            <roque-switch
              label="Visible"
              .checked="${p.is_visible}"
              ?disabled="${this.busy}"
              @aero-change="${(e: CustomEvent) => this._onVisibilityToggle(e, p)}"
            ></roque-switch>
          </div>
          <div class="actions">
            <roque-button
              context="submit"
              buttonId="edit-${p.id}"
              @aero-click="${() => this._openEdit(p)}"
              >Edit</roque-button
            >
            <roque-button
              context="clear"
              buttonId="delete-${p.id}"
              class="danger"
              @aero-click="${() => this._askDelete(p)}"
              >Delete</roque-button
            >
          </div>
        </div>
      </roque-card>
    `
  }

  private _renderEditDialog() {
    return html`
      <roque-dialog
        windowTitle="${this.editing ? 'Edit post' : ''}"
        ?open="${this.editing !== null}"
        @aero-cancel="${this._closeEdit}"
      >
        <div class="edit-body">
          <p class="dialog-note">
            Update the caption or change whether this post is visible to your
            followers.
          </p>
          <roque-textarea
            label="Caption"
            rows="4"
            placeholder="What's on your mind?"
            .value="${this.editCaption}"
            @aero-input="${(e: CustomEvent) =>
              (this.editCaption = e.detail?.value ?? '')}"
          ></roque-textarea>
          <roque-switch
            label="Visible to followers"
            .checked="${this.editVisible}"
            @aero-change="${(e: CustomEvent) =>
              (this.editVisible = e.detail?.checked ?? false)}"
          ></roque-switch>
        </div>
        <div slot="actions">
          <roque-button buttonId="edit-cancel" @aero-click="${this._closeEdit}"
            >Cancel</roque-button
          >
          <roque-button
            context="submit"
            buttonId="edit-save"
            @aero-click="${this._saveEdit}"
            >${this.busy ? 'Saving…' : 'Save'}</roque-button
          >
        </div>
      </roque-dialog>
    `
  }

  private _renderDeleteDialog() {
    return html`
      <roque-dialog
        windowTitle="Delete post"
        ?open="${this.deleting !== null}"
        @aero-cancel="${this._closeDelete}"
      >
        <p class="dialog-note">
          Permanently delete this post, its media, and any unlock records?
          This cannot be undone.
        </p>
        <div slot="actions">
          <roque-button buttonId="delete-cancel" @aero-click="${this._closeDelete}"
            >Cancel</roque-button
          >
          <roque-button
            buttonId="delete-confirm"
            class="danger"
            @aero-click="${this._confirmDelete}"
            >${this.busy ? 'Deleting…' : 'Delete'}</roque-button
          >
        </div>
      </roque-dialog>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-content-manager': CreatorContentManager
  }
}
