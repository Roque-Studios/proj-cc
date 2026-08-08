import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";
// Pull in the icon dependency so it registers safely
import "../media/icon";

@customElement("roque-toast")
export class AeroToast extends LitElement {
  @property({ type: String }) heading = "";
  @property({ type: String }) message = "";
  @property({ type: String }) icon = "info";
  @property({ type: Boolean, reflect: true }) visible = true;

  static styles = css`
    /* CRITICAL FIX: Force the root web component wrapper to respect position properties */
    :host {
      position: fixed !important;
      bottom: 20px;
      right: 20px;
      z-index: 10000;
      display: block !important;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
    }

    /* Aero Glass Notification Frame Window */
    .aero-toast-window {
      width: 320px;
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.95) 0%,
        rgba(240, 245, 250, 0.9) 100%
      );
      background-color: rgba(240, 245, 250, 0.9);
      border: 1px solid rgba(0, 0, 0, 0.45);
      border-radius: 4px;
      padding: 12px 14px;
      box-sizing: border-box;

      box-shadow:
        0 4px 15px rgba(0, 0, 0, 0.3),
        inset 0 1px 0 rgba(255, 255, 255, 0.9);

      display: flex;
      gap: 12px;
      position: relative;

      /* Smooth window slide and fade presentation transitions */
      transition:
        opacity 0.2s ease-in-out,
        transform 0.2s ease-in-out;
      opacity: 1;
      transform: translateY(0);
    }

    /* Hidden state rule handles window slide away */
    .aero-toast-window.hidden {
      opacity: 0;
      transform: translateY(20px);
      pointer-events: none;
    }

    /* Windows style close button layout */
    .aero-close-btn {
      position: absolute;
      top: 6px;
      right: 8px;
      background: none;
      border: none;
      font-size: 11px;
      color: #7a7a7a;
      cursor: pointer;
      line-height: 1;
      padding: 2px 4px;
      border-radius: 2px;
    }

    .aero-close-btn:hover {
      background-color: var(--cc-danger);
      color: var(--cc-client);
    }

    .toast-icon-zone {
      display: flex;
      align-items: flex-start;
      color: var(--cc-info);
      padding-top: 2px;
    }

    .toast-text-zone {
      display: flex;
      flex-direction: column;
      gap: 3px;
      padding-right: 12px;
    }

    .toast-heading {
      font-size: 12px;
      font-weight: bold;
      color: var(--cc-heading);
      margin: 0;
    }

    .toast-message {
      font-size: 11px;
      color: #212121;
      margin: 0;
      line-height: 1.4;
    }
  `;

  private _dismiss() {
    this.visible = false;

    this.dispatchEvent(
      new CustomEvent("aero-toast-closed", {
        bubbles: true,
        composed: true,
      }),
    );
  }

  render() {
    // If hidden, configure host styling properties cleanly
    if (!this.visible) {
      this.style.pointerEvents = "none";
    } else {
      this.style.pointerEvents = "auto";
    }

    return html`
      <div class="aero-toast-window ${this.visible ? "" : "hidden"}">
        <button class="aero-close-btn" @click="${this._dismiss}">✕</button>

        <div class="toast-icon-zone">
          <roque-icon .name="${this.icon}" size="16"></roque-icon>
        </div>

        <div class="toast-text-zone">
          ${this.heading
            ? html`<h4 class="toast-heading">${this.heading}</h4>`
            : ""}
          ${this.message
            ? html`<p class="toast-message">${this.message}</p>`
            : ""}
        </div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-toast": AeroToast;
  }
}
