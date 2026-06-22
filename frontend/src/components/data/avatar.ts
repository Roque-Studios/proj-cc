import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-avatar")
export class AeroAvatar extends LitElement {
  @property({ type: String }) src = "";
  @property({ type: String }) alt = "User Avatar";
  @property({ type: Number }) size = 48; // Default size in pixels

  static styles = css`
    :host {
      display: inline-block;
    }

    /* The Iconic Windows 7 Start Menu Picture Border Frame */
    .aero-avatar-frame {
      position: relative;
      background: #ffffff;
      padding: 4px; /* White inner border padding */
      border: 1px solid rgba(0, 0, 0, 0.35);
      border-radius: 4px;

      /* Multi-layered drop shadow for that elevated, floating look */
      box-shadow:
        0 2px 5px rgba(0, 0, 0, 0.25),
        inset 0 1px 0 rgba(255, 255, 255, 0.6);

      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }

    /* Glass overlay reflection across the profile image */
    .aero-avatar-frame::after {
      content: "";
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: linear-gradient(
        135deg,
        rgba(255, 255, 255, 0.4) 0%,
        rgba(255, 255, 255, 0.1) 45%,
        rgba(255, 255, 255, 0) 50%,
        rgba(255, 255, 255, 0) 100%
      );
      pointer-events: none;
      z-index: 2;
    }

    .aero-avatar-img {
      display: block;
      width: 100%;
      height: 100%;
      object-fit: cover;
      border: 1px solid rgba(0, 0, 0, 0.15); /* Soft border around the image itself */
      border-radius: 2px;
    }

    /* Fallback avatar box when image fails or isn't provided */
    .aero-avatar-fallback {
      width: 100%;
      height: 100%;
      background: linear-gradient(to bottom, #bcccdb 0%, #8faec4 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      color: #ffffff;
      font-family: "Segoe UI", sans-serif;
      font-weight: bold;
      text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
    }
  `;

  render() {
    // Dynamically calculate frame sizing based on property inputs
    const frameStyle = `width: ${this.size}px; height: ${this.size}px;`;
    const fontSize = `${Math.max(this.size * 0.4, 12)}px`;

    return html`
      <div class="aero-avatar-frame" style="${frameStyle}">
        ${this.src
          ? html`
              <img
                class="aero-avatar-img"
                src="${this.src}"
                alt="${this.alt}"
                @error="${() => (this.src = "")}"
              />
            `
          : html`
              <div class="aero-avatar-fallback" style="font-size: ${fontSize}">
                ${this.alt ? this.alt.charAt(0).toUpperCase() : "U"}
              </div>
            `}
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-avatar": AeroAvatar;
  }
}
