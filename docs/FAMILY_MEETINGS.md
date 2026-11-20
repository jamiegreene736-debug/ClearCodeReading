# Family meetings

The teacher opens **Teaching → Family meetings**, chooses a reader and linked parent, enters the meeting time in Eastern Time, and adds a room, street address, or video link. Parents open **My child → Family meetings**. Administrators open **Program → Family meetings**. The schedule lists upcoming meetings and retains past and cancelled meetings in history.

## Records and permissions

A family meeting belongs to one teacher and one guardian–reader relationship. It uses the shared portal accounts and is independent of instructional sessions and external scheduler approvals. Teachers can schedule only for current assignments. Guardians see only their own invitations while their relationship is eligible. School admins see their own schools. On a school domain the schedule is further limited to that school. Edits retain the original participants. To change the parent, cancel and create a new invitation.

Times are stored as instants and shown in Eastern Time with the daylight-saving abbreviation. Save rejects past starts, reversed ranges, ambiguous local times, and overlapping family meetings for the teacher or invited parent. Participant locks serialize competing bookings. A revision number prevents an older browser tab from overwriting a later change.

## Email lifecycle

The same database transaction saves the meeting and its notices. New meetings queue an invitation immediately. Each revision supersedes pending notices and queues an update or cancellation. A meeting scheduled more than 24 hours ahead gets one reminder due 24 hours before the start. A shorter-notice booking gets the invitation or update. A worker catching up after an outage skips obsolete notices and reminders for meetings that have started.

The existing public-schema CRM email worker drains the private meeting outbox using the program's connected invitation mailbox. It rechecks recipients before sending. No CRM contact is required. The sender uses a stable Message-ID, retries definite temporary failures with backoff, and looks in Gmail Sent after an ambiguous result or interrupted send. After repeated unsuccessful reconciliation the notice is flagged for review. Staff can see the delivery state on the schedule. The worker, connected program mailbox and Gmail configuration must be available for delivery. A queued notice is not proof of mailbox acceptance or inbox delivery.
