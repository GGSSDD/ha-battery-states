// Battery States card, shipped and loaded by the battery_states integration.
// Draws the battery list exactly like the former mod-card / button-card /
// markdown / auto-entities stack, from the data of the integration's
// "Low batteries" sensor. Sorting, grouping and filtering happen here; each
// Home Assistant user's choice is kept in their profile (frontend user data).
console.info("battery-states-card: loading");

window.customCards = window.customCards || [];
window.customCards.push({
  type: "battery-states-card",
  name: "Battery States",
  description: "Battery list of the Battery States integration.",
});

(async () => {
  // Reuse Home Assistant's own LitElement, html and css (same method as tooltip-card).
  await customElements.whenDefined("ha-panel-lovelace");
  const LitElement = Object.getPrototypeOf(customElements.get("ha-panel-lovelace"));
  const html = LitElement.prototype.html;
  const css = LitElement.prototype.css;
  if (!html || !css) {
    console.error("battery-states-card: Home Assistant did not provide Lit html/css");
    return;
  }

  const DEFAULT_ENTITY = "sensor.battery_states_low_batteries";
  const PREFS_KEY = "battery_states_card"; // per-user sort / group / filter
  const DEFAULT_PREFS = { sort: "ascending", group: "none", filter: false };
  const GROUPS = ["area", "type"];
  const cleanPrefs = (v) => ({
    sort: v?.sort === "descending" ? "descending" : "ascending",
    group: GROUPS.includes(v?.group) ? v.group : "none",
    filter: v?.filter === true,
  });
  const DEFAULT_LOW = 20; // the integration sends its low battery limit (low_threshold)
  const MENU_DOWN = "M7,10L12,15L17,10H7Z"; // mdi:menu-down, as in HA's select menu

  // Same colour formula as the old card's icon style.
  const iconColor = (state) => {
    const val = Math.min(100, Math.max(0, parseFloat(state) || 0));
    let r, g;
    if (val >= 60) {
      const pct = (100 - val) / (100 - 60);
      r = Math.round(255 * pct);
      g = 255;
    } else if (val > 10) {
      const pct = (60 - val) / (60 - 10);
      r = 255;
      g = Math.round(255 * (1 - pct));
    } else {
      r = 255;
      g = 0;
    }
    return `rgb(${r},${g},0)`;
  };

  // Python's str.lower() ordering, as the old pyscript sorted group names.
  const byLower = (a, b) => {
    const x = a.toLowerCase();
    const y = b.toLowerCase();
    return x < y ? -1 : x > y ? 1 : 0;
  };

  // Same list the old pyscript built: filter, then group (headers) and sort.
  // Array.prototype.sort is stable, like Python's sorted() (also with reverse).
  const buildList = (devices, groupBy, sortOrder, filterOn, low) => {
    const desc = sortOrder === "descending";
    const cmp = (a, b) => (desc ? b.value - a.value : a.value - b.value);
    let items = devices.map((d) => ({ ...d, area_prefix: groupBy !== "area" }));
    // Same test as the TBR mark and the integration's count: the reading itself.
    if (filterOn) items = items.filter((d) => (parseFloat(d.state) || 0) <= low);
    if (groupBy === "none") return [...items].sort(cmp);
    const key = groupBy === "area" ? "area" : "battery_type";
    const groups = new Map();
    for (const d of items) {
      // No area: kept apart as "" (a real area may be called "No area").
      const g = key === "area" ? d.area || "" : d.battery_type || "Unknown";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(d);
    }
    const out = [];
    // Batteries without an area ("No area") or a known type ("Unknown") come last.
    const last = key === "area" ? "" : "Unknown";
    const names = [...groups.keys()].filter((g) => g !== last).sort(byLower);
    if (groups.has(last)) names.push(last);
    for (const g of names) {
      out.push({ header: true, name: g || "No area" });
      out.push(...groups.get(g).sort(cmp));
    }
    return out;
  };

  class BatteryStatesCard extends LitElement {
    static get properties() {
      return {
        hass: { attribute: false },
        _config: { state: true },
        _menuOpen: { state: true },
        _prefs: { state: true },
      };
    }

    constructor() {
      super();
      this._menuOpen = false;
      this._prefs = undefined; // until the user's saved choice has arrived
      // A press outside the select closes the menu (like HA's dropdown).
      this._onDocPointer = (ev) => {
        if (!this._menuOpen) return;
        if (ev.composedPath().includes(this.renderRoot.querySelector(".select"))) return;
        this._menuOpen = false;
      };
      this._onReposition = () => {
        if (this._menuOpen) this._placeMenu();
      };
    }

    setConfig(config) {
      this._config = { ...config };
    }

    static getStubConfig() {
      return {};
    }

    getCardSize() {
      const st = this.hass && this._config ? this.hass.states[this._entityId()] : undefined;
      return 4 + (st?.attributes?.devices?.length ?? 0);
    }

    // Sections dashboards: full width by default, at least half.
    getGridOptions() {
      return { columns: 12, rows: "auto", min_columns: 6 };
    }

    // The integration's sensor: the one set in the card's config if it exists,
    // else found by itself (its entity ID may have been changed or got a _2).
    _entityId() {
      const want = this._config.entity;
      if (want && this.hass.states[want]) return want;
      const entities = this.hass.entities;
      if (this._found?.entities !== entities) {
        const hit = Object.values(entities || {}).find(
          (e) => e.platform === "battery_states" && e.translation_key === "low_batteries"
        );
        this._found = { entities, id: hit?.entity_id };
      }
      return this._found.id ?? want ?? DEFAULT_ENTITY;
    }

    // The user's choice, live: also changed on another device or in another card.
    _subscribePrefs() {
      if (this._prefsSub || !this.hass) return;
      const conn = this.hass.connection;
      if (!conn?.subscribeMessage) {
        this._prefs = this._prefs ?? { ...DEFAULT_PREFS };
        return;
      }
      this._prefsSub = conn
        .subscribeMessage((msg) => (this._prefs = cleanPrefs(msg?.value)), {
          type: "frontend/subscribe_user_data",
          key: PREFS_KEY,
        })
        .catch((err) => {
          console.error("battery-states-card: could not load the saved choices", err);
          this._prefs = this._prefs ?? { ...DEFAULT_PREFS };
          return () => {};
        });
    }

    _setPrefs(change) {
      this._prefs = { ...(this._prefs ?? DEFAULT_PREFS), ...change };
      this.hass
        .callWS?.({ type: "frontend/set_user_data", key: PREFS_KEY, value: this._prefs })
        ?.catch((err) => console.error("battery-states-card: could not save the choice", err));
    }

    willUpdate() {
      if (this.isConnected) this._subscribePrefs();
    }

    connectedCallback() {
      super.connectedCallback();
      this._subscribePrefs();
      document.addEventListener("pointerdown", this._onDocPointer, true);
      window.addEventListener("scroll", this._onReposition, true);
      window.addEventListener("resize", this._onReposition);
    }

    disconnectedCallback() {
      super.disconnectedCallback();
      this._prefsSub?.then((unsub) => unsub());
      this._prefsSub = undefined;
      document.removeEventListener("pointerdown", this._onDocPointer, true);
      window.removeEventListener("scroll", this._onReposition, true);
      window.removeEventListener("resize", this._onReposition);
      this._menuOpen = false;
      const menu = this.renderRoot?.querySelector(".menu");
      if (menu?.matches(":popover-open")) menu.hidePopover();
    }

    // Re-render only when something this card shows has changed.
    _watched(hass) {
      const id = this._entityId();
      const st = hass?.states[id];
      const ids = [id];
      for (const d of st?.attributes?.devices || []) ids.push(d.entity_id);
      return ids;
    }

    shouldUpdate(changed) {
      if (changed.has("_config") || changed.has("_menuOpen") || changed.has("_prefs")) return true;
      if (!changed.has("hass")) return true;
      const old = changed.get("hass");
      if (!old || !this.hass) return true;
      // HA replaces its formatters once translations, the entity registry and
      // the numeric sensor classes have loaded: redraw then, or values shown on
      // an early first render keep lacking their unit ("47" instead of "47%").
      if (
        old.locale !== this.hass.locale ||
        old.themes !== this.hass.themes ||
        old.formatEntityState !== this.hass.formatEntityState ||
        old.localize !== this.hass.localize ||
        old.entities !== this.hass.entities ||
        old.connection !== this.hass.connection
      )
        return true;
      return this._watched(this.hass).some((id) => old.states[id] !== this.hass.states[id]);
    }

    // The menu is a popover in the browser's top layer, placed at page
    // coordinates like HA's select menu: centred below the anchor, flipped
    // above it when there is more room there, height limited to the space
    // left (10 px padding). It plays the same 50 ms show/hide animation.
    updated(changed) {
      const menu = this.renderRoot.querySelector(".menu");
      if (!menu || typeof menu.showPopover !== "function") return;
      if (!changed.has("_menuOpen")) {
        if (this._menuOpen) this._placeMenu();
        return;
      }
      if (this._menuOpen) {
        if (!menu.matches(":popover-open")) menu.showPopover();
        this._placeMenu();
        menu.classList.remove("hide");
        menu.classList.remove("show");
        void menu.offsetWidth;
        menu.classList.add("show");
      } else if (menu.matches(":popover-open")) {
        menu.classList.remove("show");
        void menu.offsetWidth;
        menu.classList.add("hide");
        const done = () => {
          menu.classList.remove("hide");
          if (!this._menuOpen && menu.matches(":popover-open")) menu.hidePopover();
        };
        menu.addEventListener("animationend", done, { once: true });
        setTimeout(done, 150);
      }
    }

    _placeMenu() {
      const menu = this.renderRoot.querySelector(".menu");
      const anchor = this.renderRoot.querySelector(".select-card");
      if (!menu || !anchor) return;
      const r = anchor.getBoundingClientRect();
      const pad = 10;
      menu.style.maxHeight = "";
      const h = menu.offsetHeight;
      const w = menu.offsetWidth;
      const below = window.innerHeight - r.bottom - pad;
      const above = r.top - pad;
      let top = r.bottom;
      let origin = "top";
      if (h > below && above > below) {
        top = Math.max(pad, r.top - h);
        origin = "bottom";
        if (h > above) menu.style.maxHeight = `${above}px`;
      } else if (h > below) {
        menu.style.maxHeight = `${Math.max(0, below)}px`;
      }
      let left = r.left + (r.width - w) / 2;
      left = Math.min(Math.max(left, pad), window.innerWidth - pad - w);
      menu.style.transformOrigin = origin;
      menu.style.top = `${top + window.scrollY}px`;
      menu.style.left = `${left + window.scrollX}px`;
    }

    _menuItems() {
      return [...this.renderRoot.querySelectorAll(".menu-item")];
    }

    _onAnchorKey(ev) {
      if (!this._menuOpen) return;
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp" || ev.key === "Home" || ev.key === "End") {
        ev.preventDefault();
        const items = this._menuItems();
        if (!items.length) return;
        items[ev.key === "ArrowUp" || ev.key === "End" ? items.length - 1 : 0].focus();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        this._menuOpen = false;
      }
    }

    _onMenuKey(ev, option) {
      const items = this._menuItems();
      const i = items.indexOf(ev.currentTarget);
      if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
        ev.preventDefault();
        const n = items.length;
        items[(i + (ev.key === "ArrowDown" ? 1 : n - 1)) % n].focus();
      } else if (ev.key === "Home" || ev.key === "End") {
        ev.preventDefault();
        items[ev.key === "Home" ? 0 : items.length - 1].focus();
      } else if (ev.key === "Enter" || ev.key === " ") {
        ev.preventDefault();
        this._choose(option);
      } else if (ev.key === "Escape" || ev.key === "Tab") {
        if (ev.key === "Escape") ev.preventDefault();
        this._menuOpen = false;
        if (ev.key === "Escape") this.renderRoot.querySelector(".select-anchor")?.focus();
      }
    }

    _choose(option) {
      this._menuOpen = false;
      this.renderRoot.querySelector(".select-anchor")?.focus({ preventScroll: true });
      this._setPrefs({ group: option });
    }

    _moreInfo(entityId) {
      this.dispatchEvent(
        new CustomEvent("hass-more-info", { bubbles: true, composed: true, detail: { entityId } })
      );
    }

    render() {
      if (!this._config || !this.hass || !this._prefs) return html``;
      const entityId = this._entityId();
      const st = this.hass.states[entityId];
      if (!st) {
        return html`<ha-card class="main"><div class="missing">${this._config.entity
          ? `Entity not available: ${this._config.entity}`
          : "Battery States is not set up"}</div></ha-card>`;
      }
      // The sensor's state is the low count; anything else means the integration
      // isn't running (failed, disabled, reloading): don't show an empty "all clear".
      if (Number.isNaN(parseInt(st.state, 10))) {
        return html`<ha-card class="main"><div class="missing">Battery States is not available</div></ha-card>`;
      }
      const a = st.attributes;
      const devices = a.devices || [];
      const counts = a.battery_low_count || [];
      const { sort: sortOrder, group: groupBy, filter: filterOn } = this._prefs;
      const low = Number.isFinite(a.low_threshold) ? a.low_threshold : DEFAULT_LOW;
      const list = buildList(devices, groupBy, sortOrder, filterOn, low);
      return html`
        <ha-card class="main">
          <div class="stack">
            <div class="title"><div class="title-name">Battery States</div></div>
            ${this._renderSummary(counts)}
            ${this._renderControls(sortOrder, groupBy, filterOn)}
            <div class="list">${list.map((d, i) => this._renderItem(d, list[i + 1], low))}</div>
          </div>
        </ha-card>
      `;
    }

    _renderSummary(counts) {
      let total = 0;
      const rows = [];
      // Same order as the group-by-type headers: A to Z ignoring case, "Unknown" last.
      const typeOf = (item) => Object.keys(item)[0];
      const ordered = [...counts].sort(
        (a, b) => (typeOf(a) === "Unknown") - (typeOf(b) === "Unknown") || byLower(typeOf(a), typeOf(b))
      );
      for (const item of ordered) {
        const name = Object.keys(item)[0];
        const val = parseInt(item[name], 10) || 0;
        total += val;
        if (val > 0) {
          if (rows.length) rows.push(html`<tr><td colspan="2"><hr /></td></tr>`);
          rows.push(html`<tr><td>${name}:</td><td>${val}</td></tr>`);
        }
      }
      return html`
        <ha-card class="summary">
          <div class="summary-body">
            <table>
              <tbody>
                <tr><th>Battery Type</th><th>Pcs</th></tr>
                ${rows}
                <tr><td colspan="2"><hr /></td></tr>
                <tr class="total"><td>TOTAL:</td><td>${total}</td></tr>
              </tbody>
            </table>
          </div>
        </ha-card>
      `;
    }

    _renderControls(sortOrder, groupBy, filterOn) {
      const options = GROUPS.filter((o) => o !== groupBy);
      const chip = groupBy === "area" ? "area" : groupBy === "type" ? "battery_type" : null;
      return html`
        <div class="controls">
          <div class="sort-cell">
            <ha-card
              class="btn sort"
              @click=${() => this._setPrefs({ sort: sortOrder === "ascending" ? "descending" : "ascending" })}
            >
              <ha-state-icon
                class="sort-icon"
                .hass=${this.hass}
                .icon=${sortOrder === "ascending" ? "mdi:arrow-up" : "mdi:arrow-down"}
              ></ha-state-icon>
              <ha-ripple></ha-ripple>
            </ha-card>
          </div>
          <div class="gap1"></div>
          <div class="select">
            <ha-card class="select-card">
              <button
                class="select-anchor"
                aria-haspopup="menu"
                aria-expanded=${this._menuOpen ? "true" : "false"}
                @click=${() => (this._menuOpen = !this._menuOpen)}
                @keydown=${(ev) => this._onAnchorKey(ev)}
              >
                <div class="select-content"><p class="value">Group by</p></div>
                <div class="select-icon ${this._menuOpen ? "open" : ""}">
                  <ha-svg-icon .path=${MENU_DOWN}></ha-svg-icon>
                </div>
              </button>
            </ha-card>
            <div class="menu" popover="manual" role="menu">
              ${options.map(
                (o) => html`<div
                  class="menu-item"
                  role="menuitem"
                  tabindex="-1"
                  @click=${() => this._choose(o)}
                  @keydown=${(ev) => this._onMenuKey(ev, o)}
                >
                  <span class="menu-label">${o}</span>
                </div>`
              )}
            </div>
          </div>
          <div class="gap2"></div>
          <div class="chip-cell">
            ${chip
              ? html`<ha-card class="chip ${chip === "area" ? "chip-area" : "chip-type"}">
                  <div class="chip-name">${chip}</div>
                  <ha-card
                    class="btn chip-close"
                    @click=${() => this._setPrefs({ group: "none" })}
                  >
                    <ha-state-icon class="close-icon" .hass=${this.hass} .icon=${"mdi:close"}></ha-state-icon>
                    <ha-ripple></ha-ripple>
                  </ha-card>
                </ha-card>`
              : ""}
          </div>
          <div class="chip-cell2"></div>
          <div class="gap3"></div>
          <div class="filter-cell">
            <ha-card class="btn filter" @click=${() => this._setPrefs({ filter: !filterOn })}>
              <ha-state-icon
                class="filter-icon ${filterOn ? "on" : ""}"
                .hass=${this.hass}
                .icon=${filterOn ? "mdi:filter-variant-remove" : "mdi:filter-variant"}
              ></ha-state-icon>
              <ha-ripple></ha-ripple>
            </ha-card>
          </div>
        </div>
      `;
    }

    _renderItem(d, next, low) {
      if (d.header) {
        return html`<ha-card class="header"><div class="header-text">${d.name}:</div></ha-card>`;
      }
      const stateObj = this.hass.states[d.entity_id];
      const val = parseFloat(d.state) || 0;
      const base = d.area_prefix && d.area ? `${d.area}: ${d.name}` : d.name;
      const shown = d.reading ? d.state : "0";
      const stateText = stateObj
        ? this.hass.formatEntityState
          ? this.hass.formatEntityState(stateObj, shown)
          : `${shown} %`
        : "";
      const separator = next && !next.header;
      return html`
        <ha-card class="row ${separator ? "sep" : ""}" @click=${() => this._moreInfo(d.entity_id)}>
          <ha-state-icon
            class="row-icon"
            style="color: ${iconColor(d.state)}"
            .hass=${this.hass}
            .stateObj=${stateObj}
            .stateValue=${d.reading ? d.state : "unavailable"}
          ></ha-state-icon>
          <div class="text">
            <div class="name ellipsis">${val <= low
              ? html`${base + "\u00a0\u00a0\u00a0"}<span class="tbr">*TBR!</span>`
              : base}</div>
            <div class="label ellipsis">Battery Type: ${d.battery_type}</div>
            <div class="state ellipsis">${stateText}</div>
          </div>
          <ha-ripple></ha-ripple>
        </ha-card>
      `;
    }

    static get styles() {
      return css`
        :host {
          display: block;
          /* Text and line colours follow the theme's text colour, so the card
             reads on light and dark themes alike (on a theme whose text is
             white these are exactly the old fixed whites). */
          --bs-text: var(--primary-text-color);
          --bs-text-faded: color-mix(in srgb, var(--primary-text-color) 50%, transparent);
          --bs-line: color-mix(in srgb, var(--primary-text-color) 15%, transparent);
        }
        /* Background comes from the theme (ha-card's own default); change it,
           or anything else, with uix in the card config. Stable hooks:
           ha-card.main, .title, ha-card.summary, .controls, .select-anchor,
           .menu, .menu-item, ha-card.chip, .list, ha-card.header, ha-card.row,
           .row-icon, .text, .name, .tbr, .label, .state */
        /* Corners too come from the theme (ha-card's own default). */
        ha-card.main {
          padding: 0;
        }
        /* The pieces inside the card are ha-cards too (for the theme's ripple
           and corners), but they must not take the theme's card border, shadow
           or blur: themes that draw those would box every row and button. */
        ha-card:not(.main) {
          border: none;
          box-shadow: none;
          backdrop-filter: none;
          -webkit-backdrop-filter: none;
        }
        .missing {
          padding: 16px;
        }
        .stack {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .ellipsis {
          text-overflow: ellipsis;
          white-space: nowrap;
          overflow: hidden;
        }

        /* Title (was a button-card) */
        .title {
          padding: 15px 0 0 15px;
          font-size: 24px;
          font-weight: 400;
          line-height: normal;
          letter-spacing: normal;
        }
        .title-name {
          text-overflow: ellipsis;
          white-space: nowrap;
          overflow: hidden;
        }

        /* Summary box (was a markdown card) */
        ha-card.summary {
          margin: 15px;
          border-radius: 5px;
          background: none;
          border: 2px solid var(--bs-text-faded);
        }
        .summary-body {
          padding: 10px 15px;
          line-height: 1.6;
        }
        table {
          width: 100%;
          border-spacing: 0;
          border-collapse: separate;
          border: none;
        }
        th,
        td {
          border: none;
        }
        th {
          padding: 0.25em 0;
          color: var(--bs-text);
          font-weight: 500;
          font-size: 14px;
          line-height: 1.6;
        }
        td {
          padding: 0.25em 0;
          color: var(--bs-text-faded);
          font-size: 11px;
        }
        th:first-child,
        td:first-child {
          text-align: left;
        }
        th:last-child,
        td:last-child {
          text-align: center;
          width: 1px;
          white-space: nowrap;
        }
        hr {
          padding: 0;
          margin: 0 -1px;
          border: none;
          border-top: 0.5px solid var(--bs-line);
        }
        tr.total td {
          color: var(--error-color);
          font-weight: bold;
        }

        /* Controls row (was layout-card grid) */
        .controls {
          display: grid;
          height: 24px;
          padding: 0 15px 0 0;
          /* On a narrow card the Group by select, the chip and the gaps get
             smaller (the texts end with "…"); the buttons always show. */
          grid-template-columns:
            auto minmax(4px, 12px) minmax(0, max-content) minmax(4px, 20px)
            minmax(0, max-content) auto minmax(4px, 1fr) 24px;
          grid-template-rows: 1fr;
          align-items: center;
        }
        ha-card.btn {
          position: relative;
          box-sizing: border-box;
          overflow: hidden;
          cursor: pointer;
          display: flex;
          justify-content: center;
          align-items: center;
          height: 24px;
          width: 24px;
          padding: 0;
          background: none;
          font-size: 1.2rem;
          line-height: normal;
          user-select: none;
          -webkit-user-select: none;
          -webkit-tap-highlight-color: transparent;
          /* Same ripple variables as button-card, so the theme's
             button-card-ripple-* settings apply here too. */
          --ha-ripple-color: var(--button-card-ripple-color);
          --ha-ripple-hover-color: var(--ha-ripple-color, var(--button-card-ripple-hover-color));
          --ha-ripple-pressed-color: var(--ha-ripple-color, var(--button-card-ripple-pressed-color));
          --ha-ripple-hover-opacity: var(--button-card-ripple-hover-opacity, 0.04);
          --ha-ripple-pressed-opacity: var(--button-card-ripple-pressed-opacity, 0.12);
        }
        /* Icons are drawn like button-card draws them: an absolutely placed,
           inline ha-state-icon centred by auto margins. */
        ha-card ha-state-icon {
          display: block;
          position: absolute;
          margin: auto;
          inset: 0;
          --ha-icon-display: inline;
          --mdc-icon-size: 100%;
          --iron-icon-width: 100%;
          --iron-icon-height: 100%;
        }
        ha-card.sort {
          margin-left: 13px; /* corners: theme, as with button-card */
        }
        .sort-icon {
          width: 15px;
          height: 15px;
        }
        ha-card.filter {
          border-radius: 0;
        }
        .filter-icon {
          width: 20px;
          height: 20px;
          left: auto;
          right: 0;
          color: var(--bs-text-faded);
        }
        .filter-icon.on {
          color: var(--accent-color);
        }

        /* Group by select (was a styled mushroom select) */
        .select {
          position: relative;
          min-width: 0;
        }
        ha-card.select-card {
          background: none;
          border-radius: 5px;
          height: 24px;
          width: 100px;
          max-width: 100%;
        }
        /* Same element and base styles as HA's select menu anchor, with the
           overrides the old card applied (no padding, gap, radius or tint). */
        .select-anchor {
          display: flex;
          flex-direction: row;
          align-items: center;
          position: relative;
          overflow: hidden;
          box-sizing: border-box;
          width: 100%;
          height: 24px;
          padding: 0;
          gap: 0;
          margin: 0;
          border: none;
          border-radius: 0;
          outline: none;
          background: 0 0;
          color: var(--primary-text-color);
          text-align: left;
          font-family: var(--ha-font-family-body, inherit);
          font-style: normal;
          font-weight: var(--ha-font-weight-normal);
          letter-spacing: 0.25px;
          cursor: pointer;
          user-select: none;
          -webkit-user-select: none;
          -webkit-tap-highlight-color: transparent;
          transition: box-shadow 0.18s ease-in-out, color 0.18s ease-in-out;
        }
        .select-anchor:focus-visible {
          box-shadow: 0 0 0 2px var(--secondary-text-color);
        }
        .select-content {
          flex: 1;
          display: flex;
          flex-direction: column;
          justify-content: center;
          align-items: flex-start;
          overflow: hidden;
        }
        .value {
          margin: auto;
          width: 100%;
          min-width: 0;
          text-overflow: ellipsis;
          overflow: hidden;
          color: var(--bs-text);
          font-size: 18px;
          font-weight: 400;
          letter-spacing: 0.25px;
          line-height: normal;
          white-space: nowrap;
          overflow: hidden;
        }
        .select-icon {
          display: block;
          width: 20px;
          height: 20px;
          font-size: 13.3333px;
          line-height: normal;
          --mdc-icon-size: 20px;
        }
        .select-icon.open {
          transform: rotate(180deg);
        }
        /* Shown in the browser's top layer (popover), placed at page
           coordinates under the anchor, like HA's own select menu. */
        .menu {
          position: absolute;
          inset: auto;
          margin: 0;
          border: 0;
          overflow: auto;
          color: inherit;
          width: 100px;
          height: auto;
          box-sizing: border-box;
          flex-direction: column;
          border-radius: 5px;
          /* The theme's text colour turned around: dark behind white text
             (rgb 25,25,25 with white), light behind dark text. The first line
             is for browsers without relative colours. */
          background: rgba(25, 25, 25, 0.85);
          background: rgb(from var(--primary-text-color) calc(280 - r) calc(280 - g) calc(280 - b) / 0.85);
          backdrop-filter: blur(5px);
          -webkit-backdrop-filter: blur(5px);
          box-shadow: rgba(0, 0, 0, 0.3) 0px 2px 6px;
          padding: 5px;
        }
        .menu:popover-open {
          display: flex;
        }
        .menu.show {
          animation: menu-show 50ms ease;
        }
        .menu.hide {
          animation: menu-show 50ms ease reverse;
        }
        @keyframes menu-show {
          0% {
            opacity: 0;
            scale: 0.9;
          }
          to {
            opacity: 1;
            scale: 1;
          }
        }
        .menu-item {
          display: flex;
          align-items: center;
          min-height: 30px;
          padding: 7px 14px;
          box-sizing: border-box;
          font-size: 14px;
          line-height: 1.2;
          color: var(--wa-color-text-normal, var(--primary-text-color));
          position: relative;
          isolation: isolate;
          outline: none;
          cursor: pointer;
          user-select: none;
          -webkit-user-select: none;
          -webkit-tap-highlight-color: transparent;
          transition: var(--wa-transition-fast, 75ms) background-color var(--wa-transition-easing, ease);
        }
        .menu-item:focus-visible {
          z-index: 1;
          outline: var(--wa-focus-ring);
          background-color: var(--wa-color-neutral-fill-normal);
        }
        .menu-item:hover {
          background: color-mix(in srgb, var(--accent-color) 10%, transparent);
        }

        /* Active group chip (was state-switch + button-cards) */
        /* Home Assistant's own chip colour: the theme's text colour at 15 %,
           so the chip shows on any card background. */
        ha-card.chip {
          position: relative;
          background: var(--chip-background-color, rgba(var(--rgb-primary-text-color), 0.15));
          height: 24px;
          box-sizing: border-box;
          overflow: hidden; /* corners: theme, as with button-card */
          user-select: none;
          -webkit-user-select: none;
        }
        .chip-cell {
          min-width: 0;
        }
        ha-card.chip-area {
          width: 60px;
          max-width: 100%;
        }
        ha-card.chip-type {
          width: 100px;
          max-width: 100%;
        }
        .chip-name {
          position: absolute;
          font-size: 11px;
          line-height: normal;
          left: 10px;
          right: 24px;
          top: 50%;
          transform: translateY(-50%);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
          padding: 2px 0; /* room for "_" and descenders, which overflow would cut */
        }
        ha-card.chip-close {
          position: absolute;
          right: 0;
          top: 0;
          border-radius: 50%;
        }
        .close-icon {
          width: 14px;
          height: 14px;
        }

        /* List (was auto-entities + layout-card) */
        .list {
          padding: 10px 0;
        }
        ha-card.header {
          background: none;
          border-radius: 0;
          box-shadow: none;
        }
        .header-text {
          padding: 4px 15px;
          font-size: 18px;
          font-weight: 500;
          line-height: 1.6;
          text-decoration: none;
        }
        ha-card.row {
          position: relative;
          box-sizing: border-box;
          overflow: hidden;
          cursor: pointer;
          height: 50px;
          padding: 4px 0;
          background: none;
          border-radius: 0;
          font-size: 1.2rem;
          line-height: normal;
          user-select: none;
          -webkit-user-select: none;
          -webkit-tap-highlight-color: transparent;
          /* Same ripple variables as button-card, so the theme's
             button-card-ripple-* settings apply here too. */
          --ha-ripple-color: var(--button-card-ripple-color);
          --ha-ripple-hover-color: var(--ha-ripple-color, var(--button-card-ripple-hover-color));
          --ha-ripple-pressed-color: var(--ha-ripple-color, var(--button-card-ripple-pressed-color));
          --ha-ripple-hover-opacity: var(--button-card-ripple-hover-opacity, 0.04);
          --ha-ripple-pressed-opacity: var(--button-card-ripple-pressed-opacity, 0.12);
        }
        ha-card.row.sep::after {
          content: "";
          position: absolute;
          left: 15px;
          right: 15px;
          bottom: 0;
          height: 0;
          border-top: 0.5px solid var(--bs-line);
          pointer-events: none;
        }
        .row-icon {
          width: 50px;
          height: 23px;
          right: auto;
          margin: auto 0;
        }
        /* Name and type line on the left, the value on the right: a long name
           or type ends with "…" before the value instead of running under it. */
        .text {
          position: absolute;
          top: 0;
          bottom: 0;
          left: 50px;
          right: 15px;
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          grid-template-rows: 100%;
          column-gap: 8px;
        }
        .name {
          grid-column: 1 / 2; /* both lines: "1" alone would reach the far edge */
          position: absolute;
          left: 0;
          top: calc(50% - 9px);
          transform: translateY(-50%);
          max-width: 100%;
          font-size: 14px;
          font-weight: 500;
          line-height: 1;
        }
        .tbr {
          font-size: 10px;
          color: var(--error-color);
          vertical-align: top;
        }
        .label {
          grid-column: 1 / 2; /* both lines: "1" alone would reach the far edge */
          position: absolute;
          left: 0;
          top: calc(50% + 9px);
          transform: translateY(-50%);
          max-width: 100%;
          font-size: 11px;
          color: var(--bs-text-faded);
          line-height: 1;
        }
        .state {
          grid-column: 2;
          align-self: start;
          position: relative;
          top: 50%;
          transform: translateY(-50%);
          font-size: 14px;
          line-height: normal;
        }
      `;
    }
  }

  if (!customElements.get("battery-states-card")) {
    customElements.define("battery-states-card", BatteryStatesCard);
  }
})();
