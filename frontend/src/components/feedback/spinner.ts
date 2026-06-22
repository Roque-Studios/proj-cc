import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-spinner")
export class AeroSpinner extends LitElement {
  // Size dimension in pixels
  @property({ type: Number }) size = 32;

  // Optional text label to display underneath or next to the ring
  @property({ type: String }) label = "";

  static styles = css`
    :host {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      font-size: 12px;
      color: #435b75;
    }

    .spinner-wrapper {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
    }

    /* The Windows Aero Glossy Loading Ring Track */
    .aero-ring {
      position: relative;
      border-radius: 50%;

      /* Subtle base translucent glass track */
      border: 3px solid rgba(135, 206, 250, 0.25);

      /* The distinct Aero glowing neon-blue swept gradient crest */
      border-top: 3px solid #00a2e8;
      border-right: 3px solid rgba(0, 162, 232, 0.6);
      border-bottom: 3px solid rgba(0, 162, 232, 0.2);
      border-left: 3px solid rgba(0, 162, 232, 0.05);

      /* Add that subtle operating system desktop neon glow shadow overlay */
      box-shadow: 0 0 4px rgba(0, 162, 232, 0.4);

      /* Pure hardware accelerated rotation loop */
      animation: aero-spin 1s linear infinite;
    }

    /* Subtle white glare dot overlay inside the track to heighten skeuomorphism */
    .aero-ring::before {
      content: "";
      position: absolute;
      top: -1px;
      left: 50%;
      width: 4px;
      height: 4px;
      background: #ffffff;
      border-radius: 50%;
      box-shadow:
        0 0 6px #ffffff,
        0 0 2px #00a2e8;
      transform: translateX(-50%);
    }

    .spinner-label {
      text-shadow: 0 1px 0 rgba(255, 255, 255, 0.8);
    }

    @keyframes aero-spin {
      0% {
        transform: rotate(0deg);
      }
      100% {
        transform: rotate(360deg);
      }
    }
  `;

  render() {
    const ringStyle = `width: ${this.size}px; height: ${this.size}px;`;

    return html`
      <div class="spinner-wrapper">
        <div class="aero-ring" style="${ringStyle}"></div>
        ${this.label
          ? html`<span class="spinner-label">${this.label}</span>`
          : ""}
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-spinner": AeroSpinner;
  }
}
