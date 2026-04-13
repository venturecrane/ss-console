# Booking Cutover Test Plan

Checklist for verifying the first-party booking system before removing the
Calendly flow. Each section is a gate: all items must pass before proceeding
to the next section. If any check fails, stop and consult the rollback steps
at the bottom.

Issue: #226

---

## 1. Pre-cutover checks

These are infrastructure prerequisites. Nothing works until they pass.

- [ ] Migration 0011 applied to production D1 (#218)
  - Verify with `migrations/0011_verify.sql` — all queries must return
    expected row counts and column names
  - Tables: `assessments` (with `cancelled` status), `assessment_schedule`,
    `booking_holds`, `availability_blocks`, `integrations`, `oauth_states`
  - Index: `uniq_assessments_scheduled_at_active` partial unique index exists
- [ ] Environment variables set in Cloudflare Pages production
  - `GOOGLE_CLIENT_ID` — Google Cloud OAuth 2.0 client ID
  - `GOOGLE_CLIENT_SECRET` — Google Cloud OAuth 2.0 client secret
  - `BOOKING_ENCRYPTION_KEY` — 32-byte base64 key for AES-GCM encryption of
    refresh tokens (`openssl rand -base64 32`)
  - `PUBLIC_TURNSTILE_SITE_KEY` — Cloudflare Turnstile site key (public)
  - `TURNSTILE_SECRET_KEY` — Cloudflare Turnstile secret key (server-side)
  - `BOOKING_CACHE` KV namespace bound in `wrangler.toml` and production
- [ ] Google OAuth connected via admin UI
  - Admin navigates to integration settings, clicks "Connect Google Calendar"
  - OAuth consent screen completes, callback stores encrypted refresh token
    in `integrations` table
  - Verify: `SELECT status FROM integrations WHERE provider = 'google_calendar'`
    returns `active`
- [ ] Turnstile widget configured
  - Site key added to Cloudflare Turnstile dashboard for `smd.services` domain
  - Widget renders on the new `/book` page without console errors
- [ ] `BOOKING_CACHE` KV namespace exists and is bound
  - Verify the namespace is listed in `wrangler kv:namespace list`

## 2. Functional tests — booking flow

Walk through the full booking flow end-to-end. Use a real browser, not curl,
to exercise Turnstile and client-side JS.

### 2a. Slot loading

- [ ] `GET /api/booking/slots?tz=America/Phoenix` returns 200 with day-grouped
      slots
- [ ] Slots respect the weekly schedule: Mon-Fri 9:00-16:00 Phoenix, no
      weekends
- [ ] Slots respect 24-hour minimum notice (no slots in the next 24 hours)
- [ ] Slots respect 14-day max lookahead (no slots beyond 14 days out)
- [ ] Buffer time (15 min) is enforced: if a Google Calendar event ends at
      10:00, the 10:00 slot is unavailable
- [ ] Existing booked assessments (`status = 'scheduled'`) block their slots
- [ ] Manual `availability_blocks` rows block their time ranges

### 2b. Reserve flow

- [ ] Select a slot, fill in name + email + business name, submit
- [ ] Turnstile challenge completes without user interaction (invisible mode)
- [ ] Response returns 201 with `manage_url`
- [ ] `booking_holds` row created with 5-minute TTL, then deleted after commit
- [ ] `assessments` row created with `status = 'scheduled'`,
      `scheduled_at` = selected slot
- [ ] `assessment_schedule` sidecar row created with:
  - `slot_start_utc` / `slot_end_utc` matching the selected slot
  - `guest_name`, `guest_email` populated
  - `manage_token_hash` populated (SHA-256 hex)
  - `manage_token_expires_at` = slot end + 48 hours
  - `google_sync_state` transitions from `pending` to `synced`
- [ ] Entity created or matched via `findOrCreateEntity` (dedup by business
      name slug)
- [ ] Contact created (or skipped if email already exists)
- [ ] Context entry appended with `type = 'intake'` and intake metadata

### 2c. Confirmation email

- [ ] Guest receives confirmation email at the submitted address
- [ ] Email contains: slot date/time, Google Meet link (if created),
      manage booking link
- [ ] ICS attachment is present, opens in calendar apps, shows correct time
- [ ] ICS UID follows pattern `booking-{schedule_id}@smd.services`
- [ ] Admin notification sent to `team@smd.services` with intake details

### 2d. Double-booking prevention

- [ ] Attempting to reserve the same slot concurrently returns 409 for the
      second request
- [ ] The `booking_holds` UNIQUE constraint blocks concurrent INSERTs
- [ ] The `uniq_assessments_scheduled_at_active` partial index prevents
      duplicate scheduled assessments at the same time
- [ ] An expired hold does NOT block a new reservation (upsert-when-expired
      pattern)

### 2e. Reschedule

- [ ] Guest visits manage URL, selects a new slot, confirms reschedule
- [ ] Old Google Calendar event is updated (not duplicated)
- [ ] `assessment_schedule.reschedule_count` increments
- [ ] `assessment_schedule.previous_slot_utc` records the old slot
- [ ] ICS attachment on reschedule email has incremented SEQUENCE
- [ ] Old slot becomes available again

### 2f. Cancel

- [ ] Guest visits manage URL, clicks cancel, confirms
- [ ] Assessment status transitions to `cancelled`
- [ ] `assessment_schedule.cancelled_at` and `cancelled_by = 'guest'` set
- [ ] Google Calendar event is cancelled (METHOD=CANCEL ICS)
- [ ] Cancelled slot becomes available for new bookings
- [ ] Manage token still works for viewing (read-only) after cancellation

## 3. Google Calendar sync verification

- [ ] After a successful reserve, a Google Calendar event appears on the
      consultant's calendar
- [ ] Event title matches `BOOKING_CONFIG.meeting_label`
- [ ] Event includes Google Meet link (auto-created via conferenceData)
- [ ] Event attendee is the guest email
- [ ] `assessment_schedule.google_event_id` is populated
- [ ] `assessment_schedule.google_event_link` is populated
- [ ] `assessment_schedule.google_meet_url` is populated
- [ ] `assessment_schedule.google_sync_state = 'synced'`
- [ ] On reschedule: existing event is PATCHED with new time, not duplicated
- [ ] On cancel: event is deleted or marked cancelled on Google Calendar

## 4. Error scenarios

### 4a. Google Calendar unavailable

- [ ] If Google API returns 5xx during slot fetch: `/api/booking/slots`
      returns 503 with a user-facing message (not a raw stack trace)
- [ ] If Google API returns 5xx during reserve: booking still succeeds
      locally, `google_sync_state = 'error'`, `google_last_error` populated
- [ ] Retry logic: the system retries once on transient Google failures
      (`google_call_retries = 1` in config)

### 4b. Rate limiting

- [ ] 11th booking attempt from the same IP within one hour returns 429
- [ ] Rate limit counter resets after the 1-hour window
- [ ] Rate limiting only applies to `/api/booking/reserve`, not
      `/api/booking/slots`
- [ ] When `BOOKING_CACHE` KV is not bound (dev), rate limiting is skipped

### 4c. Expired holds

- [ ] A hold that expires (5 minutes) does not permanently block the slot
- [ ] The upsert-when-expired pattern allows a new reservation to claim a
      slot with an expired hold
- [ ] Daily cleanup cron (`workers/booking-cleanup/`) removes holds expired
      for >1 hour

### 4d. Turnstile failure

- [ ] Missing Turnstile token returns 400 (in production with secret set)
- [ ] Invalid/expired Turnstile token returns 400 with error message
- [ ] When `TURNSTILE_SECRET_KEY` is not set (dev), Turnstile is skipped

### 4e. Manage token

- [ ] Invalid manage token returns 404 (no information leakage)
- [ ] Expired manage token (>48h after slot end) returns 410 Gone
- [ ] Manage token cannot be used to access a different booking

## 5. Rollback steps

If the new booking system has a critical failure after cutover:

1. **Revert `/book` page** — re-deploy with the Calendly widget page:
   `git revert <commit-that-replaced-book.astro>` and push
2. **Keep `/book/thanks`** — the legacy intake form at
   `src/pages/book/thanks.astro` is independent and can continue
   accepting intake submissions
3. **Remove middleware redirect** — the 301 redirect from `/book/thanks`
   to `/book` (in `src/middleware.ts`) should be removed during rollback
   so the thanks page is directly accessible again
4. **Google OAuth** — no rollback needed; the integration row stays in D1
   and is harmless when unused
5. **Data** — any bookings made through the new system remain in
   `assessments` + `assessment_schedule`. They are valid data and should
   not be deleted. The assessment was created; only the booking method
   changes on rollback.

---

## 6. Post-cutover cleanup

After the new booking system is verified in production and stable for at
least one month, the following legacy code should be removed. **Do not
delete these during cutover** — keep them as a rollback safety net.

### Files to delete

| File                              | Description               | Notes                                                                                             |
| --------------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------- |
| `src/pages/book.astro`            | Calendly widget page      | Currently prerendered; replaced by new `/book` with SlotPicker                                    |
| `src/pages/book/thanks.astro`     | Post-Calendly intake form | Intake is folded into the new reserve flow                                                        |
| `src/pages/api/booking/intake.ts` | Intake form POST handler  | Replaced by `/api/booking/reserve` which handles entity creation, contact, and intake in one step |

### Code to remove

| Location            | Lines | Description                 | Notes                                                                   |
| ------------------- | ----- | --------------------------- | ----------------------------------------------------------------------- |
| `src/middleware.ts` | 35-41 | `/book/thanks` 301 redirect | Comment says "Remove this redirect one month after the booking cutover" |

### Comments to clean up

| Location                     | Line | Content                                                                     | Action                                      |
| ---------------------------- | ---- | --------------------------------------------------------------------------- | ------------------------------------------- |
| `src/lib/email/templates.ts` | 282  | `"Booking emails (Calendly replacement"`                                    | Remove "Calendly replacement" parenthetical |
| `src/lib/email/templates.ts` | 380  | `"legacy intake notification (which was tied to the Calendly+intake flow)"` | Remove legacy reference                     |
| `src/env.d.ts`               | 46   | `"Booking system (Calendly replacement) — added with migration 0011"`       | Remove "Calendly replacement" parenthetical |

### Code to keep (not legacy)

| Location                           | Line | Description                               | Reason                                                                                    |
| ---------------------------------- | ---- | ----------------------------------------- | ----------------------------------------------------------------------------------------- |
| `src/lib/enrichment/tech-stack.ts` | 27   | Calendly in tech stack detection patterns | Detects Calendly on **client** websites during enrichment — unrelated to our booking flow |
