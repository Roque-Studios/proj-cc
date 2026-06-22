import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-radio")
export class AeroRadio extends LitElement {
  @property({ type: Boolean, reflect: true }) checked = false;
  @property({ type: Boolean }) disabled = false;
  @property({ type: String }) name = ""; // Crucial for native radio grouping
  @property({ type: String }) value = "";
  @property({ type: String }) label = "";

  static styles = css`
    :host {
      display: inline-block;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      font-size: 13px;
      user-select: none;
    }

    .aero-radio-label {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      cursor: pointer;
      color: #1e1e1e;
      text-shadow: 0 0 10px rgba(255, 255, 255, 0.8);
    }

    .aero-radio-label.disabled {
      cursor: not-allowed;
      color: #838383;
    }

    /* Hidden Native Radio Button */
    .native-radio {
      position: absolute;
      opacity: 0;
      width: 0;
      height: 0;
    }

    /* Aero Custom Radio Outer Circle */
    .aero-circle {
      width: 12px;
      height: 12px;
      position: relative;
      background: linear-gradient(to bottom, #fafafa 0%, #f0f0f0 100%);

      /* Inset circular borders matching Windows Aero style */
      border: 1px solid #8e8e8e;
      border-top-color: #555555; /* Inset depth shadow */
      border-radius: 50%;
      box-shadow: inset 1px 1px 1px rgba(0, 0, 0, 0.1);

      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.15s ease-in-out;
    }

    /* Aero Hover Glow State */
    .aero-radio-label:hover .native-radio:not(:disabled) ~ .aero-circle {
      border-color: #5b9ed6;
      background: linear-gradient(to bottom, #f0f7fc 0%, #d8eaf7 100%);
      box-shadow:
        0 0 3px rgba(107, 180, 229, 0.6),
        inset 1px 1px 1px rgba(0, 0, 0, 0.05);
    }

    /* Aero Focus Glow Ring */
    .native-radio:focus-visible ~ .aero-circle {
      border-color: #3c7fb1;
      box-shadow: 0 0 4px rgba(60, 127, 177, 0.8);
    }

    /* The Glossy Core Aqua Dot */
    .aero-circle::after {
      content: "";
      position: absolute;
      width: 6px;
      height: 6px;
      border-radius: 50%;

      /* Windows 7 / Vista Metallic Blue Radio Dot Gradient */
      background: linear-gradient(
        to bottom,
        #7abcff 0%,
        #60abf8 40%,
        #1d7fed 50%,
        #0059bf 100%
      );
      border: 1px solid #004b9a;

      opacity: 0;
      transform: scale(0.5);
      transition:
        opacity 0.15s ease-in-out,
        transform 0.15s ease-in-out;
    }

    /* Checked State Layout adjustments */
    .native-radio:checked ~ .aero-circle {
      background: linear-gradient(to bottom, #ffffff 0%, #e6f3ff 100%);
    }

    .native-radio:checked ~ .aero-circle::after {
      opacity: 1;
      transform: scale(1);
    }

    /* Disabled State */
    .native-radio:disabled ~ .aero-circle {
      background: #f4f4f4;
      border-color: #c0c0c0;
      box-shadow: none;
    }

    .native-radio:disabled ~ .aero-circle::after {
      background: #838383;
      border-color: #666666;
    }
  `;

  private _handleChange(e: Event) {
    const input = e.target as HTMLInputElement;
    this.checked = input.checked;

    this.dispatchEvent(
      new CustomEvent("aero-change", {
        bubbles: true,
        composed: true,
        detail: {
          checked: this.checked,
          value: this.value,
          name: this.name,
        },
      }),
    );
  }

  render() {
    return html`
      <label class="aero-radio-label ${this.disabled ? "disabled" : ""}">
        <input
          type="radio"
          class="native-radio"
          .name="${this.name}"
          .value="${this.value}"
          .checked="${this.checked}"
          ?disabled="${this.disabled}"
          @change="${this._handleChange}"
        />
        <span class="aero-circle"></span>
        ${this.label ? html`<span>${this.label}</span>` : ""}
      </label>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-radio": AeroRadio;
  }
}
