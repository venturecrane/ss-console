/**
 * Client-facing skill summaries - the one-line "what it does" shown per skill on
 * the Operator › Skills facet (ADR 0069 Slice 3; brief §5 follow-on, Captain call
 * 2026-07-08: names alone don't tell a client what a skill does).
 *
 * WHY A SEPARATE, HAND-AUTHORED CATALOG (not the SKILL.md `description`):
 * Each skill's `description` frontmatter is authored for the AGENT - Hermes reads
 * it to select skills - so it is uneven for a client list (some are 150-word
 * paragraphs; ~40% carry em dashes the house style bans on client surfaces). The
 * summaries below are compressed faithfully from those authored descriptions -
 * reviewed, client-legible, em-dash-free, each preserving the skill's key "never
 * does X" boundary. This is authored client copy (agents ARE the voice per
 * content-policy), NOT runtime paraphrase and NOT invented capability: every line
 * is a reduction of the real authored description, reviewed by Captain before
 * ship. Kept OUT of the SKILL.md files so it never touches the agent runtime
 * (which keys on `description`).
 *
 * Maintenance contract (guarded by tests/skill-summaries.test.ts): every skill
 * under operator/skills/ MUST have an entry here - a new skill cannot reach the
 * client surface without a reviewed client summary - and no summary may contain
 * an em dash. A skill configured on a persona but absent here renders name-only
 * (honest degradation), never a fabricated line.
 */

export const SKILL_SUMMARIES: Record<string, string> = {
  'ar-chaser': 'Drafts accounts-receivable follow-ups from QuickBooks for your review.',
  'assessment-findings-draft':
    'Drafts evidence-bound findings from an assessment interview transcript.',
  'asset-collection-follower':
    'Drafts the new-client onboarding checklist and chases the missing items.',
  'client-matter-digest':
    'Drafts a per-matter status update for a client, in your voice. Reports status, never advises.',
  'client-verification-tracker':
    'Tracks discovery-response verifications and chases the signer until signed. Never signs or sends without attorney approval.',
  'conflict-intake-router':
    'Captures conflict checks and routes them to the person who must clear them. Surfaces conflicts, never clears them.',
  'connector-auth-check':
    'Verifies daily that the connection to your practice management system is alive, so a broken connection is caught before it interrupts work. Checks the connection only; reads and writes no matter data.',
  'consult-scheduler':
    'Offers consult times within your rules and drafts the confirmation for a human to send.',
  'daily-needs-you-digest':
    'One batched daily digest of what across your matters genuinely needs attention. Never acts, never manufactures urgency.',
  'deadline-and-sol-tracker':
    'Surfaces your court dates, filing deadlines, and statute-of-limitations dates by urgency. Reflects dates you entered, never computes them.',
  'deadline-miss-escalator':
    'Escalates an approaching or missed deadline up a ladder so it never slips silently. Internal only.',
  'demand-letter-drafter':
    'Drafts a policy-limits demand letter from the matter record when your attorney asks. The demand figure and every settlement decision stay with the attorney; it never sends to a carrier.',
  'discovery-response-drafter':
    'Drafts responses to served discovery from the matter record when your attorney asks, with objections labeled as candidates. Never serves, never signs a verification; the attorney finalizes.',
  'discovery-response-staging':
    'Stages a served discovery request and its documents so a response can be drafted, then files the finished draft for attorney review. Never drafts the response.',
  'discovery-response-tracker':
    'Tracks California discovery response deadlines in both directions and brings the decision to your attorney. Never computes a deadline as final.',
  'discovery-served-watch':
    'Spots served discovery, reads the service date, and surfaces the deadline input for attorney confirmation. Never computes the deadline.',
  'document-library-establishment':
    'Surveys the documents your firm writes and proposes a library of templates, then builds only what you bless. Admin only. Case detail stays a visible marker, never filled in.',
  'document-receipt-logger':
    'Logs an inbound document against the right matter and drafts a receipt entry. Records arrival, never interprets contents.',
  'email-reply':
    'Handles inbound email from allow-listed senders and drafts replies in your voice.',
  'engagement-letter-chaser':
    "Tracks an unsigned engagement letter and drafts nudges until it's signed. Never interprets the terms.",
  'follow-up-discovery-drafter':
    "On your attorney's request, drafts the next round of written discovery aimed at what the record leaves unestablished, plus a short plan. Targeting decisions stay with the attorney; it never serves.",
  'inbox-triage': 'Triages your inbox daily and drafts categorized replies for your review.',
  'intake-to-system-sync':
    'Syncs a converted lead from your intake CRM into Smokeball, with dedupe and conflict checks.',
  'lien-ledger-tracker':
    'Tracks every provider balance blocking disbursement and chases the open ones, one contact per provider. Never computes a reduction or moves money.',
  'matter-document-review':
    "Reads a matter's documents and surfaces highlights, timelines, and gaps for an attorney. Never drafts legal work product.",
  'matter-inbox-router':
    'Routes inbound matter mail to the skill that handles it and replies to colleagues. Never decides legal substance.',
  'matter-initiation-setup':
    'Sets up a new matter at day one: folders, standard tasks, and the deadlines in view for attorney confirmation. Never computes a final SOL, never files.',
  'matter-memo-on-update':
    'Logs a short factual internal memo whenever a matter changes in Smokeball. Passive supervision, never analysis.',
  'matter-status-digest':
    'Assembles a periodic internal digest of your matters from Smokeball. Reports state, never decides next steps.',
  'matter-status-responder':
    'Answers a client\'s routine "where are we" with a factual status from the system of record. Status only, no opinion.',
  'mediation-brief-drafter':
    'Drafts a confidential mediation brief from the matter record against your skeleton when your attorney asks. Valuation and negotiation stay with the attorney; it never submits to a mediator.',
  'mediation-settlement-tracker':
    'Assembles the input packet for a mediation or settlement brief and tracks settlement deadlines. Never writes the brief, never asserts a deadline as final.',
  'medical-chronology-maintainer':
    'Keeps a cited medical chronology current on a PI matter, and turns an administrator request into a chronology package built on the seat. Extractive only, never characterizes causation.',
  'medical-records-chaser':
    "Watches for the plaintiff's medical records to land and chases outstanding providers. Never diagnoses, never drafts a demand.",
  'meet-and-confer-drafter':
    'Drafts a meet-and-confer letter, for internal review, about discovery deficiencies your attorney flagged. Never sends to opposing counsel.',
  'minors-compromise-packet':
    "Fills the minor's-compromise court forms from authored figures for your attorney to finalize and file. Never computes amounts, never advises.",
  'motion-calendar-tracker':
    "Keeps each matter's motion calendar current from Smokeball. Reads and organizes, never computes a deadline or files a motion.",
  'motion-package-assembler':
    'Assembles and stages a law-and-motion filing package from components already drafted in the matter. Never drafts the motion, never reserves a hearing date.',
  'new-matter-intake':
    'Turns a new-client inquiry into a structured matter draft and a non-committal acknowledgment, after a read-only conflict check.',
  'operator-introduce':
    'Introduces itself on request: connections, matter count, voice-establishment status per kind of writing, and every routine with its schedule and on/off state. States only what it observed or read.',
  'operator-self-initiation':
    'Sets itself up for your firm on an admin request: self-test, learning your writing voice, and building your document library, pausing for your blessing at each step. Creates nothing unblessed.',
  'operator-self-test':
    'Runs a one-page self-check on request: connections, a counted read, document output, and a live demonstration that it refuses identifiers it has not read. Reports to the requester only.',
  'opposing-response-deficiency-review':
    "Reads the opposing side's discovery responses and surfaces candidate gaps for an attorney. An assist, never a legal finding.",
  'paid-media-anomaly-watcher': 'Scans paid-media accounts daily for anomalies and alerts you.',
  'proposal-drafter': 'Drafts a proposal from a meeting transcript and your SOW templates.',
  'referral-source-acknowledgment':
    'Drafts a courtesy thank-you to whoever referred a new matter. Never discloses client identity. Drafted for review, never auto-sent.',
  'retainer-hours-reconciler': 'Reconciles tracked hours against retainer caps for your review.',
  'scope-creep-flagger': 'Flags out-of-scope requests in Slack or email for your reply.',
  'separate-statement-assembler':
    'Assembles the Rule 3.1345 separate statement for a motion to compel by collating requests and responses. Staged for your attorney, authors no argument.',
  'service-confirmation-watcher':
    'Watches for the proof of service to sync in, reads the served date, and surfaces the responsive-pleading deadline for confirmation. Never computes it.',
  'settlement-statement-feeder':
    'Assembles the settlement-statement and disbursement figures when a case settles, for a person to execute in Smokeball. Never moves trust money.',
  'shape-establishment':
    'Establishes how a kind of output is shaped, from examples you point it at. Admin only. Installs only rules it can show you in plain words.',
  'stalled-matter-nudge':
    'Surfaces matters with no recent activity and drafts a neutral follow-up. Flags inactivity, never decides what a matter needs.',
  'status-report-assembler':
    'Assembles a weekly client status report from your PM tools and analytics.',
  'trial-binder-assembler':
    'Assembles the trial binder from authored components and tracks pre-trial deadlines. Organizes and stages, never authors or argues.',
  'trust-balance-nudge':
    'Watches your IOLTA trust balance and drafts a top-up request. Read-only on funds, never moves money.',
  'voice-establishment':
    "Establishes your firm's writing voice from documents you point it at, and names which of your own documents break the rules it derived. Admin only. Keeps none of your prose.",
  workspace: 'Reads and writes Google Workspace through trust-classified tools.',
}
