import { LitElement, html, css, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-avatar")
export class AeroAvatar extends LitElement {
  @property({ type: String }) src = "";
  @property({ type: String }) alt = "User Avatar";
  @property({ type: Number }) size = 48; // Default size in pixels
  /**
   * When true, the avatar wears a green MSN-style "story live" ring — the
   * classic online dot grown into a ring around the picture. Set while the
   * creator has a live (unexpired) 24-hour story.
   */
  @property({ type: Boolean, attribute: "story-active" }) storyActive = false;
  /**
   * Optional click handler wiring: hosts can listen for `aero-avatar-click`
   * to open the story viewer when the ring is present.
   */
  @property({ type: Boolean, attribute: "clickable" }) clickable = false;

  static styles = css`
    :host {
      display: inline-block;
    }

    /* MSN-style story ring: a green circle banded around the frame. The ring
       uses a thick solid green border + a 2px white gap so the avatar reads
       as "online with a story" at a glance (old MSN contact-list style). */
    .story-ring {
      position: relative;
      padding: 3px;
      border-radius: 8px;
      background: linear-gradient(135deg, #2eb82e 0%, #35c759 50%, #2eb82e 100%);
      box-shadow: 0 0 0 1px rgba(30, 110, 30, 0.45), 0 2px 6px rgba(0, 0, 0, 0.25);
      /* The ring is the frame itself: white gap between ring and avatar. */
      width: fit-content;
    }

    .story-ring.clickable {
      cursor: pointer;
      transition: transform 0.15s ease, box-shadow 0.15s ease;
    }

    .story-ring.clickable:hover {
      transform: scale(1.05);
      box-shadow: 0 0 0 1px rgba(30, 110, 30, 0.55), 0 3px 10px rgba(0, 0, 0, 0.3);
    }

    .story-ring.clickable:active {
      transform: scale(0.98);
    }

    /* Keyboard focus on the clickable ring (Enter/Space handled). */
    .story-ring.clickable:focus-visible {
      outline: 2px solid #2eb82e;
      outline-offset: 2px;
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

    const frame = html`
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

    if (!this.storyActive) return frame;

    return html`
      <div
        class="story-ring ${this.clickable ? "clickable" : ""}"
        role="${this.clickable ? "button" : nothing}"
        tabindex="${this.clickable ? 0 : nothing}"
        aria-label="${this.clickable ? "Story available — click to view" : nothing}"
        @click="${this._onRingClick}"
        @keydown="${(e: KeyboardEvent) => {
          if (this.clickable && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            this._onRingClick();
          }
        }}"
      >
        ${frame}
      </div>
    `;
  }

  private _onRingClick() {
    if (!this.clickable) return;
    this.dispatchEvent(
      new CustomEvent("aero-avatar-click", { bubbles: true, composed: true }),
    );
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-avatar": AeroAvatar;
  }
}
