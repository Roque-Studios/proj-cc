import { LitElement, html, css } from "lit";
import { customElement, property } from "lit/decorators.js";
import { unsafeHTML } from "lit/directives/unsafe-html.js";

// Configuration interface for Table Columns
export interface AeroTableColumn {
  key: string;
  label: string;
  width?: string;
  align?: "left" | "center" | "right";
}

@customElement("roque-table")
export class AeroTable extends LitElement {
  // Columns definition array
  @property({ type: Array }) columns: AeroTableColumn[] = [];

  // Rows data array
  @property({ type: Array }) data: Record<string, any>[] = [];

  static styles = css`
    :host {
      display: block;
      width: 100%;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      font-size: 13px;
    }

    .table-container {
      width: 100%;
      overflow-x: auto;
      background-color: #ffffff;
      border: 1px solid #b1b1b1;
      box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
    }

    .aero-table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
    }

    /* --- Aero Column Header Styling --- */
    th {
      padding: 5px 8px;
      font-weight: normal;
      color: #212121;
      background: linear-gradient(
        to bottom,
        #fafafa 0%,
        #f2f2f2 40%,
        #e7e7e7 50%,
        #e1e1e1 50.1%,
        #e5e5e5 100%
      );
      border-bottom: 1px solid #b1b1b1;
      border-right: 1px solid #dcdcdc;
      position: relative;
      cursor: pointer;
      user-select: none;
      text-shadow: 0 1px 0 rgba(255, 255, 255, 0.6);
    }

    th:last-child {
      border-right: none;
    }

    /* Header Hover Glass Effect */
    th:hover {
      background: linear-gradient(
        to bottom,
        #f5fbff 0%,
        #eaf5fd 40%,
        #d8eefc 50%,
        #cbe6f9 50.1%,
        #dbeffc 100%
      );
      border-right-color: #b0d7f2;
    }

    /* --- Table Body Rows Styling --- */
    td {
      padding: 5px 8px;
      color: #000000;
      border-bottom: 1px solid #f0f0f0;
      white-space: nowrap;
    }

    /* Soft Zebra Striping Option matching Explorer file rows */
    tr:nth-child(even) {
      background-color: #fafafa;
    }

    /* Aero Windows Selection Hover Highlight style */
    tr:hover td {
      background-color: #edf5fc;
      color: #000000;
    }

    /* Align Utility Rules */
    .align-left {
      text-align: left;
    }
    .align-center {
      text-align: center;
    }
    .align-right {
      text-align: right;
    }
  `;

  render() {
    return html`
      <div class="table-container">
        <table class="aero-table">
          <thead>
            <tr>
              ${this.columns.map(
                (col) => html`
                  <th
                    style="${col.width ? `width: ${col.width};` : ""}"
                    class="align-${col.align || "left"}"
                  >
                    ${col.label}
                  </th>
                `,
              )}
            </tr>
          </thead>
          <tbody>
            ${this.data.length === 0
              ? html`
                  <tr>
                    <td
                      colspan="${this.columns.length}"
                      class="align-center"
                      style="color: #838383; padding: 20px;"
                    >
                      No data available inside current rank parameters.
                    </td>
                  </tr>
                `
              : this.data.map(
                  (row) => html`
                    <tr>
                      ${this.columns.map((col) => {
                        const cellValue = row[col.key];

                        // Check if the cell value is a string containing HTML markup tags
                        const renderedContent =
                          typeof cellValue === "string" &&
                          cellValue.includes("<")
                            ? unsafeHTML(cellValue)
                            : cellValue;

                        return html`
                          <td class="align-${col.align || "left"}">
                            <slot name="cell-${col.key}-${row.id || ""}">
                              ${renderedContent}
                            </slot>
                          </td>
                        `;
                      })}
                    </tr>
                  `,
                )}
          </tbody>
        </table>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "roque-table": AeroTable;
  }
}
