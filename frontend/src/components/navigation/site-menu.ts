import { LitElement, html, css, nothing } from 'lit'
import { customElement, property } from 'lit/decorators.js'

import '../media/icon.ts'
import type { UserMe } from '../../lib/api'

/**
 * Mobile-first site menu (hamburger).
 *
 * A slim sticky top bar with a hamburger button (left) and the brand (right).
 * Tapping the hamburger slides a drawer in from the left with **role-based
 * items**:
 *
 * - anonymous visitors → **Sign in** / **Create account** (both land on
 *   `/login`, which has the register flow built in);
 * - signed-in users → **My profile** (`/profile`) and **Sign out**.
 *
 * The drawer closes on item tap, backdrop tap or Esc. Emits `aero-logout`
 * when Sign out is chosen so the host can clear tokens + refresh.
 */
@customElement('roque-site-menu')
export class SiteMenu extends LitElement {
  /** The signed-in user, or null for anonymous visitors. */
  @property({ type: Object }) user: UserMe | null = null
  /** Brand text shown in the top bar (defaults to nothing). */
  @property({ type: String }) brand = ''

  // Reflected so the :host([open]) attribute selectors (scrim + drawer)
  // actually match — a plain @state would toggle the property but never the
  // attribute, leaving the drawer invisible (the same trap the existing
  // roque-menu / roque-dialog avoid with reflect: true).
  @property({ type: Boolean, reflect: true }) open = false

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .topbar {
      position: sticky;
      top: 0;
      z-index: 900;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      padding: 8px 12px;
      background: linear-gradient(
        to bottom,
        rgba(28, 45, 63, 0.96),
        rgba(20, 33, 47, 0.96)
      );
      color: #eaf1f8;
      box-shadow: 0 1px 6px rgba(0, 0, 0, 0.35);
    }

    .burger {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 38px;
      height: 38px;
      border: 1px solid rgba(255, 255, 255, 0.25);
      border-radius: 4px;
      background: rgba(255, 255, 255, 0.06);
      color: #eaf1f8;
      cursor: pointer;
      transition: background 0.15s ease, transform 0.15s ease;
    }

    .burger:hover {
      background: rgba(255, 255, 255, 0.16);
    }

    .burger:active {
      transform: scale(0.94);
    }

    .brand {
      font-size: 14px;
      font-weight: 600;
      letter-spacing: 0.3px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    /* Slide-in drawer */
    .scrim {
      position: fixed;
      inset: 0;
      z-index: 950;
      background: rgba(0, 0, 0, 0.45);
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.2s ease;
    }

    :host([open]) .scrim {
      opacity: 1;
      pointer-events: auto;
    }

    .drawer {
      position: fixed;
      top: 0;
      left: 0;
      bottom: 0;
      z-index: 960;
      width: min(300px, 82vw);
      background: #f4f8fb;
      box-shadow: 3px 0 18px rgba(0, 0, 0, 0.4);
      transform: translateX(-100%);
      transition: transform 0.22s cubic-bezier(0.2, 0.8, 0.3, 1);
      display: flex;
      flex-direction: column;
    }

    :host([open]) .drawer {
      transform: translateX(0);
    }

    .drawer-head {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 14px 14px 12px;
      background: linear-gradient(to bottom, #1c2d3f, #22384f);
      color: #eaf1f8;
    }

    .drawer-head .avatar {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 34px;
      height: 34px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.14);
      color: #cfe3f5;
    }

    .drawer-head .who {
      min-width: 0;
    }

    .drawer-head .who-name {
      font-size: 14px;
      font-weight: 600;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .drawer-head .who-sub {
      font-size: 11px;
      color: #9db4c9;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .nav-list {
      display: flex;
      flex-direction: column;
      padding: 8px;
      gap: 2px;
      overflow-y: auto;
    }

    .nav-item {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 11px 12px;
      border: 1px solid transparent;
      border-radius: 4px;
      font-size: 13px;
      color: #1e2a38;
      cursor: pointer;
      user-select: none;
      transition: background 0.12s ease, border-color 0.12s ease;
    }

    .nav-item:hover {
      border-color: #b8d6f3;
      background: linear-gradient(to bottom, #fafcfe 0%, #e3edf9 100%);
    }

    .nav-item roque-icon {
      color: #3a6a96;
    }

    .nav-item.signout {
      color: #a0281c;
    }

    .nav-item.signout roque-icon {
      color: #a0281c;
    }

    .nav-note {
      margin: 10px 12px 4px;
      font-size: 11px;
      color: #6b7a8a;
      line-height: 1.5;
    }
  `

  private _close() {
    this.open = false
  }

  private _onKeydown = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && this.open) this._close()
  }

  connectedCallback() {
    super.connectedCallback()
    window.addEventListener('keydown', this._onKeydown)
  }

  disconnectedCallback() {
    super.disconnectedCallback()
    window.removeEventListener('keydown', this._onKeydown)
  }

  private _go(url: string) {
    this._close()
    window.location.href = url
  }

  private _signOut() {
    this._close()
    this.dispatchEvent(
      new CustomEvent('aero-logout', { bubbles: true, composed: true }),
    )
  }

  render() {
    const signedIn = !!this.user
    const who = signedIn
      ? this.user?.username || this.user?.email || 'Account'
      : 'Guest'

    return html`
      <div class="topbar">
        <button
          class="burger"
          aria-label="Open menu"
          aria-expanded="${this.open}"
          @click="${() => (this.open = !this.open)}"
        >
          <roque-icon name="menu" size="22"></roque-icon>
        </button>
        ${this.brand ? html`<div class="brand">${this.brand}</div>` : nothing}
        <span style="width: 38px"></span>
      </div>

      <div class="scrim" @click="${this._close}"></div>

      <nav class="drawer" aria-label="Site menu">
        <div class="drawer-head">
          <span class="avatar"><roque-icon name="user" size="18"></roque-icon></span>
          <div class="who">
            <div class="who-name">${who}</div>
            <div class="who-sub">${signedIn ? this.user?.email ?? '' : 'Not signed in'}</div>
          </div>
        </div>

        <div class="nav-list">
          ${signedIn
            ? html`
                <div class="nav-item" role="menuitem" @click="${() => this._go('/profile')}">
                  <roque-icon name="user" size="16"></roque-icon>
                  <span>My profile</span>
                </div>
                <div class="nav-item signout" role="menuitem" @click="${this._signOut}">
                  <roque-icon name="logout" size="16"></roque-icon>
                  <span>Sign out</span>
                </div>
              `
            : html`
                <div class="nav-item" role="menuitem" @click="${() => this._go('/login')}">
                  <roque-icon name="user" size="16"></roque-icon>
                  <span>Sign in</span>
                </div>
                <div class="nav-item" role="menuitem" @click="${() => this._go('/login')}">
                  <roque-icon name="key" size="16"></roque-icon>
                  <span>Create account</span>
                </div>
                <p class="nav-note">
                  Create a free account to subscribe to creators and see the
                  full feed.
                </p>
              `}
        </div>
      </nav>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-site-menu': SiteMenu
  }
}
