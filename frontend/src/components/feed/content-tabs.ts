import { LitElement, html, css } from 'lit'
import { customElement, property, state } from 'lit/decorators.js'

import '../layouts/tabs.ts'
import './subscriber-feed.ts'
import './media-gallery.ts'
import type { TabChangedEventDetail } from '../layouts/tabs'

/**
 * Two-tab content browser for a creator: **Posts** and **MEDIA**.
 *
 * Wraps ``roque-tabs`` (the shared tab strip component) around the two ways
 * to browse a creator's content:
 *
 * - **Posts** — the existing paginated feed (`roque-subscriber-feed`),
 * - **MEDIA** — the flat gallery of the creator's full content
 *   (`roque-media-gallery`), lazily loaded only once the tab is opened.
 *
 * Both panels inherit the same access gating as the feed (locked paid
 * broadcasts stay blurred + priced; non-followers see everything blurred).
 * ``userId`` is forwarded so post comments keep the delete-own-comment
 * affordance on both pages that embed this component.
 */
@customElement('roque-content-tabs')
export class ContentTabs extends LitElement {
  /** The creator whose content is browsed. */
  @property({ type: Number, attribute: 'creator-id' }) creatorId = 0
  /** The signed-in viewer's user id (comment deletion affordance). */
  @property({ type: Number, attribute: 'user-id' }) userId = 0

  @state() private activeTab = 0

  static styles = css`
    :host {
      display: block;
      font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    /* The tab panels are the feed/gallery; let the shared tab strip's own
       chrome (gradient header, white panel) carry the look. */
    .panel {
      width: 100%;
    }
  `

  private _onTabChanged(e: CustomEvent<TabChangedEventDetail>) {
    this.activeTab = e.detail.activeTab
  }

  render() {
    return html`
      <!-- roque-tabs manages its own active-tab state and reports every
           switch through the tab-changed event below. -->
      <roque-tabs @tab-changed="${this._onTabChanged}">
        <div class="panel" slot="panel" label="Posts">
          <roque-subscriber-feed
            creator-id="${this.creatorId}"
            user-id="${this.userId}"
          ></roque-subscriber-feed>
        </div>
        <div class="panel" slot="panel" label="MEDIA">
          <roque-media-gallery
            creator-id="${this.creatorId}"
            ?active="${this.activeTab === 1}"
          ></roque-media-gallery>
        </div>
      </roque-tabs>
    `
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'roque-content-tabs': ContentTabs
  }
}
