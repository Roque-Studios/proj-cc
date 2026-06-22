import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-icon-button")
export class AeroIconButton extends LitElement {
  // Name matching the key inside your roque-icon registry
  @property({ type: String }) name = "";

  // Control size of the inner icon graphic in pixels
  @property({ type: Number }) iconSize = 16;

  // Accessible label for screen readers
  @property({ type: String }) ariaLabel = "";

  @property({ type: Boolean }) disabled = false;

  static styles = css`
    :host {
      display: inline-block;
      vertical-align: middle;
    }

    .aero-icon-btn {
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 4px;
      background: transparent;
      border: 1px solid transparent;
      border-radius: 3px;
      cursor: pointer;
      outline: none;
      color: #435b75; /* Classic dark slate blue toolbar accent color */
      transition: all 0.1s ease-in-out;
    }

    /* Aero Glass Toolbar Hover state */
    .aero-icon-btn:hover:not(:disabled) {
      border-color: rgba(60, 127, 177, 0.45);
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.6) 0%,
        rgba(255, 255, 255, 0.2) 50%,
        rgba(180, 215, 245, 0.2) 50.1%,
        rgba(180, 215, 245, 0.4) 100%
      );
      background-color: rgba(220, 237, 250, 0.4);
      box-shadow:
        0 1px 1px rgba(0, 0, 0, 0.05),
        inset 0 1px 0 rgba(255, 255, 255, 0.4);
      color: #000000;
    }

    /* Aero Push Click State */
    .aero-icon-btn:active:not(:disabled) {
      border-color: #3c7fb1;
      background: linear-gradient(to bottom, #e2f0fb 0%, #cfe5f9 100%);
      box-shadow: inset 1px 1px 2px rgba(0, 0, 0, 0.15);
    }

    /* Windows Focus Ring */
    .aero-icon-btn:focus-visible {
      border-color: #3c7fb1;
      box-shadow: 0 0 4px rgba(60, 127, 177, 0.8);
    }

    /* Disabled State */
    .aero-icon-btn:disabled {
      color: #b6b6b6;
      cursor: not-allowed;
    }
  `;

  render() {
    return html`
      <button
        class="aero-icon-btn"
        ?disabled="${this.disabled}"
        aria-label="${this.ariaLabel || this.name}"
      >
        <roque-icon .name="${this.name}" .size="${this.iconSize}"></roque-icon>
      </button>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-icon-button": AeroIconButton;
  }
}
