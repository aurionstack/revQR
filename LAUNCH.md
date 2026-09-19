# RevQR launch checklist

The application changes do not by themselves verify external payment, mail,
courier, monitoring or backup services. New checkout is closed by default.

## Operator details and policies

Aurion Stack is a trading name, not a claim of company registration. Contact:
support@revqr.tech, +91 9322974288, Mapusa, Goa 403510, India.
Confirm the operator's full postal address and named grievance contact; review
the privacy, terms, refunds and shipping pages with a qualified adviser.
Confirm tax obligations before issuing tax invoices. Current receipts explicitly
are not GST invoices. Set POLICIES_APPROVED only after this review.

Draft commercial terms: INR 1,599/year; INR 2,499/two years (INR 699 saving
against two annual purchases); INR 249 per physical stand, shipping included.
First subscription purchases have a seven-calendar-day refund request window.
Defective/incorrect stands have replacement/refund support; statutory rights
are not limited. Approximate delivery is seven business days, not guaranteed.
Validate production, packing, courier, tax and gateway costs before enabling
PHYSICAL_STANDS_ENABLED. No automatic renewal charges are made.

## Payments

1. Configure live Razorpay credentials and a separate random webhook secret.
   Never reuse an API secret for webhook authentication.
2. In Razorpay configure https://revqr.tech/billing/webhook with payment.captured,
   order.paid, payment.failed, refund.processed and payment.dispute.* events.
   Verify an actual signed delivery and its event in /admin/operations.
3. Verify test-mode checkout, abandoned browser callback, duplicate events,
   wrong signature, refunds and disputes before a controlled live purchase.
   Refunds/disputes flag operator review; they do not automatically revoke
   entitlement. Reconcile entitlements and refunds before resolving the flag.
4. Set PUBLIC_CHECKOUT_ENABLED only after checks pass. Existing subscriber
   access does not depend on this gate. Never use a real charge in CI.
5. Configure an hourly job: `python -m app.maintenance`. It reconciles up to
   60 recent orders, queues renewal reminders and sends pending receipts.
   It does not charge or refund. SMTP delivery is at least once; duplicates
   are possible after a crash. Five failed attempts need operator attention.

## Backups and restore

With the authenticated Heroku CLI, capture before deploying schema changes:

```powershell
heroku pg:backups:capture --app revqr
heroku pg:backups --app revqr
heroku pg:backups:schedule DATABASE_URL --at '02:00 Asia/Kolkata' --app revqr
heroku pg:backups:schedules --app revqr
```

Check plan support, retention and capture completion; do not assume a scheduled
backup worked. Download privately and restore into a separate, disposable local
database, never DATABASE_URL or the production database. Use pg_restore with
--no-owner --no-acl --exit-on-error. Compare business/payment counts and verify
stored logos after restore. Record evidence of restore time and database
consistency. Database backups contain customer data: encrypt/restrict access,
set retention and do not commit them. Retain a known-good app release; rolling
back application code does not roll back a database migration safely.

## Monitoring and limits

- Configure private shared Redis storage via RATE_LIMIT_STORAGE_URI (TLS in
  production). Default memory IP limits apply per process, not across dynos.
- Database business/scan quotas and global provider-attempt/token reservations
  work across workers. Tokens are conservatively estimated, not an actual
  billing measurement. Also configure Gemini project budgets and alerts.
- Configure SENTRY_DSN and confirm a sanitized test error reaches the service.
  Add an independent uptime monitor for /health/ready and mail delivery alerts.
- Monitor /admin/operations for refund/dispute flags, notification failures,
  audit activity and webhook processing. Configuration flags are not service
  health checks. An empty webhook list is not proof of successful setup.
- Rotate previously exposed Gemini keys at Google; replace/revoke old keys.
  Keep all credentials in environment configuration, not source or screenshots.
- Test support@revqr.tech inbound and outbound mail, SPF/DKIM/DMARC, password
  recovery and administrator second-factor delivery on real devices.

## Verification and release

Use a disposable PostgreSQL database for tests (fixtures drop application tables).
Run `python -m pytest -q`, `python -m compileall -q app`, `git diff --check`,
and Alembic upgrade/downgrade/upgrade on an isolated database. Do not point tests
at production. Test signup/verification/recovery, QR scan/download, copy/paste,
manual replies, business redirect, checkout/recheck and shipment tracking on
Android/iOS and desktop. Clipboard access requires HTTPS/user interaction and
must retain manual copying as fallback. Google account selection is controlled
by Google; a business link cannot sign a user into its owner's account.

Only deploy after backup and tests, then check /health/ready, public pages and
an authenticated owner/admin workflow. Mark external checks complete with
evidence, not merely because environment settings are present.
