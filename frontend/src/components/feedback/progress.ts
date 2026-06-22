import { LitElement, html, css } from 'lit';
import { customElement, property } from 'lit/decorators.js';

@customElement('roque-progress')
export class AeroProgress extends LitElement {
  // Value from 0 to 100
  @property({ type: Number }) value = 0;

  // Max value defaulting to 100
  @property({ type: Number }) max = 100;

  // Status state to change color: 'normal' (green), 'paused' (yellow), 'error' (red)
  @property({ type: String }) status = 'normal';

  static styles = css`
    :host {
      display: block;
      width: 100%;
    }

    /* Track/Container */
    .aero-progress-track {
      height: 14px;
      background: #e6e6e6;
      border: 1px solid #b6b6b6;
      outline: 1px solid #e2e2e2;
      outline-offset: -2px;
      border-radius: 2px;
      box-shadow: inset 1px 1px 3px rgba(0, 0, 0, 0.15);
      overflow: hidden;
      position: relative;
    }

    /* The Filled Bar */
    .aero-progress-bar {
      height: 100%;
      width: 0%;
      transition: width 0.3s ease-out;
      position: relative;
      overflow: hidden;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.4);
    }

    /* Aero Green Theme */
    .status-normal {
      background: linear-gradient(
        to bottom,
        #b2e1b2 0%,
        #79ce79 40%,
        #49b649 50%,
        #3fa33f 50.1%,
        #5db95d 100%
      );
      border-right: 1px solid #2d752d;
    }

    /* Aero Paused/Warning Yellow */
    .status-paused {
      background: linear-gradient(
        to bottom,
        #fce8b2 0%,
        #f9d279 40%,
        #f5b849 50%,
        #ef9e3f 50.1%,
        #f2b25d 100%
      );
      border-right: 1px solid #aa6f25;
    }

    /* Aero Error Red */
    .status-error {
      background: linear-gradient(
        to bottom,
        #f7baba 0%,
        #ee8888 40%,
        #e45353 50%,
        #d13b3b 50.1%,
        #db5757 100%
      );
      border-right: 1px solid #8e2323;
    }

    /* The Moving Aero Glow Glare */
    .aero-progress-bar::after {
      content: '';
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
      bottom: 0;
      background: linear-gradient(
        to right,
        rgba(255, 255, 255, 0) 0%,
        rgba(255, 255, 255, 0) 20%,
        rgba(255, 255, 255, 0.4) 50%,
        rgba(255, 255, 255, 0) 80%,
        rgba(255, 255, 255, 0) 100%
      );
      background-size: 100px 100%;
      background-repeat: repeat-x;
      animation: aero-pulse 1.8s linear infinite;
    }

    @keyframes aero-pulse {
      0% {
        background-position: -100px 0;
      }
      100% {
        background-position: 200px 0;
      }
    }
  `;

  render() {
    // Calculate percentage bounded between 0 and 100
    const percentage = Math.min(Math.max((this.value / this.max) * 100, 0), 100);

    return html`
      <div 
        class="aero-progress-track" 
        role="progressbar" 
        aria-valuenow="${this.value}" 
        aria-valuemin="0" 
        aria-valuemax="${this.max}"
      >
        <div 
          class="aero-progress-bar status-${this.status}" 
          style="width: ${percentage}%"
        ></div>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-progress': AeroProgress;
  }
}