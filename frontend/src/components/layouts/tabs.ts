import {
  LitElement,
  html,
  css,
  type PropertyValues,
  type TemplateResult,
} from "lit";
import { customElement, property, state, query } from "lit/decorators.js";

export interface TabChangedEventDetail {
  activeTab: number;
}

@customElement("roque-tabs")
export class RoqueTabs extends LitElement {
  @property({ type: Number, reflect: true })
  activeTab: number = 0;

  @state()
  private _tabHeaders: string[] = [];

  @query('slot[name="panel"]')
  private _panelSlot!: HTMLSlotElement | null;

  static override styles = css`
    :host {
      display: flex;
      flex-direction: column;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      width: 100%;
    }

    .tab-list {
      display: flex;
      gap: 2px;
      padding: 8px 8px 0 8px;
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.4) 0%,
        rgba(255, 255, 255, 0.15) 60%,
        rgba(0, 0, 0, 0.02) 60.1%,
        rgba(255, 255, 255, 0.25) 100%
      );
      background-color: rgba(185, 215, 235, 0.3);
      border: 1px solid rgba(255, 255, 255, 0.45);
      border-bottom: 1px solid rgba(0, 0, 0, 0.15);
      border-radius: 4px 4px 0 0;
      box-shadow:
        inset 0 1px 0 rgba(255, 255, 255, 0.6),
        0 1px 3px rgba(0, 0, 0, 0.1);
    }

    .tab-button {
      position: relative;
      padding: 5px 16px;
      cursor: pointer;
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.5) 0%,
        rgba(255, 255, 255, 0.1) 100%
      );
      background-color: rgba(230, 240, 245, 0.3);
      border: 1px solid rgba(0, 0, 0, 0.2);
      border-bottom: none;
      border-radius: 3px 3px 0 0;
      font-size: 12px;
      color: #333333;
      font-weight: 400;
      margin-bottom: -1px;
      z-index: 1;
      overflow: hidden;
      transition: all 0.15s ease-in-out;
      text-shadow: 0 0 5px rgba(255, 255, 255, 0.9);
    }

    .tab-button::before {
      content: "";
      position: absolute;
      top: 0; left: 0; right: 0; bottom: 0;
      background: radial-gradient(
        circle at bottom,
        rgba(0, 162, 232, 0.35) 0%,
        rgba(255, 255, 255, 0) 80%
      );
      opacity: 0;
      transition: opacity 0.2s ease;
      pointer-events: none;
      z-index: -1;
    }

    .tab-button:hover::before { opacity: 1; }

    .tab-button:hover {
      color: #000000;
      background-color: rgba(240, 248, 255, 0.6);
      border-color: rgba(0, 0, 0, 0.3) rgba(0, 0, 0, 0.3) transparent rgba(0, 0, 0, 0.3);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.7);
    }

    .tab-button.active {
      color: #000000;
      font-weight: 400;
      background: #ffffff;
      border-color: rgba(0, 0, 0, 0.25);
      border-bottom: 1px solid #ffffff;
      box-shadow:
        inset 0 2px 0 #ff9933,
        inset 0 3px 0 rgba(255, 255, 255, 0.9);
      padding-top: 6px;
      margin-top: -1px;
      z-index: 2;
    }

    .tab-button.active::before { display: none; }

    .tab-panels {
      display: block;
      background-color: #ffffff;
      border: 1px solid rgba(0, 0, 0, 0.2);
      border-top: none;
      padding: 16px;
      box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);
      border-radius: 0 0 4px 4px;
    }

    ::slotted([slot="panel"]:not(.active)) {
      display: none !important;
    }
  `;

  private _handleSlotChange(e: Event): void {
    const slot = e.target as HTMLSlotElement;
    const assignedNodes = slot.assignedElements({ flatten: true });
    this._tabHeaders = assignedNodes.map(
      (node) => node.getAttribute("label") || "Tab",
    );
    this._updateActivePanel();
  }

  private _updateActivePanel(): void {
    if (!this._panelSlot) return;
    const panels = this._panelSlot.assignedElements({ flatten: true });
    panels.forEach((panel, index) => {
      panel.classList.toggle("active", index === this.activeTab);
    });
  }

  protected override updated(changedProperties: PropertyValues<this>): void {
    super.updated(changedProperties);
    if (changedProperties.has("activeTab")) {
      this._updateActivePanel();
      this.dispatchEvent(
        new CustomEvent<TabChangedEventDetail>("tab-changed", {
          detail: { activeTab: this.activeTab },
          bubbles: true,
          composed: true,
        }),
      );
    }
  }

  public selectTab(index: number): void {
    this.activeTab = index;
  }

  protected override render(): TemplateResult {
    return html`
      <div class="tab-list" role="tablist">
        ${this._tabHeaders.map(
          (label, index) => html`
            <button
              class="tab-button ${this.activeTab === index ? "active" : ""}"
              role="tab"
              aria-selected="${this.activeTab === index}"
              @click="${() => this.selectTab(index)}"
            >
              ${label}
            </button>
          `,
        )}
      </div>
      <div class="tab-panels">
        <slot name="panel" @slotchange="${this._handleSlotChange}"></slot>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-tabs": RoqueTabs;
  }
}
