import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

import '../components/buttons/button.ts'
import '../components/layouts/card.ts'
import '../components/navigation/pagination.ts'
import '../components/feedback/toast.ts'
import '../components/feedback/alert.ts'
import '../components/feedback/spinner.ts'
import '../components/data/badge.ts'
import { api, ApiError, clearTokens } from '../lib/api'
import type { RevenueSummary, Subscriber } from '../lib/api'

const FILTERS: { value: string; label: string }[] = [
  { value: '', label: 'All' },
  { value: 'active', label: 'Active' },
  { value: 'trialing', label: 'Trialing' },
  { value: 'incomplete', label: 'Pending' },
  { value: 'past_due', label: 'Past due' },
  { value: 'canceled', label: 'Canceled' },
  { value: 'expired', label: 'Expired' },
]

/** Badge context per subscription status. */
function _statusContext(status: string): string {
  switch (status) {
    case 'active':
      return 'success'
    case 'trialing':
    case 'incomplete':
      return 'info'
    case 'past_due':
      return 'warning'
    default:
      return 'default'
  }
}

function _money(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

function _date(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString()
}

/**
 * Creator subscriber management: every subscription to this creator, with the
 * subscriber's identity and start date, filterable by status and paginated,
 * plus the revenue summary (monthly + one-time totals from the payment
 * ledger). Mobile-first: cards stack full-width; the revenue bar reflows.
 */
@customElement('roque-subscribers-manager')
export class CreatorSubscribersManager extends LitElement {
  @state() private items: Subscriber[] = []
  @state() private summary: RevenueSummary | null = null
  @state() private loading = true
  @state() private error = ''
  @state() private page = 1
  @state() private pageSize = 10
  @state() private total = 0
  @state() private statusFilter = '' // '' = all statuses

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

    .revenue-bar {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 10px;
      margin-bottom: 10px;
    }

    .revenue-card {
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.9) 0%,
        rgba(235, 245, 250, 0.75) 100%
      );
      border: 1px solid rgba(90, 130, 165, 0.35);
      border-radius: 4px;
      padding: 10px 12px;
      text-align: center;
    }

    .revenue-card .value {
      font-size: 18px;
      font-weight: 600;
      color: #1e395b;
      white-space: nowrap;
    }

    .revenue-card .label {
      font-size: 10px;
      color: #4a5b6e;
      margin-top: 2px;
    }

    .sub-counts {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }

    .filters {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      margin-bottom: 16px;
    }

    roque-button.filter-chip {
      --cc-filter-color: inherit;
    }

    roque-button.filter-chip.active::part(aero-btn) {
      background: linear-gradient(to bottom, #e8f4fb 0%, #cfe8f7 100%);
      background-color: rgba(120, 190, 235, 0.35);
      outline-color: rgba(40, 110, 165, 0.6);
      box-shadow:
        0 0 5px rgba(0, 162, 232, 0.45),
        inset 0 1px 0 rgba(255, 255, 255, 0.8);
    }

    .list {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .sub-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
    }

    .sub-identity {
      min-width: 0;
    }

    .sub-email {
      font-size: 14px;
      font-weight: 600;
      color: #1e2a38;
      word-break: break-all;
    }

    .sub-meta {
      font-size: 11px;
      color: #4a5b6e;
      margin-top: 3px;
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    .sub-meta .nonrenew {
      color: #b04a1f;
    }

    .sub-badges {
      display: flex;
      align-items: center;
      gap: 6px;
      flex-wrap: wrap;
    }

    .pager {
      display: flex;
      justify-content: center;
      margin-top: 18px;
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
      const body = await api.getCreatorSubscribers(
        this.page,
        this.pageSize,
        this.statusFilter || undefined,
      )
      this.items = body.items
      this.summary = body.summary
      this.total = body.total
    } catch (err) {
      this._handleError(err)
    } finally {
      this.loading = false
    }
  }

  private _onFilter(status: string) {
    if (status === this.statusFilter) return
    this.statusFilter = status
    this.page = 1
    this._load()
  }

  private _onPageChange(e: CustomEvent) {
    const page = e.detail?.page ?? 1
    if (page === this.page) return
    this.page = page
    this._load()
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
        <roque-card heading="Loading subscribers…">
          <roque-spinner size="28" label="Loading…"></roque-spinner>
        </roque-card>
      </div>`
    }

    const summary = this.summary
    const activeFilter = this.statusFilter

    return html`
      <div class="page">
        <div class="topbar">
          <div>
            <h1>Subscribers</h1>
            <p>
              Everyone subscribed to you, with start dates and your revenue
              summary. Filter by status and page through the list.
            </p>
          </div>
          <roque-button context="clear" buttonId="subs-logout-btn" @aero-click="${this._onLogout}"
            >Sign out</roque-button
          >
        </div>

        ${summary
          ? html`
              <div class="revenue-bar">
                <div class="revenue-card">
                  <div class="value">${_money(summary.total_revenue_cents)}</div>
                  <div class="label">Total revenue</div>
                </div>
                <div class="revenue-card">
                  <div class="value">${_money(summary.monthly_revenue_cents)}</div>
                  <div class="label">Monthly</div>
                </div>
                <div class="revenue-card">
                  <div class="value">${_money(summary.one_time_revenue_cents)}</div>
                  <div class="label">One-time</div>
                </div>
              </div>
              <div class="sub-counts">
                <roque-badge context="success"
                  >${summary.active_subscribers} active</roque-badge
                >
                <roque-badge context="info"
                  >${summary.trialing_subscribers} trialing</roque-badge
                >
                <roque-badge context="warning"
                  >${summary.past_due_subscribers} past due</roque-badge
                >
                <roque-badge context="default"
                  >${summary.canceled_subscribers} canceled</roque-badge
                >
                <roque-badge context="default"
                  >${summary.total_subscribers} total</roque-badge
                >
              </div>
            `
          : ''}

        ${this.error
          ? html`<div class="error-zone">
              <roque-alert
                type="error"
                heading="Failed to load subscribers"
                message="${this.error}"
                @aero-dismiss="${() => (this.error = '')}"
              ></roque-alert>
            </div>`
          : ''}

        <div class="filters">
          ${FILTERS.map(
            (f) => html`<roque-button
              class="filter-chip ${activeFilter === f.value ? 'active' : ''}"
              buttonId="filter-${f.value || 'all'}"
              @aero-click="${() => this._onFilter(f.value)}"
              >${f.label}</roque-button
            >`,
          )}
        </div>

        ${this.items.length === 0
          ? html`<div class="empty">
              ${activeFilter
                ? `No ${activeFilter} subscriptions yet.`
                : 'No subscribers yet — share your page to get started.'}
            </div>`
          : html`<div class="list">
              ${this.items.map((s) => this._renderSubscriber(s))}
            </div>`}

        ${this.total > this.pageSize
          ? html`<div class="pager">
              <roque-pagination
                total-items="${this.total}"
                items-per-page="${this.pageSize}"
                current-page="${this.page}"
                @page-change="${this._onPageChange}"
              ></roque-pagination>
            </div>`
          : ''}
      </div>
    `
  }

  private _renderSubscriber(s: Subscriber) {
    return html`
      <roque-card>
        <div class="sub-row">
          <div class="sub-identity">
            <div class="sub-email">${s.subscriber_email}</div>
            <div class="sub-meta">
              <span>Started ${_date(s.started_at)}</span>
              <span>Period ends ${_date(s.current_period_end)}</span>
              ${s.payment_provider ? html`<span>via ${s.payment_provider}</span>` : ''}
              ${s.cancel_at_period_end
                ? html`<span class="nonrenew">not renewing</span>`
                : ''}
            </div>
          </div>
          <div class="sub-badges">
            <roque-badge context="${_statusContext(s.status)}">${s.status}</roque-badge>
          </div>
        </div>
      </roque-card>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-subscribers-manager': CreatorSubscribersManager
  }
}
