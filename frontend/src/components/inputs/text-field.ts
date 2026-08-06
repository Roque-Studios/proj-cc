import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-text-field")
export class AeroTextField extends LitElement {
  @property({ type: String }) value = "";
  @property({ type: String }) placeholder = "";
  @property({ type: String }) label = "";
  // Input type ("text", "password", ...) — defaults to text so existing
  // usages are unchanged.
  @property({ type: String }) type = "text";
  @property({ type: Boolean }) disabled = false;
  @property({ type: Boolean }) error = false;

  static styles = css`
    :host {
      display: inline-block;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      font-size: 13px;
      width: 100%;
    }

    .form-group {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .aero-label {
      color: #1e1e1e;
      font-size: 12px;
      text-shadow: 0 0 10px rgba(255, 255, 255, 0.8);
    }

    .input-wrapper {
      position: relative;
      display: flex;
      align-items: center;
    }

    .aero-input {
      width: 100%;
      padding: 4px 6px;
      font-family: inherit;
      font-size: inherit;
      color: #000000;
      background-color: #ffffff;

      /* Distinct Windows Aero Inset Border Structure */
      border: 1px solid #707070;
      border-top-color: #555555; /* Darker top border for inset depth shadow */
      border-radius: 2px;

      /* Subtle inner shadow */
      box-shadow: inset 1px 1px 1px rgba(0, 0, 0, 0.1);

      outline: none;
      transition:
        border-color 0.15s ease-in-out,
        box-shadow 0.15s ease-in-out;
    }

    /* Aero Hover Glow Effect */
    .aero-input:hover:not(:disabled) {
      border-color: #5b9ed6;
      box-shadow:
        0 0 3px rgba(107, 180, 229, 0.6),
        inset 1px 1px 1px rgba(0, 0, 0, 0.1);
    }

    /* Aero Intense Focus Blue Glow */
    .aero-input:focus:not(:disabled) {
      border-color: #3c7fb1;
      box-shadow:
        0 0 5px rgba(60, 127, 177, 0.8),
        inset 1px 1px 1px rgba(0, 0, 0, 0.05);
    }

    /* Error State */
    .aero-input.error {
      border-color: #bc3b3b;
      box-shadow: 0 0 4px rgba(188, 59, 59, 0.5);
    }

    /* Disabled State */
    .aero-input:disabled {
      background-color: #f4f4f4;
      color: #838383;
      border-color: #c0c0c0;
      box-shadow: none;
      cursor: not-allowed;
    }
  `;

  private _handleInput(e: Event) {
    const input = e.target as HTMLInputElement;
    this.value = input.value;

    this.dispatchEvent(
      new CustomEvent("aero-input", {
        bubbles: true,
        composed: true,
        detail: { value: this.value },
      }),
    );
  }

  render() {
    return html`
      <div class="form-group">
        ${this.label
          ? html`<label class="aero-label">${this.label}</label>`
          : ""}
        <div class="input-wrapper">
          <input
            class="aero-input ${this.error ? "error" : ""}"
            type="${this.type}"
            .value="${this.value}"
            placeholder="${this.placeholder}"
            ?disabled="${this.disabled}"
            @input="${this._handleInput}"
          />
        </div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-text-field": AeroTextField;
  }
}
