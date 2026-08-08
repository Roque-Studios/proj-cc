import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-divider")
export class AeroDivider extends LitElement {
  @property({ type: String }) orientation = "horizontal";
  @property({ type: Number }) spacing = 15;

  static styles = css`
    /* Force the host element itself to respect block dimensions and avoid flex collapse */
    :host {
      display: block !important;
      box-sizing: border-box;
    }

    /* Adjust host configuration if the user explicitly wants a vertical separator side-by-side */
    :host([orientation="vertical"]) {
      display: inline-block !important;
      align-self: stretch; /* Forces it to match parent flex heights natively */
      height: auto;
    }

    .aero-divider {
      position: relative;
      box-sizing: border-box;
    }

    /* Fixed Horizontal Line Layer */
    .horizontal {
      width: 100%;
      height: 0;
      border-top: 1px solid var(--cc-border-soft); /* Subtle Shadow line */
      border-bottom: 1px solid var(--cc-client); /* Glint highlight line */
    }

    /* Fixed Vertical Line Layer */
    .vertical {
      display: inline-block;
      /* Using a minimum height viewport fallback so it never collapses to 0px */
      height: 100%;
      min-height: 16px;
      width: 0;
      border-left: 1px solid var(--cc-border-soft); /* Shadow line */
      border-right: 1px solid var(--cc-client); /* Glint highlight line */
      vertical-align: middle;
    }
  `;

  render() {
    const isVertical = this.orientation === "vertical";
    const spacingStyle = isVertical
      ? `margin-left: ${this.spacing}px; margin-right: ${this.spacing}px; height: 100%;`
      : `margin-top: ${this.spacing}px; margin-bottom: ${this.spacing}px;`;

    return html`
      <div
        class="aero-divider ${this.orientation}"
        style="${spacingStyle}"
      ></div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-divider": AeroDivider;
  }
}
