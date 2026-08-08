import { LitElement, html, css } from 'lit'
import { customElement, state } from 'lit/decorators.js'

/**
 * Windows 7-era theme picker.
 *
 * A row of swatch chips (Aero / Olive / Silver) that swap the app's chrome
 * palette by toggling `data-theme` on `<html>` — the tokens in /theme.css
 * re-evaluate instantly, so there is no reload. The choice is persisted to
 * `localStorage["cc_theme"]` (same convention as the auth tokens) and read
 * back by the inline FOUC guard in each page's <head>.
 */

export const THEMES = [
  { id: 'aero', label: 'Aero', swatch: 'linear-gradient(135deg, #9ec8e0 0%, #d1e4ef 70%)' },
  { id: 'olive', label: 'Olive', swatch: 'linear-gradient(135deg, #8a9468 0%, #d9dcc8 70%)' },
  { id: 'silver', label: 'Silver', swatch: 'linear-gradient(135deg, #9aa6ad 0%, #d4d9dd 70%)' },
] as const

export type ThemeId = (typeof THEMES)[number]['id']

const STORAGE_KEY = 'cc_theme'

/** The persisted theme, defaulting to the Aero look. */
export function currentTheme(): ThemeId {
  try {
    const t = localStorage.getItem(STORAGE_KEY)
    if (t === 'aero' || t === 'olive' || t === 'silver') return t
  } catch {
    /* storage unavailable — stay on the default */
  }
  return 'aero'
}

/** Apply + persist a theme (safe to call from any page). */
export function applyTheme(id: ThemeId): void {
  document.documentElement.setAttribute('data-theme', id)
  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch {
    /* storage unavailable — the in-page theme still applies */
  }
}

@customElement('roque-theme-picker')
export class ThemePicker extends LitElement {
  @state() private theme: ThemeId = 'aero'

  connectedCallback() {
    super.connectedCallback()
    this.theme = currentTheme()
  }

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .label {
      margin: 0 0 8px;
      font-size: 11px;
      letter-spacing: 0.4px;
      text-transform: uppercase;
    }

    .chips {
      display: flex;
      gap: 8px;
    }

    .chip {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 4px;
      padding: 6px 8px;
      background: none;
      border: 1px solid transparent;
      border-radius: 5px;
      cursor: pointer;
      font: inherit;
      font-size: 11px;
      color: inherit;
      transition: border-color 0.15s ease, background 0.15s ease, transform 0.15s ease;
    }

    .chip:hover {
      transform: translateY(-1px);
    }

    .chip[aria-checked='true'] {
      border-color: var(--cc-accent, #3c7fb1);
      background: rgba(var(--cc-tint, 173, 216, 230), 0.25);
    }

    .swatch {
      width: 34px;
      height: 22px;
      border-radius: 3px;
      box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.7),
        0 1px 3px rgba(0, 0, 0, 0.25);
      border: 1px solid rgba(0, 0, 0, 0.3);
    }
  `

  private _pick(id: ThemeId) {
    applyTheme(id)
    this.theme = id
  }

  render() {
    return html`
      <p class="label">Theme</p>
      <div class="chips" role="radiogroup" aria-label="Theme">
        ${THEMES.map(
          (t) => html`
            <button
              class="chip"
              role="radio"
              aria-checked="${this.theme === t.id}"
              title="Switch to the ${t.label} theme"
              @click="${() => this._pick(t.id)}"
            >
              <span class="swatch" style="background: ${t.swatch}"></span>
              ${t.label}
            </button>
          `,
        )}
      </div>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-theme-picker': ThemePicker
  }
}
