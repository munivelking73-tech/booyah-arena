# BOOYAH Arena — Razorpay payment gateway setup

This version replaces the old manual UPI-ID + transaction-ID deposit flow with a Razorpay Payment Link flow.

## New player flow

1. Player enters an amount of at least ₹10.
2. BOOYAH Arena creates a unique Razorpay Payment Link.
3. Player completes UPI/payment on the secure gateway page.
4. Razorpay sends `payment_link.paid` to `/api/webhooks/razorpay`.
5. The server verifies the webhook signature and exact amount.
6. The deposit is marked `Paid` and wallet points are credited automatically.
7. Duplicate webhook deliveries do not credit the player twice.

## Render environment variables

Set these in Render → Environment:

- `PAYMENT_PROVIDER=razorpay`
- `RAZORPAY_KEY_ID` = your Razorpay live/test key ID
- `RAZORPAY_KEY_SECRET` = your Razorpay secret
- `RAZORPAY_WEBHOOK_SECRET` = a webhook secret you create
- `PUBLIC_BASE_URL` = your public HTTPS BOOYAH Arena URL

Do not put `RAZORPAY_KEY_SECRET` or `RAZORPAY_WEBHOOK_SECRET` in HTML, JavaScript, GitHub, or the Android app.

## Razorpay webhook

Create a webhook in the Razorpay Dashboard pointing to:

`https://YOUR-BOOYAH-DOMAIN/api/webhooks/razorpay`

Use the same value you entered as `RAZORPAY_WEBHOOK_SECRET`.

Enable at least:

- `payment_link.paid`
- `payment_link.cancelled`
- `payment_link.expired`

The endpoint uses the raw request body and `X-Razorpay-Signature` for verification.

## Important

The existing `BOOYAH_UPI_ID` and `BOOYAH_UPI_NAME` variables are retained for compatibility, but the new player deposit screen uses Razorpay when the gateway is configured.

Use Razorpay test credentials first. Switch to live credentials only after the test flow, webhook delivery, and wallet crediting have been verified.
