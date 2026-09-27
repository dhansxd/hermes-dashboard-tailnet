# Hermes Dashboard Tailnet

Telegram DM commands `/db on`, `/db off`, `/db status` (also `/dash` and `/dashboard`) start/stop the Hermes web dashboard and a **private Tailscale Serve** HTTPS proxy. No Hermes core changes. Dashboard binds `127.0.0.1:9119`; Serve owns HTTPS port `8443` on this node. `off` stops only the dashboard process and Serve endpoint this plugin owns; the gateway and Tailscale stay running. No Funnel.

> macOS/Linux only: requires `lsof`, `ps`, POSIX process signals, a working `hermes` executable, Tailscale CLI, and Hermes dashboard dependencies. Requires a configured Telegram Hermes gateway. The plugin refuses to operate without an explicit admin ID and dashboard login.

## 1. Prerequisites

1. [Install Hermes](https://hermes-agent.nousresearch.com/docs/) and [connect a Telegram bot](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram). Confirm your DM reaches Hermes. `hermes gateway status` should report a working gateway.
2. [Install Tailscale](https://tailscale.com/docs/install) on the Hermes host and the device you'll use to open the dashboard. Sign both into the **same tailnet**. Confirm `tailscale status` works on the host. In the [Tailscale DNS settings](https://login.tailscale.com/admin/dns), enable MagicDNS and HTTPS certificates; Serve must be permitted by your tailnet policy. `tailscale serve status` should work. This plugin never starts Tailscale itself.
3. Check `lsof -nP -iTCP:9119 -sTCP:LISTEN` on the host: port `9119` must be free. Keep HTTPS port `8443` free in `tailscale serve status`; plugin refuses to replace an existing endpoint. Do **not** enable Tailscale Funnel for this port.
4. Ensure `hermes dashboard --help` and `tailscale serve --help` work on the host. Hermes must have its dashboard dependencies installed. On first run, Hermes may build the dashboard frontend; keep `npm` available or prebuild it using the official [dashboard guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard).

## 2. Create a dashboard username and password

Tailscale restricts network access; it **does not replace** dashboard login. Use Hermes' bundled `dashboard_auth/basic` password provider. Pick a unique strong password; **do not use example values as your actual credentials**.

```bash
hermes plugins enable dashboard_auth/basic
hermes config set dashboard.basic_auth.username myadmin
# Run the remaining commands on the Hermes host in zsh (macOS default).
# Prompts locally, without placing the password in shell history or process arguments:
read -s 'DASHBOARD_PASSWORD?New dashboard password: '; printf '\n'
export DASHBOARD_PASSWORD
HASH=$(python3 -c 'import os,hashlib,secrets,base64; p=os.environ["DASHBOARD_PASSWORD"].encode(); s=secrets.token_bytes(16); k=hashlib.scrypt(p,salt=s,n=16384,r=8,p=1,dklen=32); print("scrypt$16384$8$1$"+base64.b64encode(s).decode()+"$"+base64.b64encode(k).decode())')
unset DASHBOARD_PASSWORD
hermes config set dashboard.basic_auth.password_hash "$HASH"
unset HASH
# Optional: stable token signing across dashboard restarts:
hermes config set dashboard.basic_auth.secret "$(openssl rand -base64 32)"
```

For bash, replace `read -s 'DASHBOARD_PASSWORD?New dashboard password: '` with `read -rsp 'New dashboard password: ' DASHBOARD_PASSWORD`. Never commit `~/.hermes/config.yaml`, `.env`, a password hash, or a signing secret. If Basic Auth is already configured, **do not overwrite it** unless you intend to rotate the password. Refer to [Hermes dashboard authentication](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard#usernamepassword-provider-no-oauth-idp).

## 3. Install and allow your Telegram ID

Find your numeric Telegram user ID via `/whoami` in your Hermes bot DM (not the bot ID or chat name). Allow only the account(s) that may control the dashboard:

```bash
hermes plugins install YOUR_GITHUB_USER/hermes-dashboard-tailnet --enable
hermes config set --force plugins.entries.dashboard-tailnet.settings.admin_ids '["YOUR_NUMERIC_TELEGRAM_USER_ID"]'
```

Replace `YOUR_GITHUB_USER` with the repository owner (see repo URL). JSON brackets are intentional: `admin_ids` must be a list of strings. **Fail closed:** without IDs, nobody can control it. If your Hermes gateway has separate slash-command gating, add the same ID to `platforms.telegram.extra.allow_admin_from` or permit these commands for that user. `allow_from` (who may chat) is **not** an admin grant. The commands only work in a Telegram DM, never a group or a different platform. If your gateway does not load the new plugin immediately, follow the official [plugin reload instructions](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins). Some installations require an operator-triggered gateway restart.

## 4. Use

Send `/db on` in your Hermes bot DM. The reply supplies your actual URL, like `https://your-node.your-tailnet.ts.net:8443`. On a device connected to the same tailnet, open the URL and sign in with the username and password from step 2. Use `/db status` to check, `/db off` to stop the dashboard and its Serve route. `/dash` and `/dashboard` are equivalent. Sending `/db` without arguments shows usage; Telegram's menu may omit the command if it exceeds the menu cap, but typing it still works.

To pin it in the Telegram bot menu, configure `platforms.telegram.extra.command_menu.priority: [db]` in `~/.hermes/config.yaml`, then apply the menu through the normal Telegram adapter lifecycle. Do not use `tailscale funnel`: Funnel publishes to the internet, whereas Serve stays in the tailnet.

## Troubleshooting / safety

- `Denied: configure admin_ids`: verify your numeric ID in `plugins.entries.dashboard-tailnet.settings.admin_ids`, plus DM context. Use `/whoami` to check gateway admin gating.
- `Tailscale belum terhubung`: sign into Tailscale on the Hermes host; check `tailscale status` and HTTPS/MagicDNS setup.
- `endpoint HTTPS :8443 sudah dipakai`: check `tailscale serve status --json`; leave other services alone. No `tailscale serve reset`—that would erase unrelated routes.
- `Dashboard gagal menyala`: check `~/.hermes/logs/dashboard-tailnet.log`, Hermes dashboard dependencies, and port `9119`.
- Login missing/denied: check `dashboard.basic_auth.username`, `password_hash`, `hermes plugins list`, and `dashboard.public_url` in config. Plugin sets `public_url` to its actual tailnet URL before launching Hermes, so reverse-proxied loopback traffic still requires login. Validate unauthenticated access returns `401` before trusting the deployment. Never use a weak or shared password. Basic Auth has no MFA; use only a trusted tailnet.
- `off` refuses to turn off a route it did not create; don't reset Tailscale Serve globally. If plugin state is lost, inspect processes and Serve routes manually before stopping anything.

Sources: [Hermes dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard), [Hermes plugins](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins), [Tailscale Serve CLI](https://tailscale.com/docs/reference/tailscale-cli/serve).
