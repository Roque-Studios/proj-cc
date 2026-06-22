import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-grid")
export class AeroGrid extends LitElement {
  // Number of columns on desktop layout (defaults to 12-column grid system)
  @property({ type: Number }) columns = 12;

  // Gap sizing between cells in pixels
  @property({ type: Number }) gap = 15;

  static styles = css`
    :host {
      display: block;
      width: 100%;
    }

    .aero-grid {
      display: grid;
      width: 100%;
      box-sizing: border-box;
    }
  `;

  render() {
    return html`
      <div
        class="aero-grid"
        style="grid-template-columns: repeat(${this
          .columns}, minmax(0, 1fr)); gap: ${this.gap}px;"
      >
        <slot></slot>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-grid": AeroGrid;
  }
}
