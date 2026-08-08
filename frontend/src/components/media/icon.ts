import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";
// Import Lit's official secure string-to-SVG injection directive
import { unsafeSVG } from "lit/directives/unsafe-svg.js";

// Dictionary of classic icons used across Metrica
const ICON_REGISTRY: Record<string, string> = {
  tiktok: `<path fill="currentColor" d="M12.525.02c1.31-.02 2.61-.01 3.91-.02.08 1.53.63 3.02 1.63 4.19 1.34 1.3 3.2 1.95 5.07 1.83v3.85c-1.74.07-3.47-.46-4.88-1.5-.28-.21-.55-.45-.79-.7-.03 2.94.01 5.88-.02 8.81-.1 2.3-1.07 4.54-2.77 6.13-2.14 2.1-5.32 2.91-8.21 2.15-2.9-.71-5.34-2.95-6.22-5.83-.98-3.08-.24-6.61 1.93-9.02 1.84-2.14 4.63-3.26 7.44-2.99v3.94c-1.72-.25-3.49.33-4.66 1.63-1.14 1.21-1.52 3-.98 4.6.53 1.65 2.11 2.79 3.85 2.79 1.8 0 3.32-1.34 3.58-3.12.06-1.59.03-3.19.03-4.79v-11.7c-.01-.03-.01-.05-.01-.08z"/>`,
  instagram: `<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M7 2h10a5 5 0 0 1 5 5v10a5 5 0 0 1-5 5H7a5 5 0 0 1-5-5V7a5 5 0 0 1 5-5zm9.5 4.5h.01M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z"/>`,
  youtube: `<path fill="currentColor" d="M23.498 6.163a3.003 3.003 0 0 0-2.11-2.11C19.517 3.545 12 3.545 12 3.545s-7.516 0-9.387.507a3.003 3.003 0 0 0-2.11 2.11C0 8.033 0 12 0 12s0 3.967.503 5.837a3.003 3.003 0 0 0 2.11 2.11c1.871.507 9.387.507 9.387.507s7.517 0 9.387-.507a3.003 3.003 0 0 0 2.11-2.11C24 15.967 24 12 24 12s0-3.967-.502-5.837zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>`,
  x: `<path fill="currentColor" d="M18.901 1.153h3.68l-8.04 9.19L24 22.846h-7.406l-5.8-7.584-6.638 7.584H.474l8.6-9.83L0 1.154h7.594l5.243 6.932zM17.61 20.644h2.039L6.486 3.24H4.298z"/>`,
  twitter: `<path fill="currentColor" d="M18.901 1.153h3.68l-8.04 9.19L24 22.846h-7.406l-5.8-7.584-6.638 7.584H.474l8.6-9.83L0 1.154h7.594l5.243 6.932zM17.61 20.644h2.039L6.486 3.24H4.298z"/>`,
  link: `<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>`,
  lock: `<rect x="3" y="11" width="18" height="11" rx="2" ry="2" fill="none" stroke="currentColor" stroke-width="2"/><path fill="none" stroke="currentColor" stroke-width="2" d="M7 11V7a5 5 0 0 1 10 0v4"/>`,
  search: `<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" d="M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zm10 2l-4.35-4.35"/>`,
  info: `<path fill="none" stroke="currentColor" stroke-width="2" d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10zm0-11v5m0-8h.01"/>`,
  // Mobile-first hamburger menu (three stacked lines).
  menu: `<path fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" d="M4 6h16M4 12h16M4 18h16"/>`,
  // Person silhouette for the profile menu item.
  user: `<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4" fill="none" stroke="currentColor" stroke-width="2"/>`,
  logout: `<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M16 17l5-5-5-5M21 12H9"/>`,
  clock: `<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M12 6v6l4 2"/>`,
  key: `<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/>`,
  // Left arrow for back navigation (profile page → feed).
  back: `<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M19 12H5M12 19l-7-7 7-7"/>`,
  // Message bubble for DM entry points.
  chat: `<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>`,
  // Heart outline — the "like" action.
  heart: `<path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>`,
  // Heart filled — the "liked" state.
  'heart-filled': `<path fill="currentColor" d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/>`,
  // Photo attachment for the DM composer.
  image: `<rect x="3" y="3" width="18" height="18" rx="2" fill="none" stroke="currentColor" stroke-width="2"/><circle cx="8.5" cy="8.5" r="1.5" fill="currentColor"/><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M21 15l-5-5L5 21"/>`,
};

@customElement("roque-icon")
export class AeroIcon extends LitElement {
  @property({ type: String }) name = "";
  @property({ type: Number }) size = 16;

  static styles = css`
    :host {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      vertical-align: middle;
      line-height: 1;
    }

    svg {
      display: block;
      width: 100%;
      height: 100%;
    }
  `;

  render() {
    const iconPath = ICON_REGISTRY[this.name.toLowerCase()] || "";

    return html`
      <svg
        style="width: ${this.size}px; height: ${this.size}px;"
        viewBox="0 0 24 24"
      >
        ${iconPath ? unsafeSVG(iconPath) : ""}
      </svg>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-icon": AeroIcon;
  }
}
