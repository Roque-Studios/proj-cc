import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-switch")
export class AeroSwitch extends LitElement {
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

    .aero-switch-label {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      cursor: pointer;
      color: #1e1e1e;
      text-shadow: 0 0 10px rgba(255, 255, 255, 0.8);
    }

    .aero-switch-label.disabled {
      cursor: not-allowed;
      color: #838383;
    }

    /* Hidden Native Checkbox underneath */
    .native-checkbox {
      position: absolute;
      opacity: 0;
      width: 0;
      height: 0;
    }

    /* The Track Container */
    .aero-switch-track {
      position: relative;
      width: 36px;
      height: 16px;
      background: linear-gradient(to bottom, #dcdcdc 0%, #f0f0f0 100%);
      border: 1px solid #8e8e8e;
      border-top-color: #555555; /* Inset depth edge shadow */
      border-radius: 8px; /* Rounded pill shape */
      box-shadow: inset 1px 1px 2px rgba(0, 0, 0, 0.2);
      transition: all 0.2s ease-in-out;
      overflow: hidden;
    }

    /* Aero Green Activated Glow Background */
    .aero-switch-track::before {
      content: "";
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: linear-gradient(
        to bottom,
        #b2e1b2 0%,
        #79ce79 40%,
        #3fa33f 50.1%,
        #5db95d 100%
      );
      opacity: 0;
      transition: opacity 0.2s ease-in-out;
    }

    /* The Rounded Sliding Knob */
    .aero-switch-knob {
      position: absolute;
      top: 1px;
      left: 1px;
      width: 12px;
      height: 12px;
      border-radius: 50%;

      /* Pure Windows Aero Glass Push-Button Finish */
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.7) 0%,
        rgba(255, 255, 255, 0.2) 50%,
        rgba(0, 0, 0, 0.05) 50.1%,
        rgba(255, 255, 255, 0.15) 100%
      );
      background-color: #f2f2f2;
      border: 1px solid rgba(0, 0, 0, 0.35);
      box-shadow:
        0 1px 2px rgba(0, 0, 0, 0.2),
        inset 0 1px 0 #ffffff;

      transition:
        transform 0.2s cubic-bezier(0.25, 0.8, 0.25, 1),
        background-color 0.2s;
      z-index: 2;
    }

    /* Hover States */
    .aero-switch-label:hover
      .native-checkbox:not(:disabled)
      ~ .aero-switch-track {
      border-color: #5b9ed6;
    }

    .aero-switch-label:hover
      .native-checkbox:not(:disabled)
      ~ .aero-switch-track
      .aero-switch-knob {
      background-color: #e2f3ff;
      border-color: #3c7fb1;
      box-shadow:
        0 0 4px rgba(0, 162, 232, 0.4),
        inset 0 1px 0 #ffffff;
    }

    /* Active Checked State adjustments */
    .native-checkbox:checked ~ .aero-switch-track::before {
      opacity: 1; /* Reveals the glossy green backplane */
    }

    .native-checkbox:checked ~ .aero-switch-track .aero-switch-knob {
      transform: translateX(20px);
      background-color: #ffffff;
    }

    /* Focus Rings */
    .native-checkbox:focus-visible ~ .aero-switch-track {
      box-shadow:
        0 0 5px rgba(60, 127, 177, 0.8),
        inset 1px 1px 2px rgba(0, 0, 0, 0.1);
      border-color: #3c7fb1;
    }

    /* Disabled States */
    .native-checkbox:disabled ~ .aero-switch-track {
      background: #e4e4e4;
      border-color: #c0c0c0;
      box-shadow: none;
    }

    .native-checkbox:disabled ~ .aero-switch-track::before {
      display: none;
    }

    .native-checkbox:disabled ~ .aero-switch-track .aero-switch-knob {
      background: #f4f4f4;
      border-color: #d0d0d0;
      box-shadow: none;
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
      <label class="aero-switch-label ${this.disabled ? "disabled" : ""}">
        <input
          type="checkbox"
          class="native-checkbox"
          .checked="${this.checked}"
          ?disabled="${this.disabled}"
          @change="${this._handleChange}"
        />
        <div class="aero-switch-track">
          <div class="aero-switch-knob"></div>
        </div>
        ${this.label ? html`<span>${this.label}</span>` : ""}
      </label>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-switch": AeroSwitch;
  }
}
