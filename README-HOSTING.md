# BOOYAH ARENA — Render + MongoDB Atlas deployment

## 1. Create MongoDB Atlas database
Create a MongoDB Atlas cluster, database user, and allow your application to connect. Copy the Atlas SRV connection string.

Set:
- `MONGO_URI` = your Atlas SRV connection string
- `MONGO_DB` = `booyah_arena`

Do not commit the real MongoDB URI, database password, or admin password to GitHub.

## 2. Deploy to Render
Create a new Render Web Service from this project/repository. Render can use `render.yaml` automatically.

Build command:
`pip install -r requirements.txt`

Start command:
`python server.py`

Health check:
`/api/health`

## 3. Render environment variables
Add these in Render → Environment:

- `MONGO_URI` — MongoDB Atlas SRV URI (Secret)
- `MONGO_DB` — `booyah_arena`
- `BOOYAH_ADMIN_USER` — your production admin username (Secret)
- `BOOYAH_ADMIN_PASS` — your production admin password (Secret)
- `BOOYAH_UPI_ID` — `9940879866@nyes`
- `BOOYAH_UPI_NAME` — `BOOYAH ARENA`

Render automatically provides `PORT`; the server binds to `0.0.0.0`.

## 4. Important
The real values for `MONGO_URI`, `BOOYAH_ADMIN_USER`, and `BOOYAH_ADMIN_PASS` are intentionally NOT included in this ZIP. Enter them as Render environment variables.


## 5. Use ffgamers.com as the production domain
The Render Blueprint is preconfigured with `ffgamers.com` as the custom domain and disables the default `onrender.com` address after the custom domain is active. Render automatically adds the corresponding `www` domain and redirects it to the root domain.

After the Render service is created:

1. Open **Render → your `booyah-arena` service → Settings → Custom Domains**.
2. Confirm `ffgamers.com` is listed.
3. In the DNS provider where `ffgamers.com` is registered, point the domain to Render.
   - For a root-domain A-record setup, Render currently documents `216.24.57.1` as the load-balancer IP.
   - For `www`, use a CNAME pointing to your Render service hostname, such as `booyah-arena.onrender.com`.
   - Remove conflicting `AAAA` records while configuring the domain.
4. Return to Render and click **Verify**.
5. Render provisions and renews HTTPS automatically after verification.

**Important:** the exact DNS records depend on your domain registrar/DNS provider. Do not change nameservers unless you intend to move DNS management.

Production URL: `https://ffgamers.com`


Customer Support (WhatsApp): https://chat.whatsapp.com/CsmvEFNvAFI5DjpwfXob0f


## Player verification Excel
The Admin page now includes:
- **Export selected Excel** for the currently selected match slot.
- **Export upcoming Excel** for the next upcoming slots that have joined players.
The workbook lists player name, Free Fire UID, username, email, squad, entry fee and match status, with blank YES/NO columns for **UID verified**, **Name verified**, and **Entered match**.
