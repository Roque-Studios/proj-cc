import { LitElement, html, css, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-textarea")
export class AeroTextarea extends LitElement {
  @property({ type: String }) value = "";
  @property({ type: String }) placeholder = "";
  @property({ type: String }) label = "";
  @property({ type: Number }) rows = 4;
  // Optional max character count (the native textarea attribute).
  @property({ type: Number }) maxlength = 0;
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

    .textarea-wrapper {
      position: relative;
      display: flex;
      width: 100%;
    }

    .aero-textarea {
      width: 100%;
      padding: 6px;
      font-family: inherit;
      font-size: inherit;
      color: #000000;
      background-color: #ffffff;
      resize: vertical; /* Follows typical Windows form scaling */

      /* Distinct Windows Aero Inset Border Structure */
      border: 1px solid #707070;
      border-top-color: #555555; /* Darker top border for socket depth shadow */
      border-radius: 2px;

      /* Inset shadow */
      box-shadow: inset 1px 1px 2px rgba(0, 0, 0, 0.1);

      outline: none;
      transition:
        border-color 0.15s ease-in-out,
        box-shadow 0.15s ease-in-out;
    }

    /* Aero Hover Glow Effect */
    .aero-textarea:hover:not(:disabled) {
      border-color: #5b9ed6;
      box-shadow:
        0 0 3px rgba(107, 180, 229, 0.6),
        inset 1px 1px 2px rgba(0, 0, 0, 0.1);
    }

    /* Aero Intense Focus Blue Glow */
    .aero-textarea:focus:not(:disabled) {
      border-color: #3c7fb1;
      box-shadow:
        0 0 5px rgba(60, 127, 177, 0.8),
        inset 1px 1px 1px rgba(0, 0, 0, 0.05);
    }

    /* Error State */
    .aero-textarea.error {
      border-color: #bc3b3b;
      box-shadow: 0 0 4px rgba(188, 59, 59, 0.5);
    }

    /* Disabled State */
    .aero-textarea:disabled {
      background-color: #f4f4f4;
      color: #838383;
      border-color: #c0c0c0;
      box-shadow: none;
      cursor: not-allowed;
    }

    /* --- Aero Scrollbar Styling Injection --- */
    .aero-textarea::-webkit-scrollbar {
      width: 17px;
      background: #f0f0f0;
      border-left: 1px solid #d9d9d9;
    }

    .aero-textarea::-webkit-scrollbar-thumb {
      background: linear-gradient(
        to right,
        rgba(255, 255, 255, 0.5) 0%,
        rgba(255, 255, 255, 0.2) 50%,
        rgba(0, 0, 0, 0.05) 50.1%,
        rgba(255, 255, 255, 0.15) 100%
      );
      background-color: #cdcdcd;
      border: 1px solid #a6a6a6;
      border-radius: 1px;
      box-shadow: inset 1px 1px 0 rgba(255, 255, 255, 0.4);
    }

    .aero-textarea::-webkit-scrollbar-thumb:hover {
      background-color: #aebedb;
      border-color: #728cb8;
    }
  `;

  private _handleInput(e: Event) {
    const textarea = e.target as HTMLTextAreaElement;
    this.value = textarea.value;

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
        <div class="textarea-wrapper">
          <textarea
            class="aero-textarea ${this.error ? "error" : ""}"
            .rows="${this.rows}"
            .value="${this.value}"
            placeholder="${this.placeholder}"
            maxlength="${this.maxlength > 0 ? this.maxlength : nothing}"
            ?disabled="${this.disabled}"
            @input="${this._handleInput}"
          ></textarea>
        </div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-textarea": AeroTextarea;
  }
}
