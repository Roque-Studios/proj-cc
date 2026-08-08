import { LitElement, html, css } from "lit";
import { property } from "lit/decorators.js";

export class AeroBadge extends LitElement {
  /**
   * Visual context variants: 'default' | 'success' | 'warning' | 'error' | 'info'
   */
  @property({ type: String, reflect: true }) context = "default";

  static styles = css`
    :host {
      display: inline-block;
      vertical-align: middle;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
    }

    .aero-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 2px 6px;
      font-size: 11px;
      font-weight: 600;
      line-height: 1;
      white-space: nowrap;
      border-radius: 3px;
      border: 1px solid;
      box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.35),
        0 1px 1px rgba(0, 0, 0, 0.05);
      text-shadow: 0 1px 0 rgba(255, 255, 255, 0.4);
    }

    /* Variant: Default / Neutral Silver */
    :host([context="default"]) .aero-badge {
      background: linear-gradient(to bottom, var(--cc-client) 0%, #eaeaea 100%);
      border-color: #b9b9b9;
      color: #333333;
    }

    /* Variant: Success / Aero Green */
    :host([context="success"]) .aero-badge {
      background: linear-gradient(to bottom, #f1f9ed 0%, #d4edd6 100%);
      border-color: #92cf94;
      color: #1e4b21;
    }

    /* Variant: Warning / Gold Sand */
    :host([context="warning"]) .aero-badge {
      background: linear-gradient(to bottom, #fffdf0 0%, #fef0b9 100%);
      border-color: #e3ca74;
      color: #5c480a;
    }

    /* Variant: Error / Classic Crimson */
    :host([context="error"]) .aero-badge {
      background: linear-gradient(to bottom, #fdf2f2 0%, #f8d7da 100%);
      border-color: #e8a2a7;
      color: var(--cc-danger-strong);
    }

    /* Variant: Info / Soft Sky Blue */
    :host([context="info"]) .aero-badge {
      background: linear-gradient(to bottom, var(--cc-fill-soft) 0%, #d1ecf1 100%);
      border-color: #bee5eb;
      color: #0c5460;
    }
  `;

  render() {
    return html`
      <span class="aero-badge">
        <slot></slot>
      </span>
    `;
  }
}

if (!customElements.get("roque-badge")) {
  customElements.define("roque-badge", AeroBadge);
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-badge": AeroBadge;
  }
}
