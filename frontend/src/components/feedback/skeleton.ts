import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";

@customElement("roque-skeleton")
export class AeroSkeleton extends LitElement {
  /**
   * Shape variation: 'text' | 'rect' | 'circle'
   */
  @property({ type: String, reflect: true }) variant = "text";

  /**
   * Explicit width (e.g., '100%', '120px', '40px')
   */
  @property({ type: String }) width = "";

  /**
   * Explicit height (e.g., '16px', '150px', '40px')
   */
  @property({ type: String }) height = "";

  static styles = css`
    :host {
      display: inline-block;
      width: 100%;
      vertical-align: middle;
    }

    .aero-skeleton {
      position: relative;
      overflow: hidden;
      background-color: #e6e6e6;
      border: 1px solid #dcdcdc;
      width: 100%;
      height: 100%;
    }

    /* Variant Shapes */
    :host([variant="text"]) .aero-skeleton {
      height: 12px;
      border-radius: 2px;
      margin-top: 4px;
      margin-bottom: 4px;
    }

    :host([variant="rect"]) .aero-skeleton {
      border-radius: 3px;
    }

    :host([variant="circle"]) .aero-skeleton {
      border-radius: 50%;
    }

    /* Shimmer Shading Sweep Animation */
    .aero-skeleton::after {
      position: absolute;
      top: 0;
      right: 0;
      bottom: 0;
      left: 0;
      transform: translateX(-100%);
      background-image: linear-gradient(
        90deg,
        rgba(255, 255, 255, 0) 0%,
        rgba(255, 255, 255, 0.6) 20%,
        rgba(255, 255, 255, 0.9) 60%,
        rgba(255, 255, 255, 0) 100
      );
      animation: shimmer 1.6s infinite ease-in-out;
      content: "";
    }

    @keyframes shimmer {
      100% {
        transform: translateX(100%);
      }
    }
  `;

  render() {
    // Dynamically map properties to local layout inline styling hooks
    const inlineStyles = `
      ${this.width ? `width: ${this.width};` : ""}
      ${this.height ? `height: ${this.height};` : ""}
    `;

    return html`
      <div
        class="aero-skeleton"
        style="${inlineStyles}"
        aria-hidden="true"
      ></div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-skeleton": AeroSkeleton;
  }
}
