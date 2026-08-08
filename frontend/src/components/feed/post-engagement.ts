import { LitElement, html, css, nothing } from 'lit'
import { customElement, property, state } from 'lit/decorators.js'

import '../buttons/button.ts'
import '../data/avatar.ts'
import '../inputs/textarea.ts'
import '../media/icon.ts'
import '../feedback/spinner.ts'
import { api, ApiError } from '../../lib/api'
import type { FeedPost, PostComment } from '../../lib/api'

/**
 * Post engagement bar: the like toggle + the comment section.
 *
 * Renders under a feed post:
 * - a **like** action (heart + count) that toggles optimistically through
 *   ``POST/DELETE /posts/{id}/like`` and rolls back on failure;
 * - a **comments** action (bubble + count) that lazily loads and renders the
 *   post's comments (newest first, paginated with a "show more" button), with
 *   a text-only composer (the backend rejects blanks and >500 chars) and a
 *   small emoji quick-row so replies are "text and emojis" with zero friction.
 *
 * ``interactable`` gates the *actions* (followers/owner only — the teaser
 * renders the counts as plain labels), and ``userId`` enables the delete
 * affordance on the viewer's own comments. Errors surface as an ``aero-toast``
 * event (``detail: {type, heading, message}``) the host feed renders.
 */
@customElement('roque-post-engagement')
export class PostEngagement extends LitElement {
  /** The post this engagement belongs to (carries the live counters). */
  @property({ type: Object }) post: FeedPost | null = null
  /** True when the viewer may like/comment (follower, or the post owner). */
  @property({ type: Boolean }) interactable = false
  /** The signed-in viewer's user id — enables deleting own comments. */
  @property({ type: Number }) userId = 0

  @state() private liked = false
  @state() private likeCount = 0
  @state() private likeBusy = false

  @state() private commentsOpen = false
  @state() private comments: PostComment[] = []
  @state() private commentsTotal = 0
  @state() private commentsLoading = false
  @state() private commentsLoadingMore = false
  @state() private hasMore = false
  @state() private commentText = ''
  @state() private commentBusy = false
  @state() private deleting = new Set<number>()

  private _page = 0
  private _loadedPostId: number | null = null

  /** Quick-pick emojis for the comment composer (text + emojis only). */
  private static readonly EMOJIS = ['👍', '❤️', '😂', '😮', '😢', '🔥', '👏', '🎉', '✨', '🙏']

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .bar {
      display: flex;
      align-items: center;
      gap: 6px;
      margin-top: 10px;
      padding-top: 8px;
      border-top: 1px solid #dde3ea;
    }

    .action {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 10px;
      font-family: inherit;
      font-size: 12px;
      color: var(--cc-header-ink-2);
      background: linear-gradient(to bottom, rgba(255, 255, 255, 0.9), rgba(var(--cc-tint-warm), 0.75));
      border: 1px solid rgba(var(--cc-tint-deep), 0.4);
      border-radius: 3px;
      cursor: pointer;
      transition: box-shadow 0.15s ease, transform 0.15s ease, background 0.15s ease;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
    }

    .action:hover:not(:disabled) {
      box-shadow: 0 0 5px rgba(var(--cc-accent-rgb), 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.8);
      transform: translateY(-1px);
    }

    .action:active:not(:disabled) {
      transform: translateY(0);
    }

    .action:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }

    .action.liked {
      color: var(--cc-danger);
      border-color: rgba(190, 60, 75, 0.5);
      background: linear-gradient(to bottom, #fdf1f2 0%, #f6d4da 100%);
    }

    .action .count {
      font-weight: 600;
    }

    /* Non-interactive (teaser) label — counts only, no actions. */
    .static {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 4px 10px;
      font-size: 12px;
      color: var(--cc-text-muted);
    }

    /* --- Comments --- */
    .comments {
      margin-top: 10px;
      border-top: 1px dashed #d3dde6;
      padding-top: 10px;
    }

    .comment {
      display: flex;
      gap: 8px;
      padding: 7px 4px;
    }

    .comment-body {
      flex: 1;
      min-width: 0;
    }

    .comment-meta {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }

    .comment-author {
      font-size: 12px;
      font-weight: 600;
      color: var(--cc-heading);
    }

    .comment-creator-tag {
      font-size: 10px;
      color: #1e4b21;
      background: #d4edd6;
      border: 1px solid #92cf94;
      border-radius: 999px;
      padding: 0 6px;
    }

    .comment-time {
      font-size: 10px;
      color: var(--cc-text-faint);
    }

    .comment-text {
      margin: 2px 0 0;
      font-size: 13px;
      line-height: 1.5;
      color: #222;
      white-space: pre-wrap;
      word-break: break-word;
    }

    .comment-del {
      background: none;
      border: none;
      padding: 0 2px;
      font-size: 11px;
      color: #a05a5a;
      cursor: pointer;
      opacity: 0;
      transition: opacity 0.15s ease;
    }

    .comment:hover .comment-del {
      opacity: 1;
    }

    .comment-del:hover {
      color: var(--cc-danger);
    }

    .comment-del:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }

    .comments-foot {
      display: flex;
      justify-content: center;
      padding: 6px 0 2px;
    }

    .more-btn {
      font-family: inherit;
      font-size: 11px;
      color: var(--cc-accent);
      background: none;
      border: none;
      cursor: pointer;
      padding: 3px 8px;
    }

    .more-btn:hover {
      text-decoration: underline;
    }

    .comments-empty {
      padding: 8px 4px;
      font-size: 12px;
      color: var(--cc-text-faint);
      text-align: center;
    }

    /* --- Composer --- */
    .composer {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: 8px;
    }

    .emoji-row {
      display: flex;
      flex-wrap: wrap;
      gap: 3px;
    }

    .emoji-btn {
      width: 26px;
      height: 26px;
      padding: 0;
      font-size: 15px;
      line-height: 26px;
      text-align: center;
      background: linear-gradient(to bottom, rgba(255, 255, 255, 0.9), rgba(var(--cc-tint-warm), 0.7));
      border: 1px solid rgba(var(--cc-tint-deep), 0.35);
      border-radius: 3px;
      cursor: pointer;
      transition: transform 0.12s ease, box-shadow 0.12s ease;
    }

    .emoji-btn:hover {
      transform: scale(1.18);
      box-shadow: 0 0 4px rgba(var(--cc-accent-rgb), 0.5);
    }

    .composer-row {
      display: flex;
      align-items: flex-end;
      gap: 8px;
    }

    .composer-row roque-textarea {
      flex: 1;
    }
  `

  protected updated(changed: Map<string, unknown>) {
    if (changed.has('post')) this._syncFromPost()
  }

  /** Pull the authoritative counters from the post object. */
  private _syncFromPost() {
    this.liked = this.post?.liked_by_me ?? false
    this.likeCount = this.post?.like_count ?? 0
    // A different post (or a fresh feed load) resets the loaded comments.
    if (this.post && this._loadedPostId !== this.post.id) {
      this._loadedPostId = this.post.id
      this.comments = []
      this.commentsTotal = 0
      this._page = 0
      this.hasMore = false
    }
  }

  private _toast(type: 'info' | 'error', heading: string, message: string) {
    this.dispatchEvent(
      new CustomEvent('aero-toast', {
        bubbles: true,
        composed: true,
        detail: { type, heading, message },
      }),
    )
  }

  // ------------------------------------------------------------------ #
  // Likes
  // ------------------------------------------------------------------ #

  private async _toggleLike() {
    if (!this.post || this.likeBusy || !this.interactable) return
    const postId = this.post.id
    const wasLiked = this.liked
    const previousCount = this.likeCount
    // Optimistic flip; the server's count wins on success.
    this.liked = !wasLiked
    this.likeCount = Math.max(0, previousCount + (this.liked ? 1 : -1))
    this.likeBusy = true
    try {
      const res = this.liked
        ? await api.likePost(postId)
        : await api.unlikePost(postId)
      this.liked = res.liked
      this.likeCount = res.like_count
    } catch (e) {
      // Roll back and explain.
      this.liked = wasLiked
      this.likeCount = previousCount
      this._toast(
        'error',
        'Like failed',
        e instanceof ApiError ? e.message : 'Could not update the like.',
      )
    } finally {
      this.likeBusy = false
    }
  }

  // ------------------------------------------------------------------ #
  // Comments
  // ------------------------------------------------------------------ #

  private async _toggleComments() {
    if (!this.post) return
    if (this.commentsOpen) {
      // Collapsing keeps the loaded comments cached, so re-opening is instant.
      this.commentsOpen = false
      return
    }
    this.commentsOpen = true
    if (this.comments.length === 0) await this._loadComments(1)
  }

  private async _loadComments(page: number) {
    if (!this.post || this.commentsLoading || this.commentsLoadingMore) return
    if (page === 1) this.commentsLoading = true
    else this.commentsLoadingMore = true
    try {
      const data = await api.getPostComments(this.post.id, page, 20)
      this._page = page
      this.hasMore = data.has_more
      this.commentsTotal = data.total
      const seen = new Set(this.comments.map((c) => c.id))
      this.comments = [
        ...this.comments,
        ...data.items.filter((c) => !seen.has(c.id)),
      ]
    } catch (e) {
      this._toast(
        'error',
        'Comments failed to load',
        e instanceof ApiError ? e.message : 'Could not load comments.',
      )
    } finally {
      this.commentsLoading = false
      this.commentsLoadingMore = false
    }
  }

  private _insertEmoji(emoji: string) {
    this.commentText = `${this.commentText}${emoji}`
  }

  private async _submitComment() {
    if (!this.post || this.commentBusy) return
    const body = this.commentText.trim()
    if (!body) {
      this._toast('error', 'Empty comment', 'Write something before posting.')
      return
    }
    this.commentBusy = true
    try {
      const created = await api.createPostComment(this.post.id, body)
      this.comments = [created, ...this.comments]
      this.commentsTotal += 1
      this.commentText = ''
      this._toast('info', 'Comment posted', 'Thanks for joining the conversation.')
    } catch (e) {
      this._toast(
        'error',
        'Comment failed',
        e instanceof ApiError ? e.message : 'Could not post the comment.',
      )
    } finally {
      this.commentBusy = false
    }
  }

  private async _deleteComment(comment: PostComment) {
    if (!this.post || this.deleting.has(comment.id)) return
    this.deleting = new Set(this.deleting).add(comment.id)
    try {
      await api.deletePostComment(this.post.id, comment.id)
      this.comments = this.comments.filter((c) => c.id !== comment.id)
      this.commentsTotal = Math.max(0, this.commentsTotal - 1)
    } catch (e) {
      this._toast(
        'error',
        'Could not delete',
        e instanceof ApiError ? e.message : 'The comment could not be deleted.',
      )
    } finally {
      const next = new Set(this.deleting)
      next.delete(comment.id)
      this.deleting = next
    }
  }

  private _authorName(c: PostComment): string {
    return c.author_display_name || c.author_username || `User ${c.user_id}`
  }

  private _commentTime(iso: string): string {
    try {
      return new Date(iso).toLocaleString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
      })
    } catch {
      return ''
    }
  }

  render() {
    const post = this.post
    if (!post) return nothing

    const likeIcon = this.liked ? 'heart-filled' : 'heart'

    return html`
      <div class="bar">
        ${this.interactable
          ? html`
              <button
                class="action ${this.liked ? 'liked' : ''}"
                aria-label="${this.liked ? 'Unlike this post' : 'Like this post'}"
                aria-pressed="${this.liked}"
                ?disabled="${this.likeBusy}"
                @click="${this._toggleLike}"
              >
                <roque-icon name="${likeIcon}" size="14"></roque-icon>
                <span class="count">${this.likeCount}</span>
              </button>
              <button
                class="action ${this.commentsOpen ? 'liked' : ''}"
                aria-label="Comments"
                aria-expanded="${this.commentsOpen}"
                @click="${this._toggleComments}"
              >
                <roque-icon name="chat" size="14"></roque-icon>
                <span class="count">${this.commentsTotal || post.comment_count}</span>
              </button>
            `
          : html`
              <span class="static" title="${this.likeCount} likes">
                <roque-icon name="heart" size="14"></roque-icon>
                ${this.likeCount}
              </span>
              <span class="static" title="${post.comment_count} comments">
                <roque-icon name="chat" size="14"></roque-icon>
                ${post.comment_count}
              </span>
            `}
      </div>

      ${this.commentsOpen
        ? html`
            <div class="comments">
              ${this.commentsLoading
                ? html`<div class="comments-empty">
                    <roque-spinner size="18" label="Loading comments…"></roque-spinner>
                  </div>`
                : this.comments.length === 0
                  ? html`<div class="comments-empty">
                      No comments yet — be the first to say something.
                    </div>`
                  : this.comments.map(
                      (c) => html`
                        <div class="comment">
                          <roque-avatar
                            src="${c.author_avatar_url || ''}"
                            alt="${this._authorName(c)}"
                            size="30"
                          ></roque-avatar>
                          <div class="comment-body">
                            <div class="comment-meta">
                              <span class="comment-author">${this._authorName(c)}</span>
                              ${c.author_is_creator
                                ? html`<span class="comment-creator-tag">creator</span>`
                                : nothing}
                              <span class="comment-time">${this._commentTime(c.created_at)}</span>
                              ${this.userId === c.user_id
                                ? html`<button
                                    class="comment-del"
                                    aria-label="Delete your comment"
                                    ?disabled="${this.deleting.has(c.id)}"
                                    @click="${() => this._deleteComment(c)}"
                                    >✕</button
                                  >`
                                : nothing}
                            </div>
                            <p class="comment-text">${c.body}</p>
                          </div>
                        </div>
                      `,
                    )}
              ${this.hasMore
                ? html`<div class="comments-foot">
                    <button
                      class="more-btn"
                      ?disabled="${this.commentsLoadingMore}"
                      @click="${() => this._loadComments(this._page + 1)}"
                      >${this.commentsLoadingMore ? 'Loading…' : 'Show more comments'}</button
                    >
                  </div>`
                : nothing}
            </div>
          `
        : nothing}

      ${this.interactable && this.commentsOpen
        ? html`
            <div class="composer">
              <div class="emoji-row">
                ${PostEngagement.EMOJIS.map(
                  (e) => html`<button
                    class="emoji-btn"
                    aria-label="Add ${e}"
                    @click="${() => this._insertEmoji(e)}"
                    >${e}</button
                  >`,
                )}
              </div>
              <div class="composer-row">
                <roque-textarea
                  rows="2"
                  placeholder="Write a comment…"
                  maxlength="500"
                  .value="${this.commentText}"
                  ?disabled="${this.commentBusy}"
                  @aero-input="${(e: CustomEvent) =>
                    (this.commentText = (e.detail?.value ?? '').slice(0, 500))}"
                ></roque-textarea>
                <roque-button
                  context="submit"
                  buttonId="post-comment-${post.id}"
                  ?disabled="${this.commentBusy}"
                  @aero-click="${this._submitComment}"
                  >${this.commentBusy ? 'Posting…' : 'Post'}</roque-button
                >
              </div>
            </div>
          `
        : nothing}
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-post-engagement': PostEngagement
  }
}
