# Booking System Cutover Test Plan

Cutover from Calendly to first-party booking system (Epic #212).

## System State

- **Old flow:** Calendly widget → `/book/thanks` intake form → `POST /api/booking/intake`
- **New flow:** Custom slot picker on `/book` → `POST /api/booking/reserve` → inline confirmation
- **Migration:** 0011 applied to production D1
- **Legacy redirect:** `/book/thanks` → 301 → `/get-started?booked=1`

## Pre-QA Cleanup

- [ ] Delete any pre-swap test events from Google Calendar (events with Google Meet conference data from before the Zoom switch)
- [ ] Verify `MEETING_URL` env var is set in Cloudflare Pages

## Happy Path — Public Booking

- [ ] Open `/book` in incognito
- [ ] Slot picker loads with available slots
- [ ] Select a slot — highlighted state visible
- [ ] Fill intake form (name, email, business name, vertical, challenge)
- [ ] Turnstile widget solves
- [ ] Submit — confirmation panel shows booked time + video call link
- [ ] Guest receives confirmation email with ICS attachment
- [ ] ICS imports cleanly into calendar app
- [ ] Scott's Google Calendar shows the event
- [ ] D1 shows: assessment (status=scheduled, scheduled_at set), schedule sidecar (google_sync_state=synced), entity (stage=assessing), contact row

## Reschedule Flow

- [ ] Click "Manage your booking" link from confirmation email
- [ ] Manage page shows current booking details
- [ ] Click Reschedule → slot picker loads → pick different slot → submit
- [ ] Reschedule email arrives with new time
- [ ] Google Calendar event updated (new time, SEQUENCE bumped)
- [ ] Original manage URL still works after reschedule
- [ ] D1: `previous_slot_utc` set, `reschedule_count` incremented, `manage_token_hash` unchanged

## Cancel Flow

- [ ] Click "Cancel" on manage page → confirmation → cancelled
- [ ] Cancellation email arrives with rebook link
- [ ] Google Calendar event deleted
- [ ] D1: `assessments.status='cancelled'`, `scheduled_at=NULL`, sidecar `cancelled_at` and `cancelled_by='guest'` set
- [ ] Original manage URL shows "cancelled" state

## Double-Booking Prevention

- [ ] Open two browser windows, both on `/book`, both select same slot
- [ ] Submit simultaneously — one succeeds, one gets "slot just taken" with refreshed slots
- [ ] No duplicate assessment rows for that slot

## Legacy Redirect

- [ ] Visit `/book/thanks` — 301 redirect to `/get-started?booked=1`
- [ ] Visit `/book/thanks/anything` — 301 redirect to `/get-started?booked=1`

## Admin Verification

- [ ] `/admin/assessments/[id]` renders scheduled assessment with correct badge and schedule details
- [ ] Video call link shows "Join Video Call" (not "Join Google Meet")
- [ ] Cancelled assessment renders with neutral slate badge (not "unknown")

## Cleanup Record

| Artifact                             | Action          | Reason                             |
| ------------------------------------ | --------------- | ---------------------------------- |
| `src/pages/book/thanks.astro`        | Deleted         | Replaced by unified `/book` page   |
| `/api/booking/intake.ts`             | Already removed | Replaced by `/api/booking/reserve` |
| Middleware redirect (`/book/thanks`) | Kept            | Handles legacy bookmarks           |
| `/get-started` intake form           | Kept            | Standalone intake still active     |
