import { LitElement, html, css } from "lit";
import "../media/icon"; // Ensure icon dependency runs inside the option slots safely

export class AeroMenuItem extends LitElement {
  static properties = {
    icon: { type: String },
    disabled: { type: Boolean },
  };

  icon = "";
  disabled = false;

  static styles = css`
    :host {
      display: block;
    }

    .aero-item-row {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 4px 10px 4px 6px;
      font-size: 12px;
      color: #000000;
      cursor: pointer;
      user-select: none;
      border: 1px solid transparent;
      border-radius: 2px;
      box-sizing: border-box;
    }

    /* The Iconic Windows 7 Sky Blue Gradient Option Hover Accent Selection */
    .aero-item-row:hover:not(.disabled) {
      border-color: #b8d6f3;
      background: linear-gradient(
        to bottom,
        #fafcfe 0%,
        #edf4fc 50%,
        #dee8f6 50.1%,
        #e3edf9 100%
      );
      background-color: #edf4fc;
    }

    .aero-item-row.disabled {
      color: #999999;
      cursor: not-allowed;
    }

    .icon-holder {
      width: 16px;
      height: 16px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: #435b75;
    }

    .label-holder {
      flex: 1;
    }
  `;

  render() {
    return html`
      <div class="aero-item-row ${this.disabled ? "disabled" : ""}">
        <div class="icon-holder">
          ${this.icon
            ? html`<roque-icon .name="${this.icon}" size="14"></roque-icon>`
            : ""}
        </div>
        <div class="label-holder">
          <slot></slot>
        </div>
      </div>
    `;
  }
}

if (!customElements.get("roque-menu-item")) {
  customElements.define("roque-menu-item", AeroMenuItem);
}
