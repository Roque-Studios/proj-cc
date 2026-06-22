import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-container")
export class AeroContainer extends LitElement {
  // Option to make the container fluid (100% width) or fixed maximum width
  @property({ type: Boolean }) fluid = false;

  static styles = css`
    :host {
      display: block;
      width: 100%;
      box-sizing: border-box;
    }

    /* The Main Aero Window Canvas background layer */
    .aero-canvas {
      width: 100%;
      margin-left: auto;
      margin-right: auto;
      padding: 20px;
      box-sizing: border-box;

      /* Classic Windows 7 / Vista client background canvas gradient */
      background: linear-gradient(
        180deg,
        #f2f7fa 0%,
        #e5eef4 40%,
        #dbe7f0 100%
      );
      background-color: #e5eef4;

      /* Crisp, subtle interior drop shadow from the main OS chrome windows */
      box-shadow: inset 0 2px 6px rgba(0, 0, 0, 0.05);
      border-radius: 3px;
      border: 1px solid #b9cbd7;
    }

    /* Layout structural sizing options */
    .fixed-width {
      max-width: 1200px;
    }

    .fluid-width {
      max-width: 100%;
    }
  `;

  render() {
    return html`
      <div class="aero-canvas ${this.fluid ? "fluid-width" : "fixed-width"}">
        <slot></slot>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-container": AeroContainer;
  }
}
