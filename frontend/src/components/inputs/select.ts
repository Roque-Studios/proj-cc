import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

// Define an interface for our dropdown items
export interface AeroSelectOption {
  value: string;
  label: string;
}

@customElement("roque-select")
export class AeroSelect extends LitElement {
  @property({ type: String }) value = "";
  @property({ type: String }) label = "";
  @property({ type: Boolean }) disabled = false;
  @property({ type: Boolean }) error = false;

  // Pass the options as a structured array
  @property({ type: Array }) options: AeroSelectOption[] = [];

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
      color: var(--cc-text);
      font-size: 12px;
      text-shadow: 0 0 10px rgba(255, 255, 255, 0.8);
    }

    .select-wrapper {
      position: relative;
      display: inline-flex;
      width: 100%;
    }

    .aero-select {
      width: 100%;
      padding: 4px 24px 4px 6px;
      font-family: inherit;
      font-size: inherit;
      color: #000000;
      background-color: var(--cc-client);
      border: 1px solid #707070;
      border-top-color: #555555;
      border-radius: 2px;
      box-shadow: inset 1px 1px 1px rgba(0, 0, 0, 0.1);
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      outline: none;
      cursor: pointer;
      transition:
        border-color 0.15s ease-in-out,
        box-shadow 0.15s ease-in-out;
    }

    .select-wrapper::after {
      content: "";
      position: absolute;
      top: 1px;
      right: 1px;
      bottom: 1px;
      width: 18px;
      pointer-events: none;
      border-left: 1px solid #b6b6b6;
      border-radius: 0 2px 2px 0;
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.6) 0%,
        rgba(255, 255, 255, 0.2) 50%,
        rgba(0, 0, 0, 0.04) 50.1%,
        rgba(255, 255, 255, 0.15) 100%
      );
      background-color: #f0f0f0;
      background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='8' height='5' viewBox='0 0 8 5'%3E%3Cpath fill='%23000000' d='M0 0h8L4 5z'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: center;
    }

    .select-wrapper:hover .aero-select:not(:disabled) {
      border-color: var(--cc-accent-light);
      box-shadow:
        0 0 3px rgba(var(--cc-accent-rgb), 0.6),
        inset 1px 1px 1px rgba(0, 0, 0, 0.05);
    }

    .select-wrapper:hover::after {
      background-color: var(--cc-fill-strong);
      border-left-color: var(--cc-accent-light);
    }

    .aero-select:focus:not(:disabled) {
      border-color: var(--cc-accent);
      box-shadow:
        0 0 5px rgba(var(--cc-accent-rgb), 0.8),
        inset 1px 1px 1px rgba(0, 0, 0, 0.05);
    }

    .aero-select.error {
      border-color: #bc3b3b;
    }

    .aero-select:disabled {
      background-color: #f4f4f4;
      color: #838383;
      border-color: var(--cc-border-soft);
      box-shadow: none;
      cursor: not-allowed;
    }
  `;

  private _handleChange(e: Event) {
    const select = e.target as HTMLSelectElement;
    this.value = select.value;

    this.dispatchEvent(
      new CustomEvent("aero-change", {
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
        <div class="select-wrapper">
          <select
            class="aero-select ${this.error ? "error" : ""}"
            .value="${this.value}"
            ?disabled="${this.disabled}"
            @change="${this._handleChange}"
          >
            ${this.options.map(
              (opt) => html`
                <option
                  value="${opt.value}"
                  ?selected="${this.value === opt.value}"
                >
                  ${opt.label}
                </option>
              `,
            )}
          </select>
        </div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-select": AeroSelect;
  }
}
