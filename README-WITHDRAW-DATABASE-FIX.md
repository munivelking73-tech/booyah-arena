# Withdrawal database/frontend fix

This build fixes the legacy `Destination required` mismatch. The player sends both structured `details` and a `destination` compatibility field. The backend validates and stores the structured payment details in MongoDB.

UPI: `payment_details.upi_id`
Bank: `payment_details.account_number`, `ifsc_code`, `branch`, `district`, `pincode`

Wallet points are reserved atomically when a request is created. Admin approval marks it Completed. Admin rejection marks it Rejected and refunds the reserved amount exactly once.

After deploying, restart/redeploy the Render service so the new `server.py` is running. Existing withdrawal records are preserved.
