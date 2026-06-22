import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-stack")
export class AeroStack extends LitElement {
  // Spacing between items in pixels (defaults to 12px for standard desktop gaps)
  @property({ type: Number }) spacing = 12;

  // Alignment of children (stretch, start, center, end)
  @property({ type: String }) align = "stretch";

  static styles = css`
    :host {
      display: block;
      width: 100%;
    }

    .aero-stack {
      display: flex;
      flex-direction: column;
      width: 100%;
      box-sizing: border-box;
    }
  `;

  render() {
    return html`
      <div
        class="aero-stack"
        style="gap: ${this.spacing}px; align-items: ${this.align};"
      >
        <slot></slot>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-stack": AeroStack;
  }
}
