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
import type { CreatorMedia, CreatorPost, Story } from '../lib/api'

/**
 * Creator content dashboard: every post/broadcast with its engagement stats
 * (views, unlock count), plus publishing (caption + photos, optional paid
 * broadcast price), edit-caption, delete, and visibility controls.
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
  @state() private composing = false
  @state() private composeCaption = ''
  @state() private composePicks: { file: File; preview: string }[] = []
  @state() private composePaid = false
  @state() private composePrice = ''
  @state() private composeError = ''
  @state() private publishing = false
  // --- 24-hour stories ---
  @state() private stories: Story[] = []
  @state() private storiesLoading = true
  @state() private storyComposing = false
  @state() private storyCaption = ''
  @state() private storyPicks: { file: File; preview: string }[] = []
  @state() private storyError = ''
  @state() private storyPublishing = false
  @state() private storyDeleting: number | null = null
  /** Countdown refresh tick (seconds) so live expiry labels stay current. */
  @state() private storyTick = 0
  private _storyTickTimer: number | null = null

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

    .topbar-actions {
      display: flex;
      gap: 8px;
      flex-shrink: 0;
      flex-wrap: wrap;
      justify-content: flex-end;
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

    /* --- Publish composer --- */
    .composer {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .upload-row {
      display: flex;
    }

    .file-picker {
      flex: 1;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
      padding: 18px 12px;
      text-align: center;
      color: #1e395b;
      background: #f6fafc;
      border: 1px dashed #9db8cc;
      border-radius: 4px;
      cursor: pointer;
      transition: border-color 0.2s ease, background 0.2s ease;
    }

    .file-picker:hover {
      border-color: #3c7fb1;
      background: #eaf4fb;
    }

    .file-hint {
      font-size: 11px;
      color: #6b7a8a;
    }

    .picks {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding-bottom: 4px;
    }

    .pick {
      position: relative;
      width: 84px;
      height: 84px;
    }

    .pick img {
      width: 100%;
      height: 100%;
      object-fit: cover;
      border: 1px solid #c8d4de;
      border-radius: 4px;
      background: #eef3f7;
    }

    .pick-remove {
      position: absolute;
      top: -6px;
      right: -6px;
      width: 20px;
      height: 20px;
      line-height: 18px;
      padding: 0;
      font-size: 14px;
      color: #fff;
      background: #b03a3a;
      border: 1px solid #7a1d12;
      border-radius: 50%;
      cursor: pointer;
      box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
    }

    .pick-remove:hover {
      background: #d14646;
    }

    .pick-remove:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .empty-cta {
      margin-top: 12px;
    }

    /* --- 24-hour story section --- */
    .stories-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }

    .stories-title {
      margin: 0;
      font-size: 16px;
      font-weight: 600;
      color: #1e395b;
    }

    .stories-sub {
      margin: 3px 0 0;
      font-size: 12px;
      color: #5a6a7a;
      line-height: 1.5;
      max-width: 480px;
    }

    .stories-empty {
      padding: 14px;
      text-align: center;
      color: #6b7a8a;
      font-size: 12px;
      border: 1px dashed #c8d4de;
      border-radius: 4px;
      background: #f8fbfd;
    }

    .stories-row {
      display: flex;
      gap: 12px;
      overflow-x: auto;
      padding-bottom: 4px;
    }

    .story-tile {
      flex: 0 0 150px;
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .story-thumb {
      width: 150px;
      height: 150px;
      object-fit: cover;
      border: 1px solid #2eb82e;
      border-radius: 6px;
      background: #eef3f7;
      box-shadow: 0 0 0 2px #fff, 0 0 0 3px rgba(46, 184, 46, 0.6);
    }

    .story-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 6px;
    }

    .story-time {
      font-size: 11px;
      color: #2e6b2e;
      font-weight: 600;
      white-space: nowrap;
    }
  `

  async connectedCallback() {
    super.connectedCallback()
    await this._load()
    await this._loadStories()
    // Refresh the stories' live countdown every 30s (and force a render tick).
    this._storyTickTimer = window.setInterval(() => {
      this.storyTick += 1
    }, 30_000)
  }

  disconnectedCallback() {
    super.disconnectedCallback()
    if (this._storyTickTimer !== null) {
      window.clearInterval(this._storyTickTimer)
      this._storyTickTimer = null
    }
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

  // ------------------------------------------------------------------ #
  // Stories
  // ------------------------------------------------------------------ #

  private async _loadStories() {
    this.storiesLoading = true
    try {
      this.stories = await api.getCreatorOwnStories()
    } catch (err) {
      this._handleError(err)
    } finally {
      this.storiesLoading = false
    }
  }

  private _openStoryComposer() {
    this.storyCaption = ''
    this.storyError = ''
    this._releaseStoryPicks()
    this.storyPicks = []
    this.storyComposing = true
  }

  private _closeStoryComposer() {
    if (this.storyPublishing) return
    this.storyComposing = false
    this._releaseStoryPicks()
    this.storyPicks = []
    this.storyError = ''
  }

  private _releaseStoryPicks() {
    for (const pick of this.storyPicks) {
      URL.revokeObjectURL(pick.preview)
    }
  }

  private _onStoryFilesPicked(e: Event) {
    if (this.storyPublishing) return
    const input = e.target as HTMLInputElement
    const files = Array.from(input.files ?? [])
    input.value = '' // allow re-picking the same file
    if (files.length === 0) return
    this.storyPicks = [
      ...this.storyPicks,
      ...files.map((file) => ({ file, preview: URL.createObjectURL(file) })),
    ]
    this.storyError = ''
  }

  private _removeStoryPick(index: number) {
    URL.revokeObjectURL(this.storyPicks[index].preview)
    this.storyPicks = this.storyPicks.filter((_, i) => i !== index)
  }

  private async _publishStory() {
    if (this.storyPublishing) return
    this.storyError = ''
    if (this.storyPicks.length === 0) {
      this.storyError = 'Add at least one photo to publish a story.'
      return
    }
    const oversized = this.storyPicks.find((p) => p.file.size > 10 * 1024 * 1024)
    if (oversized) {
      this.storyError = `${oversized.file.name} is larger than the 10 MB per-file limit.`
      return
    }

    this.storyPublishing = true
    try {
      const created = await api.createStory(
        this.storyPicks.map((p) => p.file),
        this.storyCaption,
      )
      this.storyComposing = false
      this._releaseStoryPicks()
      this.storyPicks = []
      this.storyCaption = ''
      this.stories = [created, ...this.stories]
      this._toast(
        'Story published',
        "It's live for your subscribers and disappears in 24 hours.",
      )
    } catch (err) {
      if (err instanceof ApiError && err.status === 413) {
        this.storyError = 'One of the files exceeds the 10 MB size limit.'
      } else if (err instanceof ApiError && err.status === 401) {
        this._handleError(err)
      } else {
        this.storyError =
          err instanceof Error ? err.message : 'Could not publish — please try again.'
      }
    } finally {
      this.storyPublishing = false
    }
  }

  private async _deleteStory(story: Story) {
    if (this.storyDeleting !== null) return
    this.storyDeleting = story.id
    this.error = ''
    try {
      await api.deleteStory(story.id)
      this.stories = this.stories.filter((s) => s.id !== story.id)
      this._toast('Story deleted', 'It is no longer visible to subscribers.')
    } catch (err) {
      this._handleError(err)
    } finally {
      this.storyDeleting = null
    }
  }

  private _storyLivesLeft(story: Story): string {
    const ms = new Date(story.expires_at).getTime() - Date.now()
    if (ms <= 0) return 'expired'
    const hours = Math.floor(ms / 3_600_000)
    const minutes = Math.floor((ms % 3_600_000) / 60_000)
    if (hours > 0) return `${hours}h ${minutes}m left`
    return `${Math.max(minutes, 1)}m left`
  }

  private _storyThumb(story: Story): string {
    const url = story.media[0]?.media_url
    if (!url) return ''
    const token = getAccessToken()
    return token ? `${url}&token=${encodeURIComponent(token)}` : url
  }

  private _renderStoryComposer() {
    return html`
      <roque-dialog
        windowTitle="Publish a 24-hour story"
        ?open="${this.storyComposing}"
        @aero-cancel="${this._closeStoryComposer}"
      >
        <div class="composer">
          <p class="dialog-note">
            Stories appear at the top of your page for subscribers and vanish
            after 24 hours. Add a photo (or a few) and an optional caption.
          </p>

          <roque-textarea
            label="Caption (optional)"
            rows="2"
            placeholder="A glimpse into your day…"
            .value="${this.storyCaption}"
            @aero-input="${(e: CustomEvent) =>
              (this.storyCaption = e.detail?.value ?? '')}"
          ></roque-textarea>

          <div class="upload-row">
            <label class="file-picker" for="story-files">
              <span>＋ Add photos</span>
              <span class="file-hint">JPG · PNG · WEBP · GIF — up to 10 MB each</span>
            </label>
            <input
              id="story-files"
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              multiple
              hidden
              @change="${this._onStoryFilesPicked}"
            />
          </div>

          ${this.storyPicks.length > 0
            ? html`<div class="picks">
                ${this.storyPicks.map(
                  (pick, i) => html`
                    <div class="pick">
                      <img src="${pick.preview}" alt="" />
                      <button
                        class="pick-remove"
                        aria-label="Remove photo"
                        ?disabled="${this.storyPublishing}"
                        @click="${() => this._removeStoryPick(i)}"
                      >×</button>
                    </div>
                  `,
                )}
              </div>`
            : ''}

          ${this.storyError
            ? html`<roque-alert
                type="error"
                heading="Cannot publish"
                message="${this.storyError}"
                @aero-dismiss="${() => (this.storyError = '')}"
              ></roque-alert>`
            : ''}
        </div>
        <div slot="actions">
          <roque-button
            buttonId="story-cancel"
            @aero-click="${this._closeStoryComposer}"
            >Cancel</roque-button
          >
          <roque-button
            context="submit"
            buttonId="story-publish"
            @aero-click="${this._publishStory}"
            >${this.storyPublishing ? 'Publishing…' : 'Publish story'}</roque-button
          >
        </div>
      </roque-dialog>
    `
  }

  private _renderStoriesSection() {
    return html`
      <roque-card>
        <div class="stories-head">
          <div>
            <h2 class="stories-title">24-hour story</h2>
            <p class="stories-sub">
              A photo (or a few) that appears at the top of your page for
              subscribers — it auto-expires after 24 hours.
            </p>
          </div>
          <roque-button
            context="submit"
            buttonId="new-story-btn"
            @aero-click="${this._openStoryComposer}"
            >Publish story</roque-button
          >
        </div>

        ${this.storiesLoading
          ? html`<div class="stories-empty">
              <roque-spinner size="20" label="Loading…"></roque-spinner>
            </div>`
          : this.stories.length === 0
            ? html`<div class="stories-empty">
                No active story right now. Publish one to show the green
                story ring on your page.
              </div>`
            : html`<div class="stories-row">
                ${this.stories.map(
                  (story) => html`
                    <div class="story-tile">
                      <img
                        class="story-thumb"
                        src="${this._storyThumb(story)}"
                        alt=""
                      />
                      <div class="story-meta">
                        <span class="story-time">${this._storyLivesLeft(story)}</span>
                        <roque-button
                          context="clear"
                          buttonId="delete-story-${story.id}"
                          class="danger"
                          ?disabled="${this.storyDeleting === story.id}"
                          @aero-click="${() => this._deleteStory(story)}"
                          >${this.storyDeleting === story.id ? 'Deleting…' : 'Delete'}</roque-button
                        >
                      </div>
                    </div>
                  `,
                )}
              </div>`}
      </roque-card>
    `
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
  // Publish composer
  // ------------------------------------------------------------------ #

  private _openComposer() {
    this.composeCaption = ''
    this.composePaid = false
    this.composePrice = ''
    this.composeError = ''
    this._releaseComposePicks()
    this.composePicks = []
    this.composing = true
  }

  private _closeComposer() {
    if (this.publishing) return
    this.composing = false
    this._releaseComposePicks()
    this.composePicks = []
    this.composeError = ''
  }

  private _releaseComposePicks() {
    for (const pick of this.composePicks) {
      URL.revokeObjectURL(pick.preview)
    }
  }

  private _onFilesPicked(e: Event) {
    if (this.publishing) return
    const input = e.target as HTMLInputElement
    const files = Array.from(input.files ?? [])
    input.value = '' // allow re-picking the same file
    if (files.length === 0) return
    const picks = files.map((file) => ({
      file,
      preview: URL.createObjectURL(file),
    }))
    this.composePicks = [...this.composePicks, ...picks]
    this.composeError = ''
  }

  private _removePick(index: number) {
    URL.revokeObjectURL(this.composePicks[index].preview)
    this.composePicks = this.composePicks.filter((_, i) => i !== index)
  }

  private _onComposeCaption(e: CustomEvent) {
    this.composeCaption = e.detail?.value ?? ''
  }

  private _onComposePaid(e: CustomEvent) {
    this.composePaid = e.detail?.checked ?? false
    this.composeError = ''
  }

  private _onComposePrice(e: CustomEvent) {
    this.composePrice = e.detail?.value ?? ''
    this.composeError = ''
  }

  private _priceCents(): number | null {
    if (!this.composePaid) return null
    const value = Number(this.composePrice)
    if (!Number.isFinite(value) || value <= 0) return null
    return Math.round(value * 100)
  }

  private async _publish() {
    if (this.publishing) return
    this.composeError = ''
    if (this.composePicks.length === 0) {
      this.composeError = 'Add at least one photo to publish a post.'
      return
    }
    const priceCents = this._priceCents()
    if (this.composePaid && priceCents == null) {
      this.composeError = 'Enter a valid unlock price, e.g. 4.99.'
      return
    }
    // Mirror the backend's price cap (price_cents <= 100000) so a too-large
    // price fails with a friendly message, not a generic 422.
    if (priceCents != null && priceCents > 100_000) {
      this.composeError = 'The unlock price cannot exceed $1,000.'
      return
    }
    // Mirror the backend's per-file cap (MAX_MEDIA_SIZE_BYTES) with a friendly
    // message before the upload starts.
    const oversized = this.composePicks.find((p) => p.file.size > 10 * 1024 * 1024)
    if (oversized) {
      this.composeError = `${oversized.file.name} is larger than the 10 MB per-file limit.`
      return
    }

    this.publishing = true
    try {
      const created = await api.createCreatorPost(
        this.composePicks.map((p) => p.file),
        this.composeCaption,
        priceCents ?? undefined,
      )
      this.composing = false
      this._releaseComposePicks()
      this.composePicks = []
      this.composeCaption = ''
      this.composePaid = false
      this.composePrice = ''
      this.posts = [created, ...this.posts]
      this._toast(
        created.broadcast_price_cents != null
          ? 'Broadcast published'
          : 'Post published',
        created.broadcast_price_cents != null
          ? 'Subscribers can now unlock it for a one-time price.'
          : 'Your post is live for followers.',
      )
    } catch (err) {
      if (err instanceof ApiError && err.status === 413) {
        this.composeError = 'One of the files exceeds the 10 MB size limit.'
      } else if (err instanceof ApiError && err.status === 401) {
        this._handleError(err)
      } else {
        this.composeError =
          err instanceof Error ? err.message : 'Could not publish — please try again.'
      }
    } finally {
      this.publishing = false
    }
  }

  private _renderComposer() {
    return html`
      <roque-dialog
        windowTitle="Publish a post"
        ?open="${this.composing}"
        @aero-cancel="${this._closeComposer}"
      >
        <div class="composer">
          <p class="dialog-note">
            Share photos with your followers. Media is watermarked per viewer;
            set a price to publish a paid broadcast.
          </p>

          <roque-textarea
            label="Caption"
            rows="3"
            placeholder="What are you sharing?"
            .value="${this.composeCaption}"
            @aero-input="${this._onComposeCaption}"
          ></roque-textarea>

          <div class="upload-row">
            <label class="file-picker" for="composer-files">
              <span>＋ Add photos</span>
              <span class="file-hint">JPG · PNG · WEBP · GIF — up to 10 MB each</span>
            </label>
            <input
              id="composer-files"
              type="file"
              accept="image/jpeg,image/png,image/webp,image/gif"
              multiple
              hidden
              @change="${this._onFilesPicked}"
            />
          </div>

          ${this.composePicks.length > 0
            ? html`<div class="picks">
                ${this.composePicks.map(
                  (pick, i) => html`
                    <div class="pick">
                      <img src="${pick.preview}" alt="" />
                      <button
                        class="pick-remove"
                        aria-label="Remove photo"
                        ?disabled="${this.publishing}"
                        @click="${() => this._removePick(i)}"
                      >×</button>
                    </div>
                  `,
                )}
              </div>`
            : ''}

          <roque-switch
            label="Paid broadcast — one-time unlock price"
            .checked="${this.composePaid}"
            ?disabled="${this.publishing}"
            @aero-change="${this._onComposePaid}"
          ></roque-switch>

          ${this.composePaid
            ? html`<roque-text-field
                type="number"
                label="Unlock price (USD)"
                placeholder="4.99"
                .value="${this.composePrice}"
                ?disabled="${this.publishing}"
                @aero-input="${this._onComposePrice}"
              ></roque-text-field>`
            : ''}

          ${this.composeError
            ? html`<roque-alert
                type="error"
                heading="Cannot publish"
                message="${this.composeError}"
                @aero-dismiss="${() => (this.composeError = '')}"
              ></roque-alert>`
            : ''}
        </div>
        <div slot="actions">
          <roque-button buttonId="compose-cancel" @aero-click="${this._closeComposer}"
            >Cancel</roque-button
          >
          <roque-button
            context="submit"
            buttonId="compose-publish"
            @aero-click="${this._publish}"
            >${this.publishing ? 'Publishing…' : 'Publish'}</roque-button
          >
        </div>
      </roque-dialog>
    `
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
              Publish new posts, and manage what you've shared — captions,
              visibility, paid broadcasts, or deleting it entirely.
            </p>
          </div>
          <div class="topbar-actions">
            <roque-button
              context="submit"
              buttonId="new-post-btn"
              @aero-click="${this._openComposer}"
              >Publish post</roque-button
            >
            <roque-button context="clear" buttonId="content-logout-btn" @aero-click="${this._onLogout}"
              >Sign out</roque-button
            >
          </div>
        </div>

        ${this._renderStoriesSection()}

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
              No posts yet — publish your first post or broadcast from here.
              <div class="empty-cta">
                <roque-button
                  context="submit"
                  buttonId="empty-publish"
                  @aero-click="${this._openComposer}"
                  >Publish a post</roque-button
                >
              </div>
            </div>`
          : html`<div class="grid">
              ${this.posts.map((p) => this._renderCard(p))}
            </div>`}
      </div>

      ${this._renderComposer()}
      ${this._renderStoryComposer()}
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
          <span class="chip">❤ ${p.like_count} likes</span>
          <span class="chip">💬 ${p.comment_count} comments</span>
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
