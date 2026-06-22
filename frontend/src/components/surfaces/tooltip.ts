import { LitElement, html, css } from "lit";
import { property, query } from "lit/decorators.js";

export class RoqueTooltip extends LitElement {
  @property({ type: String }) content = "";
  @property({ type: String }) position = "top";
  @property({ type: Boolean, reflect: true }) visible = false;

  @query(".roque-tooltip-bubble") _tooltipEl!: HTMLDivElement;

  static styles = css`
    :host {
      display: inline-block;
      position: relative;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
    }

    /* Standard high-contrast, crisp vintage desktop tooltip window styling */
    .roque-tooltip-bubble {
      position: absolute;
      background-color: #2c3e50;
      color: #ffffff;
      font-size: 11px;
      line-height: 1.4;
      padding: 6px 10px;
      border-radius: 3px;
      border: 1px solid #1a252f;
      white-space: nowrap;
      z-index: 1000;
      pointer-events: none;
      opacity: 0;
      transform: scale(0.96);
      transition:
        opacity 0.15s ease-in-out,
        transform 0.15s ease-in-out;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
    }

    .roque-tooltip-bubble.visible {
      opacity: 1;
      transform: scale(1);
    }
  `;

  private _showTooltip() {
    this.visible = true;
    this._updatePosition();
  }

  private _hideTooltip() {
    this.visible = false;
    // Reset layout coordinate offsets
    if (this._tooltipEl) {
      this._tooltipEl.style.top = "";
      this._tooltipEl.style.left = "";
    }
  }

  private _updatePosition() {
    // Request an update frame update to ensure the element is painted before measuring
    this.updateComplete.then(() => {
      if (!this._tooltipEl || !this.visible) return;

      const hostRect = this.getBoundingClientRect();
      const tooltipRect = this._tooltipEl.getBoundingClientRect();
      const offset = 8; // Pixel safety buffer spacer

      let top = 0;
      let left = 0;

      switch (this.position) {
        case "top":
          top = -tooltipRect.height - offset;
          left = (hostRect.width - tooltipRect.width) / 2;
          break;
        case "bottom":
          top = hostRect.height + offset;
          left = (hostRect.width - tooltipRect.width) / 2;
          break;
        case "left":
          top = (hostRect.height - tooltipRect.height) / 2;
          left = -tooltipRect.width - offset;
          break;
        case "right":
          top = (hostRect.height - tooltipRect.height) / 2;
          left = hostRect.width + offset;
          break;
      }

      this._tooltipEl.style.top = `${top}px`;
      this._tooltipEl.style.left = `${left}px`;
    });
  }

  render() {
    return html`
      <div
        class="tooltip-trigger-wrapper"
        @mouseenter="${this._showTooltip}"
        @mouseleave="${this._hideTooltip}"
        @focusin="${this._showTooltip}"
        @focusout="${this._hideTooltip}"
      >
        <slot></slot>
      </div>

      <div
        class="roque-tooltip-bubble ${this.visible ? "visible" : ""}"
        aria-hidden="${!this.visible}"
      >
        ${this.content}
      </div>
    `;
  }
}

if (!customElements.get("roque-tooltip")) {
  customElements.define("roque-tooltip", RoqueTooltip);
}
