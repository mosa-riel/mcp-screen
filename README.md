# mcp-screen

reTerminal E1001 e-paper MCP server for the [reSpeaker agent](https://gitlab.zzapps.nl/development/respeaker-agent).
Renders a screen, **hosts the PNG** on the LAN, and triggers the device's
`refresh_screen` ESPHome service so it fetches + redraws. Speaks **streamable-HTTP** so
the agent connects by `url`.

Two ports in one process:
- **MCP** endpoint (`MCP_PORT`, default 8788) — the agent calls `http://<host>:8788/mcp`.
- **PNG host** (`SCREEN_PORT`, default 8799) — the reTerminal fetches `/screen.png`.

## Tools

`show_text(title, body="")`, `show_image(url)`, `clear_screen()`

## reTerminal firmware

Point the device's `online_image` url at this host:
`http://<ha-host-ip>:8799/screen.png` (`update_interval: never` + the `refresh_screen`
service). See `../ReSpeaker/devices/reterminal-e1001/`.

## Home Assistant add-on

Bridged network, so it gets a readable internal DNS name — the agent reaches the MCP at
`http://local-mcp-screen:8788/mcp` (uniform with the other MCPs). The PNG host (`8799`)
is published to the host LAN via a `ports` mapping so the reTerminal can fetch it. The
device's ESPHome API (`:6053`) is reached **outbound by IP** — bridged containers can't
do mDNS, so set `reterminal_host` to the device's reserved IP (e.g. `192.168.18.152`),
not `reterminal-e1001.local`. Copy this folder into the HA host's `/addons` dir → Install.

## Env / options

| env               | option            | default                  |
|-------------------|-------------------|--------------------------|
| `RETERMINAL_HOST` | `reterminal_host` | `reterminal-e1001.local` |
| `SCREEN_PORT`     | `screen_port`     | `8799`                   |
| `MCP_PORT`        | `mcp_port`        | `8788`                   |
| `MCP_HOST`        | —                 | `0.0.0.0`                |
