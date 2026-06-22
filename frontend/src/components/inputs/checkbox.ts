import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-checkbox")
export class AeroCheckbox extends LitElement {
  @property({ type: Boolean, reflect: true }) checked = false;
  @property({ type: Boolean }) disabled = false;
  @property({ type: String }) label = "";

  static styles = css`
    :host {
      display: inline-block;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      font-size: 13px;
      user-select: none;
    }

    .aero-checkbox-label {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      cursor: pointer;
      color: #1e1e1e;
      text-shadow: 0 0 10px rgba(255, 255, 255, 0.8);
    }

    .aero-checkbox-label.disabled {
      cursor: not-allowed;
      color: #838383;
    }

    /* Hidden Native Checkbox */
    .native-checkbox {
      position: absolute;
      opacity: 0;
      width: 0;
      height: 0;
    }

    /* Aero Checkbox Custom Box */
    .aero-box {
      width: 13px;
      height: 13px;
      position: relative;
      background: linear-gradient(to bottom, #fafafa 0%, #f0f0f0 100%);

      /* Inset borders characteristic of Aero inputs */
      border: 1px solid #8e8e8e;
      border-top-color: #555555; /* Inset depth shadow */
      border-radius: 2px;
      box-shadow: inset 1px 1px 1px rgba(0, 0, 0, 0.1);

      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s ease-in-out;
    }

    /* Aero Hover Glow State */
    .aero-checkbox-label:hover .native-checkbox:not(:disabled) ~ .aero-box {
      border-color: #5b9ed6;
      background: linear-gradient(to bottom, #f0f7fc 0%, #d8eaf7 100%);
      box-shadow:
        0 0 3px rgba(107, 180, 229, 0.6),
        inset 1px 1px 1px rgba(0, 0, 0, 0.05);
    }

    /* Aero Focus Outline (Subtle dot indicator combined with slight glow) */
    .native-checkbox:focus-visible ~ .aero-box {
      border-color: #3c7fb1;
      box-shadow: 0 0 4px rgba(60, 127, 177, 0.8);
    }

    /* The Glassy Checkmark (Using pseudo-element) */
    .aero-box::after {
      content: "";
      position: absolute;
      width: 7px;
      height: 4px;
      border-left: 2px solid #2e6e2e;
      border-bottom: 2px solid #2e6e2e;
      transform: rotate(-45deg) translate(1px, -1px);
      opacity: 0;
      transition: opacity 0.1s ease-in-out;

      /* Aero check glow */
      filter: drop-shadow(0 0 1px rgba(144, 238, 144, 0.8));
    }

    /* Checked State Layout adjustments for background */
    .native-checkbox:checked ~ .aero-box {
      background: linear-gradient(to bottom, #ffffff 0%, #e6f3ff 100%);
    }

    .native-checkbox:checked ~ .aero-box::after {
      opacity: 1;
    }

    /* Disabled State */
    .native-checkbox:disabled ~ .aero-box {
      background: #f4f4f4;
      border-color: #c0c0c0;
      box-shadow: none;
    }

    .native-checkbox:disabled ~ .aero-box::after {
      border-color: #838383;
    }
  `;

  private _handleChange(e: Event) {
    const input = e.target as HTMLInputElement;
    this.checked = input.checked;

    this.dispatchEvent(
      new CustomEvent("aero-change", {
        bubbles: true,
        composed: true,
        detail: { checked: this.checked },
      }),
    );
  }

  render() {
    return html`
      <label class="aero-checkbox-label ${this.disabled ? "disabled" : ""}">
        <input
          type="checkbox"
          class="native-checkbox"
          .checked="${this.checked}"
          ?disabled="${this.disabled}"
          @change="${this._handleChange}"
        />
        <span class="aero-box"></span>
        ${this.label ? html`<span>${this.label}</span>` : ""}
      </label>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-checkbox": AeroCheckbox;
  }
}
