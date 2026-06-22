import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-grid-item")
export class AeroGridItem extends LitElement {
  // How many columns this item should span
  @property({ type: Number }) span = 1;

  static styles = css`
    :host {
      display: block;
    }
    .aero-grid-item {
      width: 100%;
      height: 100%;
    }
  `;

  render() {
    return html`
      <div
        class="aero-grid-item"
        style="grid-column: span ${this.span} / span ${this.span};"
      >
        <slot></slot>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-grid-item": AeroGridItem;
  }
}
