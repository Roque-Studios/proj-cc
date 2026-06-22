import { LitElement, html, css } from "lit";

export class AeroMenu extends LitElement {
  static properties = {
    open: { type: Boolean, reflect: true },
  };

  open = false;

  static styles = css`
    :host {
      display: inline-block;
      position: relative;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
    }

    /* The Main Menu Overlay Box Frame Dropdown Container */
    .aero-menu-dropdown {
      position: absolute;
      top: 100%;
      left: 0;
      z-index: 1050;
      display: none;
      min-width: 160px;
      padding: 3px; /* Standard compact desktop padding boundaries */
      margin: 2px 0 0;
      list-style: none;
      background-color: #ffffff;

      /* Pure sharp desktop application borders with strong drop depth shadows */
      border: 1px solid #979797;
      border-radius: 3px;
      box-shadow: 0 4px 10px rgba(0, 0, 0, 0.25);
    }

    /* Reveal dropdown window layout layer when toggled active */
    :host([open]) .aero-menu-dropdown {
      display: block;
    }

    /* Global styling overrides targeting inner slot menu-items directly */
    ::slotted(roque-menu-item) {
      display: block;
    }
  `;

  render() {
    return html`
      <div class="aero-menu-dropdown">
        <slot></slot>
      </div>
    `;
  }
}

if (!customElements.get("roque-menu")) {
  customElements.define("roque-menu", AeroMenu);
}
