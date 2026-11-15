# mcp-screen

reTerminal E1001 e-paper MCP server for the [reSpeaker agent](https://gitlab.zzapps.nl/development/respeaker-agent).
Shows live Home Assistant dashboards (and ad-hoc screens) on the 800x480 1-bit e-paper:
renders a PNG, **hosts it** on the LAN, and triggers the device's `refresh_screen` ESPHome
service so it fetches and redraws. Speaks **streamable-HTTP** so the agent connects by `url`.

Two ports in one process:
- **MCP** endpoint (`MCP_PORT`, default 8788): the agent calls `http://<host>:8788/mcp`.
- **HTTP host** (`SCREEN_PORT`, default 8799): `/screen.png` (what is on screen),
  `/nav/home.png`, `/nav/next.png`, `/nav/prev.png` (the device buttons), `/assets/*`
  (fonts and icons for the renderer).

## Tools

| tool | what |
|---|---|
| `list_pages()` | the saved screens (dashboards) with a description, and which one is shown |
| `show_page(name)` | show a saved screen (live, keeps updating) |
| `show_dashboard(widgets, title="", save_as="", description="")` | compose a dashboard from widgets; `save_as` keeps it as a named page (it joins the button rotation) |
| `delete_page(name)` | remove a saved page (a built-in one comes back) |
| `show_text(title, body="")` | a text screen |
| `show_image(url)` | an image, dithered to 1-bit |
| `screen_components()` | the card building blocks, fields and examples |
| `show_card(card, save_as="", description="")` | a free-form card from those blocks (TRMNL Framework) |
| `show_html(html)` | free HTML/CSS (framework loaded), for drawings cards can't do |
| `clear_screen()` | blank |

## Cards (free-form, TRMNL Framework)

`screen_components()` returns the building-block catalog (`cards.CATALOG`); `show_card(card)`
renders a card composed from it. A card is `{"title", "instance", "icon", "align", "body"}`;
the body is a tree of containers (`columns`, `stack`, `row`, `grid`) and elements (`value`,
`title`, `label`, `description`, `text`, `item`, `table`, `progress`, `divider`, `icon`,
`image`, `chart`, `map`, `compass`). Elements with `entity_id` show live Home Assistant values
and redraw on change; a card can be saved as a page (`save_as`).

`cards.py` turns the card into [TRMNL Framework](https://github.com/usetrmnl/trmnl-framework)
markup (MIT; `screen--og screen--1bit`, 800x480) inside `body.environment.trmnl`, so the
framework's e-ink design does the styling; all text is escaped. The page is served by this
add-on (`/render/<token>.html`, same origin as the framework's root-relative `/fonts/`) and
rendered in Chromium after the framework runtime settles (`TRMNL_PLUGINS_READY`).

- `chart`: Vega-Lite spec (BSD), rendered to SVG in the page; with `entity_ids` + `hours` the
  add-on supplies Home Assistant history as dataset `ha` (`t`, `v`, `naam`), and without a
  spec draws a line chart of it. Black only, series told apart by dash.
- `map`: PDOK BRT-Achtergrondkaart raster tiles ("grijs"; public, no key); outside the Netherlands
  OpenStreetMap standard tiles (tile usage policy, attribution on the map), thresholded at 190;
  stitched server-side, thresholded to line art, with route line and numbered markers; fits
  the points when no zoom is given. Online at render time (tiles cached in memory).
- `map` with `route: {van, naar, vervoer}`: the add-on looks up both places and the route itself
  (`geo.py`, a copy of mcp-geo's: OpenStreetMap Nominatim geocoding, FOSSGIS OSRM
  routing) and draws it with start and end markers, so the model never passes coordinates.
- `image`: fetched server-side, dithered (Floyd-Steinberg).
- `compass`: SVG rose with up to three arrows (direction they point to).

The framework CSS/JS (3.4.6, pinned commit), its fonts (SIL OFL 1.1; BlockKie CC BY 3.0, by
the TRMNL team) and Vega are fetched at image build time (see the Dockerfile) into
`mcp_screen/assets/{trmnl,trmnl-fonts,vendor}` and served at `/trmnl/`, `/fonts/`, `/vendor/`.
Framework charts (Highcharts, commercial) and maps (TRMNL tile server) are not used.
`show_html` loads the framework too, so free HTML can use its classes.

## Dashboards

A page is `{"title", "description", "widgets": [...]}`, laid out on a 4x3 grid (cells 200x140
under a 60 px header), first fit in order; what does not fit is left out and reported back.

| widget | size | options |
|---|---|---|
| `weather_now` | 2x1 (`large` 2x2) | `entity_id` (default the `weather_entity` option) |
| `forecast_hours` | 2x1 (`wide` 4x1) | `hours` (4, wide up to 8) |
| `forecast_days` | 2x2 | `days` (up to 5), `title` |
| `value` | 1x1 | `entity_id`, `label`, `icon` (MDI name) |
| `list` | 2x1, 2x2 when longer | `entity_ids`, `title` |
| `rooms` | 2x2 | `entity_ids` (climate; default all), `title` |
| `todo` | 2x2 (`large` 4x3) | `entity_id` (default `todo.shopping_list`), `title` |
| `text` | 2x1 (`wide` 4x1, `large` 4x3) | `text` or `entity_id` (shows its state), `title` |
| `price_chart` | 2x1 (`wide` 4x1) | `entity_id` (default the Zonneplan quarter-hour tariff sensor), `hours` (12, wide 24), `title` |

`price_chart` reads the entity's `forecast` attribute (entries with `start_date` and
`price_tax_included.amount` in 1e-7 €, as Zonneplan provides; `electricity_price` or a plain
`price` work too), averages per hour from the current hour on and draws bars (current hour
solid, the rest hatched) scaled from just under the cheapest hour. "nu" in its header is the
entity state (this quarter's price, like a value tile), "min" the cheapest hourly average.

Values are formatted per domain: lights show brightness % or aan/uit, covers their position,
climate the current temperature, money as `€ 1,23`. An explicit empty `title` hides a
widget's heading.

Built-in pages are in `mcp_screen/default_pages.json` (dashboard, weer, energie, binnen,
boodschappen; the first is home). Pages saved by the agent live in `/data/pages.json` and
override a built-in page with the same name.

## Updates

- every `refresh_minutes` the shown page is re-rendered (pushed only if the PNG changed);
- within seconds when an entity on it changes (HA websocket, `state_changed`): lights,
  switches, covers, climate, todo lists, weather. Numeric `sensor.*` states (power and the
  like) change constantly and wait for the periodic refresh, so the e-paper doesn't flash
  all the time;
- after `home_after_minutes` on another screen it returns to the home page (0 = never).

Rendering: headless Chromium, then a threshold to 1-bit (crisp text); only `show_image` is
dithered. Fonts (Inter) and icons (Material Design Icons) are served from `/assets`, fetched
at image build time, so rendering needs no internet. Home Assistant is read through the
Supervisor proxy (`homeassistant_api: true`): `GET /states`, `weather.get_forecasts` and
`todo.get_items` only.

## reTerminal firmware

`online_image` (PNG, BINARY, `update_interval: never`) pointed at
`http://<ha-host-ip>:8799/screen.png`, the `refresh_screen` API service (set the URL back to
`/screen.png` and update), and the three buttons loading `/nav/home.png` (green),
`/nav/next.png` (right) and `/nav/prev.png` (left). Draw it with
`it.image(0, 0, id(screen_img), COLOR_OFF, COLOR_ON)`: a BINARY online_image marks white as
"on", and COLOR_ON is black on this panel. The config lives in the Home Assistant repo
(`config/esphome/reterminal-e1001.yaml`).

## Home Assistant add-on

Bridged network, so the agent reaches the MCP at `http://<repo-hash>-mcp-screen:8788/mcp`. The
HTTP host (`8799`) is published to the host LAN via `ports` so the reTerminal can fetch it.
The device's ESPHome API (`:6053`) is reached **outbound by IP** — bridged containers can't do
mDNS, so set `reterminal_host` to the device's reserved IP (e.g. `192.168.18.152`). After an
update, restart the agent so it picks up new tools (it connects to MCP servers at startup).

## Options / env

| option | env | default |
|---|---|---|
| `reterminal_host` | `RETERMINAL_HOST` | `reterminal-e1001.local` |
| `screen_port` | `SCREEN_PORT` | `8799` |
| `mcp_port` | `MCP_PORT` | `8788` |
| `weather_entity` | `WEATHER_ENTITY` | `weather.forecast_home` |
| `refresh_minutes` | `REFRESH_MINUTES` | `15` |
| `home_after_minutes` | `HOME_AFTER_MINUTES` | `60` |
| — | `MCP_HOST` | `0.0.0.0` |
| — | `DATA_DIR` | `/data` |

## Local development

Outside the add-on, set `HA_URL` + `HA_TOKEN` (instead of the Supervisor API),
`SCREEN_NO_DEVICE=1` (never push to the device) and a writable `DATA_DIR`, fetch the assets
into `mcp_screen/assets/` (the five URLs in the Dockerfile; same paths), and run
`python server.py` from `mcp_screen/`. The nav endpoints render a page and return it, so
`curl http://127.0.0.1:8799/nav/next.png` steps through the pages.
