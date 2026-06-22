import { LitElement, html, css } from 'lit';
import { customElement, property } from 'lit/decorators.js';

const AeroAction = {
    FetchStatus: "FETCH_STATUS",
}

export type AeroActionType = typeof AeroAction[keyof typeof AeroAction]

@customElement('roque-button')
export class AeroButton extends LitElement {
  // A unique identifier so the parent knows WHICH button was clicked
  @property({ type: String }) buttonId = '';
  
  // A generic property to pass an extra string parameter if needed (like a URL or a Route)
  @property({ type: String }) context = '';

  static styles = css`
    :host {
      display: inline-block;
    }

    .aero-btn {
      position: relative;
      padding: 6px 18px;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
      font-size: 13px;
      color: #000000;
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.55) 0%,
        rgba(255, 255, 255, 0.20) 50%,
        rgba(0, 0, 0, 0.05) 50.1%,
        rgba(255, 255, 255, 0.15) 100%
      );
      background-color: rgba(173, 216, 230, 0.4); /* Base Aero Blue Tint */
      border: 1px solid rgba(255, 255, 255, 0.6);
      outline: 1px solid rgba(0, 0, 0, 0.25);
      outline-offset: -1px;
      border-radius: 3px;
      box-shadow: 
        0 1px 3px rgba(0, 0, 0, 0.15),
        inset 0 1px 0 rgba(255, 255, 255, 0.7);
      cursor: pointer;
      overflow: hidden;
      transition: all 0.2s ease-in-out;
      text-shadow: 0 0 4px rgba(255, 255, 255, 0.8);
    }

    /* Radial hover glow effect characteristic of Aero */
    .aero-btn::before {
      content: '';
      position: absolute;
      top: 0; left: 0; right: 0; bottom: 0;
      background: radial-gradient(
        circle at center,
        rgba(255, 255, 255, 0.6) 0%,
        rgba(255, 255, 255, 0) 70%
      );
      opacity: 0;
      transition: opacity 0.3s ease;
      pointer-events: none;
    }

    .aero-btn:hover::before {
      opacity: 1;
    }

    .aero-btn:hover {
      background-color: rgba(200, 235, 255, 0.6);
      outline-color: rgba(60, 128, 172, 0.6);
      box-shadow: 
        0 0 5px rgba(0, 162, 232, 0.5),
        inset 0 1px 0 rgba(255, 255, 255, 0.8);
    }

    /* Pressed state */
    .aero-btn:active {
      background: linear-gradient(
        to bottom,
        rgba(0, 0, 0, 0.1) 0%,
        rgba(0, 0, 0, 0.05) 50%,
        rgba(255, 255, 255, 0.1) 50.1%,
        rgba(255, 255, 255, 0.2) 100%
      );
      background-color: rgba(135, 180, 210, 0.5);
      outline-color: rgba(30, 70, 100, 0.7);
      box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.2);
    }
  `;

  private async _handleClick(e: Event) {
    e.preventDefault()
    this.dispatchEvent(new CustomEvent('aero-click', {
        bubbles: true,
        composed: true,
        detail: {
            buttonId: this.buttonId,
            context: this.context,
        }
    }))
  }

  render() {
    return html`
      <button class="aero-btn" @click="${this._handleClick}">
        <slot></slot>
      </button>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-button': AeroButton;
  }
}