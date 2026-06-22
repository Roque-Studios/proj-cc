import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";
// Self-contain our icon dependency so it handles its own registry safely
import "../media/icon.ts";
import "../buttons/button.ts";

@customElement("roque-alert")
export class AeroAlert extends LitElement {
  // Alert variants: 'info', 'warning', 'error', 'success'
  @property({ type: String }) type = "info";

  // Primary bold text header directive
  @property({ type: String }) heading = "";

  // Supporting instructional paragraph block text
  @property({ type: String }) message = "";

  // Controls visibility toggle switch state
  @property({ type: Boolean, reflect: true }) open = true;

  static styles = css`
    :host {
      display: block;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
    }

    /* Invisible overlay layer to lock focus on the task dialogue box */
    .aero-modal-overlay {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background-color: rgba(255, 255, 255, 0.1);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 9999;
    }

    /* Window Dialog Framing Box Container matching Windows 7 Task Dialog style */
    .aero-dialog-window {
      width: 100%;
      max-width: 460px;
      background: #ffffff;
      border: 1px solid #142a42;
      border-radius: 5px;
      box-shadow:
        0 8px 30px rgba(0, 0, 0, 0.35),
        inset 0 1px 0 rgba(255, 255, 255, 0.8);
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }

    /* The main content zone window section */
    .aero-dialog-main {
      display: flex;
      gap: 16px;
      padding: 16px 20px;
      background-color: #ffffff;
    }

    /* The Left Status Icon Box */
    .icon-column {
      display: flex;
      align-items: flex-start;
      padding-top: 2px;
    }

    /* Color styles matching original Win7 system warning color vectors */
    .type-info {
      color: #0066cc;
    }
    .type-warning {
      color: #e6a100;
    }
    .type-error {
      color: #cc3333;
    }
    .type-success {
      color: #2a8a2a;
    }

    /* Right text instruction zone column content stack */
    .text-column {
      display: flex;
      flex-direction: column;
      gap: 8px;
      flex: 1;
    }

    .aero-dialog-heading {
      font-size: 14px;
      color: #003399; /* Classic Windows Task Dialog core text blue hue */
      font-weight: normal;
      margin: 0;
      line-height: 1.3;
    }

    .aero-dialog-message {
      font-size: 12px;
      color: #1e1e1e;
      margin: 0;
      line-height: 1.5;
    }

    /* Bottom Command Button Bar Zone (Typically shades down to cool gray) */
    .aero-dialog-action-bar {
      background: linear-gradient(to bottom, #f0f0f0 0%, #e1e1e1 100%);
      border-top: 1px solid #d9d9d9;
      padding: 10px 20px;
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }
  `;

  private _closeAlert() {
    this.open = false;

    // Dispatch dismiss hooks so layout files can reset parent states asynchronously
    this.dispatchEvent(
      new CustomEvent("aero-dismiss", {
        bubbles: true,
        composed: true,
      }),
    );
  }

  render() {
    if (!this.open) return html``;

    // Direct registry icon mapping lookup based on type
    let systemIcon = "info";
    if (this.type === "error" || this.type === "warning") systemIcon = "info"; // Fallback mapping back onto dictionary indices

    return html`
      <div class="aero-modal-overlay">
        <div class="aero-dialog-window" role="alertdialog" aria-modal="true">
          <div class="aero-dialog-main">
            <div class="icon-column type-${this.type}">
              <roque-icon .name="${systemIcon}" size="32"></roque-icon>
            </div>

            <div class="text-column">
              ${this.heading
                ? html`<h2 class="aero-dialog-heading">${this.heading}</h2>`
                : ""}
              ${this.message
                ? html`<p class="aero-dialog-message">${this.message}</p>`
                : ""}
            </div>
          </div>

          <div class="aero-dialog-action-bar">
            <roque-button context="submit" @click="${this._closeAlert}"
              >OK</roque-button
            >
          </div>
        </div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-alert": AeroAlert;
  }
}
