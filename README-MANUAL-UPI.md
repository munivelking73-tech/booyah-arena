# BOOYAH ARENA — Manual UPI + Admin Approval

The online payment gateway is removed. Players can deposit by:
1. Entering the amount on the player dashboard.
2. Scanning the BOOYAH ARENA QR code, or sending the exact amount directly by UPI to **9940879866** if scanning does not work.
3. Entering the UTR / transaction ID and submitting the payment for review.

**Security rule:** A submitted UTR never credits points automatically. The admin must verify the UTR and the actual received amount in the bank/UPI account and then approve the deposit. Deposits of **₹200 or more are always manually verified**; this project uses manual verification for every deposit amount to prevent fake/reused UTRs from automatically creating points.

If a fake, edited, reused or invalid UTR is detected, the admin can reject the request and block the player account from the admin dashboard.

Player account creation requires Free Fire username, Free Fire UID, Gmail, phone number and password. Password visibility can be toggled with Show/Hide. The player-facing **Forgot password** option has been removed.

Render needs `MONGO_URI`, `MONGO_DB`, `BOOYAH_ADMIN_USER`, `BOOYAH_ADMIN_PASS`, `BOOYAH_UPI_PHONE`, and `BOOYAH_UPI_NAME`.
