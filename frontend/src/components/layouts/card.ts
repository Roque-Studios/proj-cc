import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-card")
export class AeroCard extends LitElement {
  @property({ type: String }) heading = "";

  static styles = css`
    :host {
      display: block;
      width: 100%;
    }

    /* The main Aero Glass outer frame window container */
    .aero-card-frame {
      position: relative;
      border-radius: 5px;

      /* Essential Aero glass gradient stack blending translucent borders */
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.45) 0%,
        rgba(255, 255, 255, 0.2) 15%,
        rgba(var(--cc-tint), 0.25) 15.1%,
        /* Subtle Windows Blue tint accent */ rgba(var(--cc-tint), 0.15) 100%
      );
      background-color: rgba(var(--cc-glass), 0.35);

      /* Dual border overlay: Outer dark edge & Inner bright glare edge */
      border: 1px solid rgba(0, 0, 0, 0.35);
      padding: 6px; /* Thick glass framing window gap width */

      box-shadow:
        0 5px 15px rgba(0, 0, 0, 0.25),
        /* Soft ambient window shadow */ inset 0 1px 0 rgba(255, 255, 255, 0.6); /* High reflection crest */
    }

    /* The bright glare accent line typical of Windows 7 window glass padding */
    .aero-card-frame::before {
      content: "";
      position: absolute;
      top: 1px;
      left: 1px;
      right: 1px;
      bottom: 1px;
      border: 1px solid rgba(255, 255, 255, 0.45);
      border-radius: 4px;
      pointer-events: none;
    }

    /* Elegant frosted glass diagonal glare stripes across the header zone */
    .aero-card-header-glare {
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      height: 40px;
      background: linear-gradient(
        125deg,
        rgba(255, 255, 255, 0.3) 0%,
        rgba(255, 255, 255, 0.1) 30%,
        rgba(255, 255, 255, 0) 30.1%
      );
      pointer-events: none;
      border-radius: 4px 4px 0 0;
    }

    .aero-card-title {
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      font-size: 13px;
      color: var(--cc-heading);
      margin: 2px 0 6px 6px;
      text-shadow:
        0 0 6px rgba(255, 255, 255, 0.9),
        0 0 10px rgba(255, 255, 255, 0.9);
      font-weight: normal;
    }

    /* The Inset Client Area - housing the actual core form content safely */
    .aero-card-client-area {
      background-color: var(--cc-client);
      border: 1px solid var(--cc-border);
      border-top-color: #6d6d6d; /* Deeper top socket shadows */
      border-radius: 2px;
      padding: 15px;
      box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.08);
    }
  `;

  render() {
    return html`
      <div class="aero-card-frame">
        <div class="aero-card-header-glare"></div>

        ${this.heading
          ? html`<div class="aero-card-title">${this.heading}</div>`
          : ""}

        <div class="aero-card-client-area">
          <slot></slot>
        </div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-card": AeroCard;
  }
}
