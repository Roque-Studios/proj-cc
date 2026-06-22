import { LitElement, html, css, type TemplateResult } from 'lit';
import { customElement, property } from 'lit/decorators.js';

@customElement('roque-dialog')
export class RoqueDialog extends LitElement {
  // Controls the visibility of the dialog window
  @property({ type: Boolean, reflect: true }) open = false;

  // Title text displayed in the frosted glass header bar
  @property({ type: String }) windowTitle = 'Windows Security';

  static override styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    /* Dimmed overlay background behind the Aero Window */
    .overlay {
      position: fixed;
      top: 0;
      left: 0;
      width: 100vw;
      height: 100vh;
      background-color: rgba(0, 0, 0, 0.2);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 9999;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.2s ease-out;
    }

    :host([open]) .overlay {
      opacity: 1;
      pointer-events: auto;
    }

    /* Core Windows Aero Window Frame Style */
    .window-frame {
      position: relative;
      width: 100%;
      max-width: 440px;
      background: linear-gradient(
        to bottom,
        rgba(255, 255, 255, 0.45) 0%,
        rgba(255, 255, 255, 0.2) 40%,
        rgba(0, 0, 0, 0.02) 40.1%,
        rgba(255, 255, 255, 0.25) 100%
      );
      background-color: rgba(165, 200, 225, 0.45); /* Aero Glass Blue Tint */
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border: 1px solid rgba(255, 255, 255, 0.6);
      outline: 1px solid rgba(0, 0, 0, 0.35);
      outline-offset: -1px;
      border-radius: 7px;
      box-shadow: 
        0 10px 30px rgba(0, 0, 0, 0.3),
        inset 0 1px 0 rgba(255, 255, 255, 0.6);
      transform: scale(0.95);
      transition: transform 0.15s cubic-bezier(0.1, 0.8, 0.3, 1);
      overflow: hidden;
    }

    :host([open]) .window-frame {
      transform: scale(1);
    }

    /* Glass Title Bar */
    .title-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 7px 10px 6px 12px;
      cursor: default;
    }

    .title-text {
      font-size: 12px;
      color: #1e1e1e;
      text-shadow: 0 0 6px rgba(255, 255, 255, 1), 0 0 3px rgba(255, 255, 255, 0.8);
    }

    /* Close Button Custom Window Control Asset */
    .close-btn {
      position: relative;
      width: 45px;
      height: 18px;
      background: linear-gradient(to bottom, rgba(255, 120, 120, 0.3) 0%, rgba(200, 40, 40, 0.2) 100%);
      border: 1px solid rgba(0, 0, 0, 0.3);
      border-radius: 2px;
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.3);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: all 0.1s ease;
    }

    .close-btn::before {
      content: '×';
      color: rgba(0, 0, 0, 0.7);
      font-size: 16px;
      font-weight: 300;
      line-height: 18px;
    }

    .close-btn:hover {
      background: linear-gradient(to bottom, #f68c7d 0%, #cb3c2c 50%, #b22d1e 50.1%, #ea543f 100%);
      border-color: #7a1d12;
      box-shadow: 0 0 6px rgba(234, 84, 63, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.4);
    }
    
    .close-btn:hover::before {
      color: #ffffff;
      text-shadow: 0 1px 2px rgba(0, 0, 0, 0.5);
    }

    .close-btn:active {
      background: linear-gradient(to bottom, #b83324 0%, #a12316 100%);
      box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.5);
    }

    /* Inner Dialog Content Panel - Opaque Windows Canvas */
    .window-body {
      background-color: #f0f0f0; /* Standard OS container gray */
      margin: 0 7px 7px 7px; /* Reveals the glossy outer frame border padding */
      border: 1px solid #a0a0a0;
      border-radius: 1px;
      display: flex;
      flex-direction: column;
    }

    /* Core Message Section */
    .content-area {
      background-color: #ffffff;
      padding: 25px 20px;
      display: flex;
      gap: 15px;
      align-items: flex-start;
    }

    /* Layout Slot for the Warning/Question Graphic Indicator */
    .icon-container {
      flex-shrink: 0;
      width: 32px;
      height: 32px;
    }

    .message-container {
      flex-grow: 1;
      font-size: 12px;
      color: #002c59; /* Classic deep blue Windows Dialog text */
      line-height: 1.5;
    }

    /* Action Command Strip (Footer Area) */
    .command-strip {
      background-color: #f0f0f0;
      border-top: 1px solid #dfdfdf;
      padding: 12px 14px;
      display: flex;
      justify-content: flex-end;
      gap: 8px;
    }
  `;

  // Emits cancel context event and toggles internal viewport visibility 
  public closeDialog(): void {
    this.open = false;
    this.dispatchEvent(new CustomEvent('aero-cancel', { bubbles: true, composed: true }));
  }

  // Emits confirmation event block execution back to layout page
  public confirmAction(): void {
    this.open = false;
    this.dispatchEvent(new CustomEvent('aero-confirm', { bubbles: true, composed: true }));
  }

  protected override render(): TemplateResult {
    return html`
      <div class="overlay" @click="${this._handleOverlayClick}">
        <div class="window-frame" role="dialog" aria-modal="true" aria-labelledby="title">
          
          <div class="title-bar">
            <span id="title" class="title-text">${this.windowTitle}</span>
            <button class="close-btn" aria-label="Close" @click="${this.closeDialog}"></button>
          </div>

          <div class="window-body">
            <div class="content-area">
              <div class="icon-container">
                <slot name="icon">
                  <svg width="32" height="32" viewBox="0 0 32 32">
                    <circle cx="16" cy="16" r="14" fill="#e53e3e" stroke="#fff" stroke-width="2"/>
                    <path d="M16 8v10M16 22h.01" stroke="#fff" stroke-width="3" stroke-linecap="round"/>
                  </svg>
                </slot>
              </div>
              <div class="message-container">
                <slot></slot>
              </div>
            </div>

            <div class="command-strip">
              <slot name="actions">
                <button 
                  style="padding: 5px 20px; font-size: 12px; font-family: inherit;" 
                  @click="${this.confirmAction}">
                  OK
                </button>
                <button 
                  style="padding: 5px 20px; font-size: 12px; font-family: inherit;" 
                  @click="${this.closeDialog}">
                  Cancel
                </button>
              </slot>
            </div>
          </div>

        </div>
      </div>
    `;
  }

  private _handleOverlayClick(e: MouseEvent): void {
    // Prevent accidental dismiss clicks inside the actual layout box frame
    if ((e.target as HTMLElement).classList.contains('overlay')) {
      this.closeDialog();
    }
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-dialog': RoqueDialog;
  }
}