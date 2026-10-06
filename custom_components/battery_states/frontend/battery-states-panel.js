// Battery States settings page (opened by the integration's Configure button).
// Built from Home Assistant's own components so it looks like HA.

(async () => {
  // HA's app shell is always defined (unlike the dashboard panel) and is a
  // direct LitElement subclass; its prototype carries html/css.
  await customElements.whenDefined("home-assistant-main");
  const LitElement = Object.getPrototypeOf(customElements.get("home-assistant-main"));
  const html = LitElement.prototype.html;
  const css = LitElement.prototype.css;

  const mdiArrowLeft = "M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z";
  const mdiPencil =
    "M20.71,7.04C21.1,6.65 21.1,6 20.71,5.63L18.37,3.29C18,2.9 17.35,2.9 16.96,3.29L15.12,5.12L18.87,8.87M3,17.25V21H6.75L17.81,9.93L14.06,6.18L3,17.25Z";
  const mdiRestore =
    "M13,3A9,9 0 0,0 4,12H1L4.89,15.89L4.96,16.03L9,12H6A7,7 0 0,1 13,5A7,7 0 0,1 20,12A7,7 0 0,1 13,19C11.07,19 9.32,18.21 8.06,16.94L6.64,18.36C8.27,20 10.5,21 13,21A9,9 0 0,0 22,12A9,9 0 0,0 13,3Z";
  const mdiClose =
    "M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z";
  const mdiMenuUp = "M7,15L12,10L17,15H7Z";
  const mdiMenuDown = "M7,10L12,15L17,10H7Z";
  const mdiPlus = "M19,13H13V19H11V13H5V11H11V5H13V11H19V13Z";

  // ha-form isn't always loaded on a freshly opened page; the entities card
  // editor loads it (the usual way for custom code to get it).
  const ensureForm = async () => {
    if (customElements.get("ha-form") && customElements.get("ha-selector")) return;
    try {
      const helpers = await window.loadCardHelpers();
      const card = await helpers.createCardElement({ type: "entities", entities: [] });
      await card.constructor.getConfigElement();
    } catch (err) {
      console.error("battery-states-panel: could not load ha-form", err);
    }
    await customElements.whenDefined("ha-form");
  };

  const FILTER_LABELS = {
    include_integrations: "Integrations",
    include_areas: "Areas",
    include_labels: "Labels",
    include_devices: "Devices",
    exclude: "Excluded battery sensors",
  };

  const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

  const KINDS = {
    low: "Low battery",
    not_responding: "Not responding",
    recovered: "Responding again",
    not_seen: "Stopped reporting", // before 1.1.0
    reminder: "Reminder",
    test: "Test message",
  };
  const STATUS = {
    sending: ["Sending", "wait"],
    sent: ["Sent", "sent"],
    partly: ["Partly sent", "held"],
    held: ["Held", "held"],
    skipped: ["Skipped", "skip"],
    not_sent: ["Not sent", "fail"],
    unknown: ["Unknown", "skip"],
    info: ["Noted", "skip"],
  };

  // The integration's alert texts (monitor.py), for the examples on this page.
  const where = (area) => (area ? ` located in the ${area.toUpperCase()}` : "");
  const word = (type) => (type === "Rechargeable" ? "recharging" : "replacing");
  const lowText = (name, area, type, percent) =>
    `The battery level for the device ${name.toUpperCase()}${where(area)}, with battery type: ${type.toUpperCase()}, has dropped to ${percent}%. Consider ${word(type)} soon!`;
  // "4 Oct 14:43", as the integration writes times in its alerts.
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const alertTime = (t) =>
    `${t.getDate()} ${MONTHS[t.getMonth()]} ${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
  const notRespondingText = (name, area, type, when, every, percent) =>
    `The device ${name.toUpperCase()}${where(area)}, with battery type: ${type.toUpperCase()}, is not responding: no report since ${when}, while it usually reports at least every ${every}. Last battery level: ${percent}%. Check its battery, the device and its connection.`;
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;
  const reminderText = (low, broken, limit) => {
    const parts = [];
    if (low) parts.push(`${plural(low, "device", "devices")} with the battery level below ${limit}%`);
    if (broken) parts.push(`${plural(broken, "device", "devices")} not responding`);
    const advice =
      low && !broken
        ? `Consider replacing or recharging ${low === 1 ? "it" : "them"} soon!`
        : broken && !low
          ? `Check ${broken === 1 ? "it" : "them"} soon!`
          : "Check them soon!";
    return `You still have ${parts.join(" and ")}. ${advice}`;
  };

  // "45 minutes", "2 hours", "3 days" (as the integration writes them).
  const duration = (seconds) => {
    if (seconds < 3600) {
      const m = Math.max(1, Math.round(seconds / 60));
      return `${m} minute${m === 1 ? "" : "s"}`;
    }
    if (seconds < 48 * 3600) {
      const h = Math.round(seconds / 3600);
      return `${h} hour${h === 1 ? "" : "s"}`;
    }
    return `${Math.round(seconds / 86400)} days`;
  };

  const COLUMNS = [
    { key: "area", label: "Area" },
    { key: "shown_name", label: "Name" },
    { key: "shown_type", label: "Type" },
  ];

  const compareText = (a, b) => (a || "").localeCompare(b || "", undefined, { sensitivity: "base" });

  class BatteryStatesPanel extends LitElement {
    static get properties() {
      return {
        hass: { attribute: false },
        narrow: { type: Boolean },
        route: { attribute: false },
        panel: { attribute: false },
        _data: { state: true },
        _error: { state: true },
        _busy: { state: true },
        _formReady: { state: true },
        _sort: { state: true },
        _filters: { state: true },
        _alerts: { state: true },
        _limits: { state: true },
        _dialog: { state: true },
      };
    }

    constructor() {
      super();
      this._busy = false;
      this._formReady = false;
      this._sort = { key: "area", desc: false };
      this._dialog = null; // {mode: "add"} | {mode: "edit", battery, name, battery_type, rechargeable, editingType}
      this._onKey = (ev) => {
        if (ev.key === "Escape" && this._dialog) this._closeDialog();
      };
    }

    connectedCallback() {
      super.connectedCallback();
      window.addEventListener("keydown", this._onKey);
      ensureForm().then(() => (this._formReady = true));
      this._load();
    }

    disconnectedCallback() {
      super.disconnectedCallback();
      window.removeEventListener("keydown", this._onKey);
      this._unsubscribeLog();
    }

    // Recent alerts stay current while the page is open.
    async _subscribeLog() {
      if (this._logSub || !this.hass?.connection) return;
      this._logSub = this.hass.connection
        .subscribeMessage((ev) => {
          if (this._data) this._data = { ...this._data, log: ev.log };
        }, { type: "battery_states/subscribe_log" })
        .catch(() => (this._logSub = undefined));
    }

    _unsubscribeLog() {
      const sub = this._logSub;
      this._logSub = undefined;
      sub?.then((unsub) => unsub?.()).catch(() => {});
    }

    // card: where an error is shown (a card's name, or the top of the page).
    async _ws(msg, card) {
      this._busy = true;
      try {
        const data = await this.hass.callWS(msg);
        this._apply(data, msg.type);
        this._error = undefined;
        return true;
      } catch (err) {
        this._error = { card, message: err?.message || String(err) };
        return false;
      } finally {
        this._busy = false;
      }
    }

    // The Filters and Alerts forms are saved by their own buttons: another save
    // (a battery, Exclude, ...) keeps their unsaved edits and only brings in
    // what that save changed (e.g. Exclude adds to the excluded sensors).
    _apply(data, type) {
      const prev = this._data;
      this._data = data;
      if (!prev) this._subscribeLog();
      const fresh = !prev || type === "battery_states/get";
      const alerts = (d) => ({ notify_services: [...(d.notify_services || [])], ...d.alerts });
      this._filters =
        fresh || type === "battery_states/set_filters"
          ? { ...data.filters }
          : this._merge(this._filters, prev.filters, data.filters);
      this._alerts =
        fresh || type === "battery_states/set_alerts" || type === "battery_states/set_notify"
          ? alerts(data)
          : this._merge(this._alerts, alerts(prev), alerts(data));
      this._limits =
        fresh || type === "battery_states/set_limits"
          ? { ...data.limits }
          : this._merge(this._limits, prev.limits, data.limits);
    }

    _merge(current, before, after) {
      const out = {};
      for (const key of Object.keys(after)) {
        const now = after[key];
        if (!Array.isArray(now)) {
          // A single value: keep the user's unsaved change, else take the new one.
          out[key] = current && current[key] !== before?.[key] ? current[key] : now;
          continue;
        }
        const was = before?.[key] || [];
        // Every key was filled from the server, so a missing one was cleared.
        const edited = Array.isArray(current?.[key]) ? current[key] : [];
        const removed = was.filter((x) => !now.includes(x));
        const added = now.filter((x) => !was.includes(x));
        out[key] = [...edited.filter((x) => !removed.includes(x)), ...added.filter((x) => !edited.includes(x))];
      }
      return out;
    }

    _load() {
      if (this.hass) this._ws({ type: "battery_states/get" });
      else setTimeout(() => this._load(), 100);
    }

    _back() {
      if (history.length > 1) {
        history.back();
        return;
      }
      history.replaceState(null, "", "/config/integrations/integration/battery_states");
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: true } }));
    }

    // ------------------------------------------------------------ list

    _sortBy(key) {
      this._sort = this._sort.key === key ? { key, desc: !this._sort.desc } : { key, desc: false };
    }

    _sorted(batteries) {
      const { key, desc } = this._sort;
      const tieKeys = ["area", "shown_name", "shown_type"].filter((k) => k !== key);
      return [...batteries].sort((a, b) => {
        let r = compareText(a[key], b[key]);
        if (desc) r = -r;
        for (const k of tieKeys) if (!r) r = compareText(a[k], b[k]);
        return r || compareText(a.entity_id, b.entity_id);
      });
    }

    _openEdit(b) {
      this._dialog = {
        mode: "edit",
        battery: b,
        name: b.name,
        battery_type: b.battery_type,
        rechargeable: b.rechargeable,
        silence: b.silence_hours == null ? "" : String(b.silence_hours),
        editingType: false,
      };
    }

    _closeDialog() {
      this._dialog = null;
      if (this._error?.card === "dialog") this._error = undefined;
    }

    async _saveDialog() {
      const d = this._dialog;
      const typed = (d.battery_type || "").trim();
      const silenceText = String(d.silence ?? "").trim();
      const silence = silenceText === "" ? null : Number(silenceText);
      if (silence !== null && !(Number.isInteger(silence) && silence >= 1 && silence <= 720)) {
        this._error = { card: "dialog", message: "Not responding after: a whole number of hours from 1 to 720, or empty." };
        return;
      }
      const ok = await this._ws({
        type: "battery_states/update_battery",
        entity_id: d.battery.entity_id,
        name: (d.name || "").trim(),
        // Typing the library's own type (or nothing) means: use the library.
        battery_type: typed === d.battery.auto_type ? "" : typed,
        rechargeable: !!d.rechargeable,
        silence_hours: silence,
      }, "dialog");
      if (ok) this._closeDialog();
    }

    // Remove: takes back a battery added by hand (a filter may still find it).
    async _remove() {
      const b = this._dialog.battery;
      if (await this._ws({ type: "battery_states/remove_battery", entity_id: b.entity_id }, "dialog")) this._closeDialog();
    }

    // Exclude: hides it for good, even when a filter matches it.
    async _exclude() {
      const b = this._dialog.battery;
      if (await this._ws({ type: "battery_states/exclude_battery", entity_id: b.entity_id }, "dialog")) this._closeDialog();
    }

    // ------------------------------------------------------------ render

    render() {
      return html`
        <div class="toolbar">
          <ha-icon-button .path=${mdiArrowLeft} .label=${"Back"} @click=${this._back}></ha-icon-button>
          <div class="main-title">Battery States</div>
        </div>
        <div class="scroller">
          <div class="content">
            ${this._error && !this._error.card
              ? html`<ha-alert alert-type="error">${this._error.message}</ha-alert>`
              : ""}
            ${this._data ? this._renderAll() : html`<div class="loading">Loading…</div>`}
          </div>
        </div>
        ${this._dialog ? this._renderDialog() : ""}
      `;
    }

    _renderAll() {
      const d = this._data;
      return html`
        <ha-card>
          <div class="card-header">
            <span>Batteries</span>
            <ha-button appearance="plain" @click=${() => (this._dialog = { mode: "add" })}>
              <ha-svg-icon slot="start" .path=${mdiPlus}></ha-svg-icon>Add battery
            </ha-button>
          </div>
          ${(() => {
            const unjudged = d.batteries.filter((b) => b.health && b.health.judged === false).length;
            return unjudged
              ? html`<div class="card-content judge-hint">
                  ${unjudged === 1 ? "1 battery can't" : `${unjudged} batteries can't`} be checked for "not
                  responding" yet: open ${unjudged === 1 ? "it" : "one"} to see why.
                </div>`
              : "";
          })()}
          ${d.batteries.length
            ? this._renderTable(d.batteries)
            : html`<div class="card-content empty">No batteries yet.</div>`}
        </ha-card>

        <ha-card header="Filters">
          <div class="card-content">
            <div class="applied">
              <div class="applied-title">
                Applied now
                <span class="found">
                  ${d.found_count === 1 ? "1 battery found" : `${d.found_count} batteries found`}
                </span>
              </div>
              ${Object.entries(FILTER_LABELS).map(([key, label]) => {
                const values = d.applied_filters?.[key] || [];
                return html`<div class="applied-row">
                  <span class="applied-label">${label}</span>
                  <span class="applied-value ${values.length ? "" : "none"}">
                    ${values.length ? values.join(", ") : "none"}
                  </span>
                </div>`;
              })}
            </div>
            <p class="hint">Battery sensors matching any of the filters are added to the list automatically.</p>
            ${this._formReady
              ? html`<ha-form
                  .hass=${this.hass}
                  .data=${this._filters}
                  .schema=${this._filterSchema(d)}
                  .computeLabel=${(s) =>
                    s.name === "exclude" ? "Exclude these battery sensors" : FILTER_LABELS[s.name]}
                  @value-changed=${(ev) => (this._filters = ev.detail.value)}
                ></ha-form>`
              : ""}
            ${this._cardError("filters")}
          </div>
          <div class="card-actions">
            <ha-button
              .disabled=${this._busy}
              @click=${() => this._ws({ type: "battery_states/set_filters", ...this._filters }, "filters")}
              >Save filters</ha-button
            >
          </div>
        </ha-card>

        ${this._renderLimits()} ${this._renderAlerts(d)} ${this._renderLog(d.log || [])}
      `;
    }

    _cardError(card) {
      return this._error?.card === card
        ? html`<ha-alert alert-type="error">${this._error.message}</ha-alert>`
        : "";
    }

    _renderLimits() {
      const schema = [
        { name: "low_threshold", selector: { number: { min: 1, max: 99, step: 1, mode: "box", unit_of_measurement: "%" } } },
        { name: "not_seen_hours", selector: { number: { min: 1, max: 168, step: 1, mode: "box", unit_of_measurement: "hours" } } },
      ];
      const labels = { low_threshold: "Low battery limit", not_seen_hours: "Minimum silence" };
      const helpers = {
        low_threshold: "A battery at or below this is low: marked *TBR! on the card, counted, and alerted.",
        not_seen_hours:
          "A device counts as not responding only when it is silent far longer than usual for it (4 times its normal longest gap, learned from its own reports or from other devices of its model), never sooner than this, and only while the rest of its network works. Time Home Assistant or its network was down doesn't count. A battery's own limit (set by tapping it) replaces this.",
      };
      return html`
        <ha-card header="Limits">
          <div class="card-content">
            <p class="hint">Used everywhere: the card, the low count and the alerts.</p>
            ${this._formReady
              ? html`<ha-form
                  .hass=${this.hass}
                  .data=${this._limits}
                  .schema=${schema}
                  .computeLabel=${(f) => labels[f.name]}
                  .computeHelper=${(f) => helpers[f.name]}
                  @value-changed=${(ev) => (this._limits = ev.detail.value)}
                ></ha-form>`
              : ""}
            ${this._cardError("limits")}
          </div>
          <div class="card-actions">
            <ha-button .disabled=${this._busy} @click=${() => this._ws({ type: "battery_states/set_limits", ...this._limits }, "limits")}
              >Save</ha-button
            >
          </div>
        </ha-card>
      `;
    }

    // One part of the Alerts card: a form bound to the shared alert settings.
    _alertForm(schema, labels, helpers = {}) {
      return this._formReady
        ? html`<ha-form
            .hass=${this.hass}
            .data=${this._alerts}
            .schema=${schema}
            .computeLabel=${(f) => labels[f.name]}
            .computeHelper=${(f) => helpers[f.name]}
            @value-changed=${(ev) => (this._alerts = { ...this._alerts, ...ev.detail.value })}
          ></ha-form>`
        : "";
    }

    _example(text) {
      return html`<div class="example"><span class="example-label">Example message</span><b>Batteries</b><br />${text}</div>`;
    }

    _renderAlerts(d) {
      const a = this._alerts || {};
      const low = parseInt(this._limits?.low_threshold, 10) || d.limits?.low_threshold || 20;
      const hours = parseInt(this._limits?.not_seen_hours, 10) || d.limits?.not_seen_hours || 12;
      const yesterday = new Date();
      yesterday.setDate(yesterday.getDate() - 1);
      yesterday.setHours(14, 43, 0, 0);
      // The examples use a battery from the list (one with an area if there is one).
      const b = d.batteries.find((x) => x.area) || d.batteries[0];
      const ex = b ? { name: b.shown_name, area: b.area, type: b.shown_type } : { name: "Window Contact", area: "Bedroom", type: "CR2032" };
      const time = { time: { no_second: true } };
      return html`
        <ha-card header="Alerts">
          <div class="card-content">
            <p class="hint">Notifications with the title "Batteries".</p>
            ${this._alertForm(
              [{ name: "notify_services", selector: { select: { options: d.notify_options, custom_value: true, multiple: true, mode: "dropdown" } } }],
              { notify_services: "Send to" }
            )}

            <div class="alert-block ${a.alert_low ? "" : "off"}">
              ${this._alertForm([{ name: "alert_low", selector: { boolean: {} } }], { alert_low: "Low battery" }, {
                alert_low: `When a battery drops to or below ${low} %. Once per drop; it can come again after the battery was replaced or recharged.`,
              })}
              ${this._example(lowText(ex.name, ex.area, ex.type, low))}
            </div>

            <div class="alert-block ${a.alert_not_seen ? "" : "off"}">
              ${this._alertForm([{ name: "alert_not_seen", selector: { boolean: {} } }], { alert_not_seen: "Not responding" }, {
                alert_not_seen: `When a device goes silent far longer than usual for it (at least ${hours} hours) while the rest of its network works. Once per silence; not again if it drops out within a day of coming back.`,
              })}
              ${this._example(notRespondingText(ex.name, ex.area, ex.type, alertTime(yesterday), "2 hours", 100))}
            </div>

            <div class="alert-block ${a.reminder ? "" : "off"}">
              ${this._alertForm([{ name: "reminder", selector: { boolean: {} } }], { reminder: "Reminder" }, {
                reminder: "On the chosen days, only while at least one battery is low or a device is not responding.",
              })}
              ${a.reminder
                ? this._alertForm(
                    [
                      {
                        name: "reminder_days",
                        selector: { select: { multiple: true, mode: "dropdown", options: WEEKDAYS.map((label, i) => ({ value: String(i), label })) } },
                      },
                      { name: "reminder_time", selector: time },
                    ],
                    { reminder_days: "Days", reminder_time: "Time" }
                  )
                : ""}
              ${this._example(reminderText(1, 1, low))}
            </div>

            <div class="alert-block ${a.quiet_hours ? "" : "off"}">
              ${this._alertForm([{ name: "quiet_hours", selector: { boolean: {} } }], { quiet_hours: "Quiet hours" }, {
                quiet_hours:
                  "Alerts that come up in this window wait until it ends, and are sent then only if they are still true. A reminder due in the window is sent when it ends.",
              })}
              ${a.quiet_hours
                ? this._alertForm(
                    [
                      { name: "quiet_from", selector: time },
                      { name: "quiet_to", selector: time },
                    ],
                    { quiet_from: "From", quiet_to: "To" }
                  )
                : ""}
            </div>
            ${this._cardError("alerts")}
          </div>
          <div class="card-actions">
            <ha-button .disabled=${this._busy} @click=${() => this._saveAlerts()}>Save</ha-button>
          </div>
        </ha-card>
      `;
    }

    async _saveAlerts() {
      const a = this._alerts || {};
      const saved = this._data.alerts || {};
      if (a.reminder && !a.reminder_time) {
        this._error = { card: "alerts", message: "Pick a time for the reminder." };
        return;
      }
      if (a.quiet_hours && (!a.quiet_from || !a.quiet_to)) {
        this._error = { card: "alerts", message: "Pick when quiet hours start and end." };
        return;
      }
      await this._ws(
        {
          type: "battery_states/set_alerts",
          notify_services: a.notify_services || [],
          alert_low: !!a.alert_low,
          alert_not_seen: !!a.alert_not_seen,
          reminder: !!a.reminder,
          reminder_days: (a.reminder_days || []).map(Number),
          // A switched-off part keeps its saved time if its field was cleared.
          reminder_time: a.reminder_time || saved.reminder_time,
          quiet_hours: !!a.quiet_hours,
          quiet_from: a.quiet_from || saved.quiet_from,
          quiet_to: a.quiet_to || saved.quiet_to,
        },
        "alerts"
      );
    }

    _svc(service) {
      return service.startsWith("notify.") ? service.slice(7) : service;
    }

    // What happened to a message, in words.
    _outcome(e) {
      const ok = (e.targets || []).filter((t) => t.ok).map((t) => this._svc(t.service));
      const bad = (e.targets || []).filter((t) => !t.ok).map((t) => `${this._svc(t.service)} ${t.error}`);
      const parts = [];
      if (e.status === "sending") parts.push("Sending…");
      if (ok.length) parts.push(`Sent to ${ok.join(", ")}`);
      if (bad.length) parts.push(`Not sent: ${bad.join("; ")}`);
      if (e.note) parts.push(e.note.charAt(0).toUpperCase() + e.note.slice(1));
      return parts.join(". ");
    }

    // As the user's profile sets it: 12/24-hour clock, browser or server time zone.
    _when(iso) {
      const t = new Date(iso);
      const loc = this.hass?.locale || {};
      const lang = loc.time_format === "system" ? undefined : loc.language;
      const zone = loc.time_zone === "server" ? this.hass?.config?.time_zone : undefined;
      const opts = { hour: "2-digit", minute: "2-digit", timeZone: zone };
      if (loc.time_format === "12") opts.hour12 = true;
      if (loc.time_format === "24") opts.hour12 = false;
      const hm = t.toLocaleTimeString(lang, opts);
      const ymd = (x) => new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(x);
      const days = Math.round((Date.parse(ymd(new Date())) - Date.parse(ymd(t))) / 86400000);
      const day =
        days === 0
          ? "Today"
          : days === 1
            ? "Yesterday"
            : days < 7
              ? t.toLocaleDateString(lang, { weekday: "short", timeZone: zone })
              : t.toLocaleDateString(lang, { day: "numeric", month: "short", timeZone: zone });
      return [day, hm];
    }

    _renderLog(log) {
      const what = (e) => {
        const place = [e.name, e.area].filter(Boolean).join(", ");
        if (e.kind === "low") return [place, e.detail].filter(Boolean).join(", ");
        if (["not_seen", "not_responding", "recovered"].includes(e.kind)) return place;
        if (e.kind === "reminder") {
          // Before 1.1.0 the detail was just the number.
          return /^\d+$/.test(e.detail || "")
            ? `${e.detail} ${e.detail === "1" ? "battery" : "batteries"} low or not seen`
            : e.detail;
        }
        return "";
      };
      return html`
        <ha-card header="Recent alerts">
          <div class="card-content"><p class="hint">The last 50 messages, newest first. Tap one to read it.</p></div>
          ${log.length
            ? html`<ul class="log">
                ${log.map((e) => {
                  const [label, cls] = STATUS[e.status] || [e.status, "skip"];
                  const detail = what(e);
                  return html`<li>
                    <time>${this._when(e.time).map((part) => html`<span>${part}</span>`)}</time>
                    <details>
                      <summary><span class="kind">${KINDS[e.kind] || e.kind}</span>${detail ? html` · ${detail}` : ""}</summary>
                      <div class="message">${e.message}</div>
                    </details>
                    <div class="meta"><span class="pill ${cls}">${label}</span><span>${this._outcome(e)}</span></div>
                  </li>`;
                })}
              </ul>`
            : html`<div class="card-content empty">No alerts yet.</div>`}
        </ha-card>
      `;
    }

    _renderTable(batteries) {
      return html`
        <div class="table" role="table">
          <div class="row head" role="row">
            ${COLUMNS.map(
              (c) => html`<button
                class="cell sort ${this._sort.key === c.key ? "active" : ""}"
                role="columnheader"
                @click=${() => this._sortBy(c.key)}
              >
                ${c.label}
                ${this._sort.key === c.key
                  ? html`<ha-svg-icon .path=${this._sort.desc ? mdiMenuDown : mdiMenuUp}></ha-svg-icon>`
                  : ""}
              </button>`
            )}
            <span class="cell edit"></span>
          </div>
          ${this._sorted(batteries).map(
            (b) => html`<div class="row item" role="row" @click=${() => this._openEdit(b)}>
              <span class="cell">${b.area || "—"}</span>
              <span class="cell name"
                >${b.shown_name}${b.health?.status === "not_responding"
                  ? html` <span class="badge warn">Non-responsive</span>`
                  : ""}</span
              >
              <span class="cell">${b.shown_type}</span>
              <span class="cell edit">
                <ha-icon-button .path=${mdiPencil} .label=${"Edit"}></ha-icon-button>
              </span>
            </div>`
          )}
        </div>
      `;
    }

    _filterSchema(d) {
      return [
        {
          name: "include_integrations",
          selector: { select: { multiple: true, mode: "dropdown", options: d.integration_options } },
        },
        { name: "include_areas", selector: { area: { multiple: true } } },
        { name: "include_labels", selector: { label: { multiple: true } } },
        { name: "include_devices", selector: { device: { multiple: true } } },
        {
          name: "exclude",
          selector: { entity: { multiple: true, filter: { domain: "sensor", device_class: "battery" } } },
        },
      ];
    }

    // ------------------------------------------------------------ pop-up

    _renderDialog() {
      const d = this._dialog;
      return html`
        <div class="scrim" @click=${() => this._closeDialog()}>
          <div class="dialog" role="dialog" aria-modal="true" @click=${(ev) => ev.stopPropagation()}>
            ${d.mode === "add" ? this._renderAddDialog() : this._renderEditDialog(d)}
            ${this._error?.card === "dialog" ? html`<div class="dialog-error">${this._cardError("dialog")}</div>` : ""}
          </div>
        </div>
      `;
    }

    // How the battery is checked for "not responding", in words.
    _renderHealth(b) {
      const h = b.health || {};
      if (!h.mode) return "";
      const when = (iso) => (iso ? this._when(iso).join(", ") : "");
      const model = h.model || "devices of its model";
      const how = {
        last_seen: "By its Last seen sensor.",
        unavailable:
          "When its integration marks it unavailable (some devices, like sleepy Bluetooth sensors, never are: set a limit below to watch those).",
        activity: "By any update from the device (your limit below).",
      }[h.mode];
      let normal = "";
      if (h.source === "own") normal = `Usually reports at least every ${duration(h.normal)} (its own rhythm).`;
      else if (h.source === "model")
        normal = `Usually reports at least every ${duration(h.normal)} (like ${h.twins} other ${model}).`;
      let verdict;
      if (h.judged) {
        verdict =
          h.source === "setting"
            ? `Not responding after ${duration(h.threshold)} of silence (your limit).`
            : h.source === "unavailable"
              ? `Not responding after ${duration(h.threshold)} unavailable, while the rest of its network works.`
              : `Not responding after ${duration(h.threshold)} of silence, while the rest of its network works.`;
      } else {
        verdict = {
          learning: `Not checked yet: still learning how often it reports (${h.reports} reports over ${h.observed_days} days). It needs a steady rhythm over 3 days, or 2 other ${model} with one.`,
          irregular: `Not checked: it reports irregularly, and there aren't 2 other ${model} with a steady rhythm to compare with. Set a limit below to watch it.`,
          alone: "Not checked: no other monitored device on its network can show that the network works. Set a limit below to watch it anyway.",
          no_report: "Not checked yet: no report from it so far.",
        }[h.reason] || "Not checked.";
      }
      const network =
        h.network === "down"
          ? "Its network seems down right now: it isn't judged until the network works again."
          : h.network === "alone" && h.judged
            ? "Alone on its network: your limit is used without that check."
            : "";
      const status =
        h.status === "not_responding"
          ? html`<div class="health-status warn">Not responding since ${when(h.since)}</div>`
          : "";
      return html`
        <div class="health">
          <div class="health-title">Not responding check</div>
          ${status}
          <div>${how}</div>
          ${normal ? html`<div>${normal}</div>` : ""}
          <div>${verdict}</div>
          ${network ? html`<div>${network}</div>` : ""}
          ${h.last_report ? html`<div class="muted">Last report: ${when(h.last_report)}</div>` : ""}
          ${b.last_seen_disabled
            ? html`<div class="muted">Its Last seen sensor is disabled: enable it for the most precise check.</div>`
            : ""}
        </div>
      `;
    }

    _renderAddDialog() {
      return html`
        <div class="dialog-head">
          <div class="dialog-title">Add battery</div>
          <ha-icon-button .path=${mdiClose} .label=${"Close"} @click=${() => this._closeDialog()}></ha-icon-button>
        </div>
        <div class="dialog-body">
          ${this._formReady
            ? html`<ha-selector
                .hass=${this.hass}
                .selector=${{ entity: { filter: { domain: "sensor", device_class: "battery" } } }}
                .label=${"Battery sensor"}
                .value=${""}
                @value-changed=${async (ev) => {
                  const id = ev.detail.value;
                  if (!id) return;
                  if (await this._ws({ type: "battery_states/add_battery", entity_id: id }, "dialog")) {
                    const b = this._data.batteries.find((x) => x.entity_id === id);
                    if (b) this._openEdit(b);
                    else this._closeDialog();
                  }
                }}
              ></ha-selector>`
            : ""}
        </div>
      `;
    }

    _renderEditDialog(d) {
      const b = d.battery;
      const typed = (d.battery_type || "").trim();
      const overridden = !!typed && typed !== b.auto_type;
      // The library already says the battery is rechargeable (built in, like the
      // SwitchBot curtains): the tick is shown ticked and can't be changed.
      const builtIn = !overridden && b.auto_type === "Rechargeable";
      // The library has no entry for this device (the integration says "Unknown").
      const notInLibrary = b.auto_type === "Unknown";
      return html`
        <div class="dialog-head">
          <div class="dialog-titles">
            <div class="dialog-title">${b.area ? `${b.area}: ` : ""}${b.shown_name}</div>
            <div class="dialog-sub">${b.entity_id}</div>
          </div>
          <ha-icon-button .path=${mdiClose} .label=${"Close"} @click=${() => this._closeDialog()}></ha-icon-button>
        </div>
        <div class="dialog-body">
          <ha-input
            class="name"
            .label=${"Friendly name"}
            .placeholder=${b.auto_name}
            .hint=${`Empty = the name in Home Assistant (${b.auto_name})`}
            .value=${d.name}
            @input=${(ev) => (this._dialog = { ...this._dialog, name: ev.target.value ?? "" })}
          ></ha-input>
          <div class="type-row">
            <ha-input
              class="type ${d.editingType ? "" : "locked"}"
              .label=${overridden
                ? "Battery type (changed)"
                : notInLibrary
                  ? "Battery type (not in the library)"
                  : "Battery type (from the library)"}
              .value=${d.battery_type || b.auto_type}
              .readonly=${!d.editingType}
              @input=${(ev) => (this._dialog = { ...this._dialog, battery_type: ev.target.value ?? "" })}
            ></ha-input>
            <ha-icon-button
              class="edit-type ${d.editingType ? "active" : ""}"
              .path=${mdiPencil}
              .label=${"Edit battery type"}
              @click=${() => {
                const on = !this._dialog.editingType;
                this._dialog = { ...this._dialog, editingType: on };
                if (on) this.updateComplete.then(() => this.renderRoot.querySelector("ha-input.type")?.focus?.());
              }}
            ></ha-icon-button>
            ${overridden
              ? html`<ha-icon-button
                  class="reset-type"
                  .path=${mdiRestore}
                  .label=${notInLibrary ? "Clear the type (not in the library)" : "Use the library's type"}
                  @click=${() => (this._dialog = { ...this._dialog, battery_type: "", editingType: false })}
                ></ha-icon-button>`
              : ""}
          </div>
          <label class="recharge ${builtIn ? "builtin" : ""}">
            <ha-checkbox
              .checked=${builtIn || !!d.rechargeable}
              .disabled=${builtIn}
              @change=${(ev) => (this._dialog = { ...this._dialog, rechargeable: ev.target.checked })}
            ></ha-checkbox>
            ${builtIn ? "Rechargeable (built-in battery, from the library)" : "Rechargeable"}
          </label>
          ${this._renderHealth(b)}
          <ha-input
            class="silence"
            type="number"
            .label=${"Not responding after (hours of silence)"}
            .placeholder=${"Automatic"}
            .hint=${"Empty = automatic. Set it for a device you know: it then counts as not responding after this much silence (1–720 hours), even alone on its network."}
            .value=${d.silence}
            @input=${(ev) => (this._dialog = { ...this._dialog, silence: ev.target.value ?? "" })}
          ></ha-input>
        </div>
        <div class="dialog-actions">
          ${b.added
            ? html`<ha-button
                class="remove"
                variant="danger"
                appearance="plain"
                title=${b.matched ? "Take back the hand-added entry; a filter still finds this battery" : "Remove this battery"}
                .disabled=${this._busy}
                @click=${() => this._remove()}
                >Remove</ha-button
              >`
            : ""}
          ${b.matched
            ? html`<ha-button
                class="exclude"
                variant="danger"
                appearance="plain"
                title="Hide this battery for good, even though a filter matches it"
                .disabled=${this._busy}
                @click=${() => this._exclude()}
                >Exclude</ha-button
              >`
            : ""}
          <span class="spacer"></span>
          <ha-button class="cancel" appearance="plain" @click=${() => this._closeDialog()}>Cancel</ha-button>
          <ha-button class="save" .disabled=${this._busy} @click=${() => this._saveDialog()}>Save</ha-button>
        </div>
      `;
    }

    static get styles() {
      return css`
        /* Like HA's sub-pages: fixed header, the content scrolls below it. */
        :host {
          display: flex;
          flex-direction: column;
          height: 100%;
          min-height: 100vh;
          max-height: 100vh;
          background: var(--primary-background-color);
          color: var(--primary-text-color);
        }
        .scroller {
          flex: 1;
          overflow-y: auto;
          -webkit-overflow-scrolling: touch;
        }
        .toolbar {
          display: flex;
          align-items: center;
          height: var(--header-height, 56px);
          padding: 0 4px;
          padding-top: env(safe-area-inset-top);
          box-sizing: content-box;
          background: var(--app-header-background-color);
          color: var(--app-header-text-color, white);
          border-bottom: var(--app-header-border-bottom, none);
          font-size: var(--ha-font-size-xl, 20px);
          flex: none;
        }
        .main-title {
          margin-left: 12px;
          line-height: 26px;
        }
        .content {
          max-width: 800px;
          margin: 0 auto;
          padding: 16px;
          padding-bottom: calc(16px + env(safe-area-inset-bottom));
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        .loading,
        .empty {
          color: var(--secondary-text-color);
        }
        .card-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
        }

        /* Battery table */
        /* One grid for the whole table (rows use subgrid), so Area and Type
           are as wide as their longest value and only Name gets shortened. */
        .table {
          display: grid;
          grid-template-columns: max-content minmax(0, 1fr) max-content 48px;
          column-gap: 16px;
          padding: 0 0 8px;
        }
        .row {
          display: grid;
          grid-template-columns: subgrid;
          grid-column: 1 / -1;
          align-items: center;
          padding: 0 4px 0 16px;
          min-height: 48px;
          border-top: 1px solid var(--divider-color);
        }
        @media (max-width: 500px) {
          .table {
            column-gap: 10px;
            font-size: 13px;
          }
        }
        .row.head {
          min-height: 40px;
          border-top: none;
        }
        .row.item {
          cursor: pointer;
        }
        .row.item:hover {
          background: var(--secondary-background-color);
        }
        .cell {
          min-width: 0;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .cell.name {
          font-weight: 500;
        }
        .cell.edit {
          display: flex;
          justify-content: flex-end;
          overflow: visible; /* the button's 48 px hover circle must not be clipped */
          color: var(--secondary-text-color);
        }
        button.sort {
          display: flex;
          align-items: center;
          gap: 2px;
          padding: 0;
          margin: 0;
          border: none;
          background: none;
          color: var(--secondary-text-color);
          font: inherit;
          font-size: var(--ha-font-size-s, 12px);
          font-weight: 500;
          text-transform: uppercase;
          letter-spacing: 0.4px;
          text-align: left;
          cursor: pointer;
          --mdc-icon-size: 18px;
        }
        button.sort.active {
          color: var(--primary-text-color);
        }

        /* Filters */
        .applied {
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          padding: 8px 12px;
          margin-bottom: 12px;
        }
        .applied-title {
          display: flex;
          justify-content: space-between;
          gap: 8px;
          font-weight: 500;
          margin-bottom: 4px;
        }
        .found {
          color: var(--secondary-text-color);
          font-weight: 400;
        }
        .applied-row {
          display: grid;
          grid-template-columns: minmax(110px, 170px) 1fr;
          gap: 8px;
          padding: 2px 0;
        }
        .applied-label {
          color: var(--secondary-text-color);
        }
        .applied-value.none {
          color: var(--secondary-text-color);
          font-style: italic;
        }
        .hint {
          color: var(--secondary-text-color);
          margin: 0 0 8px;
        }

        /* Alerts */
        .alert-block {
          display: flex;
          flex-direction: column;
          gap: 8px;
          padding: 12px 0;
          border-top: 1px solid var(--divider-color);
        }
        .alert-block.off .example {
          opacity: 0.45;
        }
        .example {
          background: var(--secondary-background-color);
          border-radius: 8px;
          padding: 10px 12px;
          font-size: var(--ha-font-size-s, 13px);
          overflow-wrap: anywhere;
        }
        .example-label {
          display: block;
          color: var(--secondary-text-color);
          font-size: 11px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }

        /* Recent alerts */
        .log {
          list-style: none;
          margin: 0;
          padding: 0 0 8px;
        }
        .log li {
          display: grid;
          grid-template-columns: 6.5em minmax(0, 1fr);
          gap: 2px 12px;
          padding: 10px 16px;
          border-top: 1px solid var(--divider-color);
        }
        .log time {
          display: flex;
          flex-direction: column; /* day, then time: every row lines up the same */
          grid-row: span 2; /* beside both the title and the status line */
          color: var(--secondary-text-color);
          font-size: var(--ha-font-size-s, 13px);
          font-variant-numeric: tabular-nums;
          white-space: nowrap;
        }
        .log details {
          min-width: 0;
        }
        .log summary {
          cursor: pointer;
          overflow-wrap: anywhere;
        }
        .log .kind {
          font-weight: 500;
        }
        .log .message {
          margin-top: 4px;
          color: var(--secondary-text-color);
          overflow-wrap: anywhere;
        }
        .log .meta {
          grid-column: 2;
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 8px;
          color: var(--secondary-text-color);
          font-size: var(--ha-font-size-s, 12px);
        }
        .pill {
          border-radius: 10px;
          padding: 1px 8px;
          font-weight: 500;
        }
        .pill.sent {
          background: rgba(var(--rgb-success-color, 67, 160, 71), 0.15);
          color: var(--success-color, #43a047);
        }
        .pill.held,
        .pill.wait {
          background: rgba(var(--rgb-warning-color, 255, 166, 0), 0.15);
          color: var(--warning-color, #ffa600);
        }
        .pill.skip {
          background: var(--secondary-background-color);
          color: var(--secondary-text-color);
        }
        .pill.fail {
          background: rgba(var(--rgb-error-color, 219, 68, 55), 0.15);
          color: var(--error-color, #db4437);
        }

        /* Pop-up */
        .scrim {
          position: fixed;
          inset: 0;
          z-index: 10;
          display: flex;
          align-items: center;
          justify-content: center;
          box-sizing: border-box;
          background: var(--mdc-dialog-scrim-color, rgba(0, 0, 0, 0.5));
          padding: 16px;
        }
        .dialog {
          width: 100%;
          max-width: 480px;
          max-height: 100%;
          overflow: auto;
          border-radius: var(--ha-dialog-border-radius, 24px);
          /* The theme's dialog colour, painted over the opaque page background
             (some themes, like iOS ones, make it see-through). */
          background-color: var(--primary-background-color);
          background-image: linear-gradient(
            var(--ha-dialog-surface-background, var(--mdc-theme-surface, transparent)),
            var(--ha-dialog-surface-background, var(--mdc-theme-surface, transparent))
          );
          color: var(--primary-text-color);
          box-shadow: var(--ha-box-shadow-l, 0 8px 24px rgba(0, 0, 0, 0.4));
        }
        .dialog-head {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 8px;
          padding: 20px 12px 4px 24px;
        }
        .dialog-titles {
          min-width: 0;
        }
        .dialog-title {
          font-size: var(--ha-font-size-xl, 20px);
          line-height: 1.3;
        }
        .dialog-sub {
          color: var(--secondary-text-color);
          font-size: var(--ha-font-size-s, 12px);
          overflow-wrap: anywhere;
        }
        .dialog-body {
          display: flex;
          flex-direction: column;
          gap: 16px;
          padding: 12px 24px;
        }
        .type-row {
          display: flex;
          align-items: center;
          gap: 4px;
        }
        ha-input.type {
          flex: 1;
        }
        ha-input.type.locked {
          opacity: 0.6;
        }
        ha-icon-button.active {
          color: var(--primary-color);
        }
        .recharge {
          display: flex;
          align-items: center;
          gap: 4px;
          cursor: pointer;
          user-select: none;
        }
        .recharge.builtin {
          cursor: default;
          color: var(--secondary-text-color);
        }
        .dialog-actions {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 16px 16px 16px;
        }
        .spacer {
          flex: 1;
        }
        .dialog-error {
          padding: 0 24px 16px;
        }
        .health {
          display: flex;
          flex-direction: column;
          gap: 4px;
          padding: 10px 12px;
          border-radius: 8px;
          background: var(--secondary-background-color);
          font-size: var(--ha-font-size-s, 13px);
        }
        .health-title {
          color: var(--secondary-text-color);
          font-size: 11px;
          letter-spacing: 0.06em;
          text-transform: uppercase;
        }
        .health-status.warn {
          color: var(--warning-color, #ffa600);
          font-weight: 500;
        }
        .health .muted,
        .judge-hint {
          color: var(--secondary-text-color);
        }
        .judge-hint {
          padding-top: 0;
          padding-bottom: 8px;
        }
        .badge {
          border-radius: 10px;
          padding: 1px 8px;
          font-size: 11px;
          font-weight: 500;
          margin-left: 6px;
        }
        .badge.warn {
          background: rgba(var(--rgb-warning-color, 255, 166, 0), 0.15);
          color: var(--warning-color, #ffa600);
        }
      `;
    }
  }

  if (!customElements.get("battery-states-panel")) {
    customElements.define("battery-states-panel", BatteryStatesPanel);
  }
})();
