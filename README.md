# Battery States

[![Validate](https://github.com/GGSSDD/ha-battery-states/actions/workflows/validate.yml/badge.svg)](https://github.com/GGSSDD/ha-battery-states/actions/workflows/validate.yml)
[![hacs](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

A Home Assistant integration that keeps an eye on all your batteries in one place:

- a **card** listing every battery with its level and battery type, sortable, groupable by area or type, and filterable to the low ones;
- **alerts** when a battery runs low, when a device stops reporting (its battery may be dead), and an optional **reminder** while anything is still low;
- **quiet hours**, a list of **recent alerts**, and a **settings page** to choose the batteries and everything else, with no YAML.

Battery types come from the community battery library of [Battery Notes](https://github.com/andrew-codechimp/HA-Battery-Notes), and you can set or correct any of them yourself.

## Requirements

- Home Assistant **2026.4.0** or newer.
- Battery sensors that report a **percentage**: sensors with the *battery* device class, as most integrations create them (Zigbee2MQTT, ZHA, Z-Wave, Matter, SwitchBot, ...).

Not supported: sensors that only say *battery low: on/off* (a binary sensor with the *battery* device class). They have no level to show or compare, so they can't be added.

## Installation

### HACS

Until Battery States is in the HACS default store, add it as a custom repository:

1. HACS → menu (⋮) → **Custom repositories**.
2. Repository: `https://github.com/GGSSDD/ha-battery-states`, type **Integration** → **Add**.
3. Find **Battery States** in HACS, **Download**, then restart Home Assistant.

### Manual

Copy `custom_components/battery_states` into your Home Assistant `config/custom_components/` folder and restart Home Assistant.

## Setup

1. **Settings → Devices & services → Add integration → Battery States.**
2. Choose where the alerts go (you can change this later). Both kinds of notify targets work:
   - notify services, such as `notify.mobile_app_<phone>`;
   - notify entities, such as a phone, a Telegram chat or anything else listed as `notify.<name>`.
3. Open the settings page: **Battery States → Configure**.

### The settings page

- **Batteries**: the monitored batteries. **Add battery** adds one sensor at a time. Tap a battery to give it a friendly name, set or correct its battery type, or mark it *Rechargeable*. A battery added by hand can be **removed**; a battery found by a filter can be **excluded** so it never shows.
- **Filters**: pick batteries automatically by **integration**, **area**, **label** or **device**. New devices that match are added on their own. **Excluded battery sensors** are never shown.
- **Limits**:
  - **Low battery limit** (default 20 %): at or below this a battery is low. It is marked `*TBR!` on the card, counted and alerted.
  - **Not seen after** (default 12 hours): how long a device may stay silent before it counts as *not seen*. See [Stopped reporting](#stopped-reporting) below.
- **Alerts**:
  - **Send to**: one or more notify targets.
  - **Low battery**: once per drop. It can come again after the battery was replaced or recharged.
  - **Stopped reporting**: once per silence.
  - **Reminder**: on the days and at the time you choose (default Tuesday, Thursday and Saturday at 20:00), only while at least one battery is low or not seen.
  - **Quiet hours** (off by default): alerts that come up in this window wait until it ends, and are sent then only if they are still true.
- **Recent alerts**: the last 50 alerts and what happened to each (sent, held for quiet hours, skipped and why, or not sent and why).

## The card

Add it from the dashboard editor (**Add card → Battery States**) or in YAML:

```yaml
type: custom:battery-states-card
```

The card finds the integration's sensor by itself. If you want to point it at a specific sensor, add `entity: sensor.<id>`.

- The summary at the top counts the low batteries per battery type.
- The **arrow** sorts by level, ascending or descending. **Group by** groups by area or by battery type. The **filter** button shows only the low batteries.
- Sort, group and filter are remembered **per Home Assistant user**: the same on all your devices, and separate for each person.
- Tap a battery to open its details.
- The card follows your theme: text, accent and error colours. It works on light and dark themes and down to about half a column wide in sections dashboards.

The integration also creates `sensor.battery_states_low_batteries`: the number of low (or not seen) batteries, handy for a badge or your own automations.

## Stopped reporting

A battery-powered device that dies usually just goes quiet, so its last level stays on screen. Battery States notices this in one of two ways:

- **The device has a Last seen sensor** (for example Zigbee2MQTT): the device counts as *not seen* when it hasn't been heard from for the **Not seen after** time.
- **No Last seen sensor**: the device counts as *not seen* when its battery sensor stays **unavailable** for the **Not seen after** time. A short outage, such as an integration reload, a Wi-Fi or cloud blip or a hub restart, doesn't count.

Time while Home Assistant (or, with a Last seen sensor, Zigbee2MQTT) was down is not counted as silence. A *not seen* battery shows as 0 % and counts as low.

If a device has **neither** a Last seen sensor nor ever goes unavailable, a dead battery can't be noticed. Some setups need a setting changed:

- **Zigbee2MQTT**: turn on one of these, or both:
  - **Last seen**: Zigbee2MQTT **Settings → Advanced → Last seen** = `ISO_8601`. Then, in Home Assistant, **enable** the device's *Last seen* entity (Home Assistant creates it disabled). The settings page tells you when a battery's Last seen sensor is disabled.
  - **Availability**: Zigbee2MQTT **Settings → Availability**, so that silent devices become unavailable.
- **ZHA**: battery-powered devices become unavailable after a set time without messages (a ZHA option), so this works without changes.

## Battery types

Each device's battery type is looked up in the [Battery Notes](https://github.com/andrew-codechimp/HA-Battery-Notes) community library, using the device's manufacturer and model. A copy of the library is included. A newer one is downloaded once a week from GitHub (`raw.githubusercontent.com`); if that fails, the copy you have keeps working. Unknown types show as *Unknown*, and you can set any type yourself on the settings page.

## Language

The card, the settings page and the alert texts are in English.

## Removing

1. **Settings → Devices & services → Battery States → Delete.** This also removes the integration's saved data.
2. Remove the card from your dashboards.
3. Remove the integration in HACS (or delete `custom_components/battery_states`) and restart Home Assistant.

## Credits and licence

- Battery Notes library: © Andrew Jackson, MIT licence (see `custom_components/battery_states/library/LICENSE`).
- Battery States: MIT licence, see [LICENSE](LICENSE).
