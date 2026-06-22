import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-pagination")
export class RoquePagination extends LitElement {
  /**
   * Total number of records across the filtered data pool
   */
  @property({ type: Number, attribute: "total-items" }) totalItems = 0;

  /**
   * Maximum layout allocation per page block
   */
  @property({ type: Number, attribute: "items-per-page" }) itemsPerPage = 10;

  /**
   * Currently active page pointer (1-indexed)
   */
  @property({ type: Number, attribute: "current-page", reflect: true })
  currentPage = 1;

  static styles = css`
    :host {
      display: block;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      font-size: 12px;
      user-select: none;
    }

    .aero-pagination-wrapper {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      padding: 4px;
      background: #fdfdfd;
      border: 1px solid #dcdcdc;
      border-radius: 3px;
    }

    .pagination-btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 24px;
      height: 22px;
      padding: 0 6px;
      box-sizing: border-box;
      background: linear-gradient(
        to bottom,
        #ffffff 0%,
        #f2f2f2 50%,
        #e1e1e1 50.1%,
        #e5e5e5 100%
      );
      border: 1px solid #b1b1b1;
      border-radius: 2px;
      color: #1e1e1e;
      font-size: 11px;
      cursor: pointer;
      text-shadow: 0 1px 0 rgba(255, 255, 255, 0.6);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.3);
    }

    /* Windows Explorer Glow Focus Variant */
    .pagination-btn:hover:not(.disabled) {
      background: linear-gradient(
        to bottom,
        #f5fbff 0%,
        #eaf5fd 50%,
        #cbe6f9 50.1%,
        #dbeffc 100%
      );
      border-color: #70a0c0;
      box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.4),
        0 0 3px rgba(112, 160, 192, 0.5);
    }

    /* Active Highlight Window Selection Layout */
    .pagination-btn.active {
      background: linear-gradient(
        to bottom,
        #e2f0fb 0%,
        #cfe5f7 50%,
        #b9daf3 50.1%,
        #c6e3f7 100%
      );
      border-color: #3c7fb1;
      font-weight: bold;
      box-shadow: inset 0 1px 1px rgba(0, 0, 0, 0.1);
    }

    /* Disabled State */
    .pagination-btn.disabled {
      background: #f4f4f4;
      border-color: #dcdcdc;
      color: #a0a0a0;
      cursor: default;
      text-shadow: none;
      box-shadow: none;
    }

    .pagination-info {
      margin: 0 8px;
      color: #555555;
      font-size: 11px;
    }
  `;

  private get _totalPages(): number {
    return Math.max(1, Math.ceil(this.totalItems / this.itemsPerPage));
  }

  private _changePage(page: number) {
    if (page < 1 || page > this._totalPages || page === this.currentPage)
      return;

    this.currentPage = page;

    // Dispatches standard event trigger payload matching web standard specs
    this.dispatchEvent(
      new CustomEvent("page-change", {
        detail: { page: this.currentPage },
        bubbles: true,
        composed: true,
      }),
    );
  }

  render() {
    const totalPages = this._totalPages;
    const isFirst = this.currentPage === 1;
    const isLast = this.currentPage === totalPages;

    return html`
      <div
        class="aero-pagination-wrapper"
        role="navigation"
        aria-label="Pagination Navigation"
      >
        <button
          class="pagination-btn ${isFirst ? "disabled" : ""}"
          @click="${() => this._changePage(1)}"
          ?disabled="${isFirst}"
        >
          «
        </button>

        <button
          class="pagination-btn ${isFirst ? "disabled" : ""}"
          @click="${() => this._changePage(this.currentPage - 1)}"
          ?disabled="${isFirst}"
        >
          ‹
        </button>

        <span class="pagination-info">
          Page ${this.currentPage} of ${totalPages}
        </span>

        <button
          class="pagination-btn ${isLast ? "disabled" : ""}"
          @click="${() => this._changePage(this.currentPage + 1)}"
          ?disabled="${isLast}"
        >
          ›
        </button>

        <button
          class="pagination-btn ${isLast ? "disabled" : ""}"
          @click="${() => this._changePage(totalPages)}"
          ?disabled="${isLast}"
        >
          »
        </button>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-pagination": RoquePagination;
  }
}
