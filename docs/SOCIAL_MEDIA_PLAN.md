# Marketing menu and social media

Marketing in the administrator portal has two sections: Social media, and
Settings for connecting Facebook and Instagram. This covers those two networks.
The behavior below is implemented in `apps/social`.

## Where it sits

Super administrators already see Dashboard, Free resources, Program, Manage, and CRM
in `templates/portal/_header.html`. Marketing is a new disclosure in that same row,
after Manage and before CRM. It uses the same panel pattern as Program and Manage.

The panel has two destinations:

- **Social media** — draft, schedule, and publish posts on the ClearCode Reading
  Facebook Page and Instagram account.
- **Settings** — sign in to connect Facebook, Instagram, or both.

The newsletter stays in the CRM. Settings is only the connection. Composing and the
queue stay on Social media.

School admins, teachers, parents, students, and CRM-only users do not see Marketing.
Posting is the public brand voice, so it stays with super administrators. A separate
permission can grant it to one marketer later without opening it to every center admin.

## The two ways to make a post

Both ways end in the same queue. A generated caption never publishes by itself.
Someone chooses Save draft, Schedule, or Post now after reading it.

### Create from a brief

The person writes a short brief: what the post is about, who it is for (families,
teachers, or general), a tone (warm, practical, or celebratory), an optional link,
and an optional photo. The portal drafts two captions from that brief:

- Facebook can be a little longer and can include the link.
- Instagram is shorter, can include a few hashtags, and needs the photo.

Both drafts stay editable. The same photo can be used on both networks.

### Write it yourself

The person writes the caption, picks Facebook, Instagram, or both, attaches a photo,
and chooses Post now or a date and time. Quick picks cover the usual send times
(tomorrow morning, later the same week, next Monday morning). Times are Eastern,
which is already the application timezone.

Instagram feed posts require a photo. Facebook can be text, a photo, or a link.
If both networks are selected, the captions can stay the same or be edited apart
before scheduling.

## Settings and one-click sign-in

Settings is a page of two cards, Facebook and Instagram. Each card has its own
sign-in button. The connection belongs to ClearCode Reading, not to the person who
clicked. Any super administrator can reconnect it later.

**Sign in with Facebook** is the one-click path for both networks. It opens
Facebook, the person approves publishing, and the portal returns to Settings. If
that person manages one Page, the portal selects it. If the Page already has a
professional Instagram account, that same return trip fills the Instagram card.
No second password is stored for Instagram in that case.

**Sign in with Instagram** is the one-click path for Instagram alone. Use it when
Facebook should stay disconnected, or when the Page has no linked Instagram
account. A personal Instagram login cannot publish, so the button only succeeds
for a professional account.

Disconnecting one card clears that network only. Scheduled posts that needed the
removed network move to Needs attention. The other network keeps publishing.
Reconnect uses the same button. Nothing is posted during sign-in.

If the person manages more than one Facebook Page, Settings asks which Page after
they return. That chooser is the only extra step, and it appears only in that case.

## Screens

1. **Marketing menu.** Social media and Settings.
2. **Settings, nothing connected.** Two cards, each with one sign-in button.
3. **Settings, Facebook only.** The Page is connected. Instagram still has its own
   sign-in button.
4. **Settings, both connected.** One Facebook sign-in filled both cards. Each card
   can reconnect or disconnect on its own.
5. **Queue and new post.** The queue links to Settings instead of hosting the
   connection itself. The composer only offers a network whose card says Connected.
6. **Scheduled.** Posts that have not gone out yet, soonest first, with the
   Eastern time and the networks. Each row has Change date and Cancel.
7. **Change the date.** A calendar and a time for that one post. The current
   date stays marked, the new date is selected, and saving updates only the
   send time.
8. **Cancel.** A confirmation names the post, the time, and both networks.
   Keeping it scheduled closes the confirmation. Canceling removes it from the
   schedule and leaves the caption in Drafts.
9. **Posted.** Posts that already went out, newest first, with the time they
   published and a link to the live Facebook or Instagram post. Those dates
   cannot be changed.

A line on the composer states the rule for this brand: do not include a child’s
name, photo, school, or reading scores.

## Scheduled and posted

Social media opens on **Scheduled**. **Posted** is the history. Drafts and Needs
attention stay as the other two tabs.

A scheduled row can change until the worker picks it up to send:

- **Change date** sets a new Eastern date and time. The caption, photo, and
  networks stay as they are. A time in the past is rejected. Saving returns to
  the Scheduled list with the new time, and the list re-sorts.
- **Cancel** asks first. Confirming removes the post from the schedule so it
  will not send. The caption is kept as a draft, which can be scheduled again.
  The Scheduled count drops by one.

Posted rows are a record. Each one shows when it went out and a link for every
network that succeeded. There is no Change date and no Cancel on a post that
already published. If Facebook succeeded and Instagram failed, the row stays in
Needs attention until the failed network is retried or dropped, and it appears
in Posted only for the network that actually published.

## What happens at send time

The portal keeps the schedule. It does not hand the clock to Facebook or Instagram,
so both networks share one queue and one cancel button.

A background worker, the same kind the app already uses, publishes a post when its
Eastern time arrives. Facebook gets a Page post. Instagram gets a single feed photo
with the caption. The photo has to be reachable by Instagram at that moment, so the
file stays with the post and is exposed only through a short-lived, unguessable
address while publishing.

Each network is recorded on its own. If Facebook succeeds and Instagram fails, the
post moves to Needs attention and only the failed network can be retried. After a
successful send, the row links to the live post. Editing the live post on Facebook
or Instagram is not part of this version.

Nothing in the queue sends because a blog post was published. A later addition can
offer “Draft a social post” from a blog article, and that draft still waits for a
person.

## Backend

This follows the Gmail mailbox connection in `apps/crm_email/google.py`: a short-lived
authorization row, an encrypted token, and a status the screen can show.

Routes, super administrators only:

- `GET /portal/marketing/settings/` shows the two cards.
- `POST /portal/marketing/settings/facebook/connect/` starts Facebook sign-in.
- `GET /portal/marketing/settings/facebook/callback/` finishes it.
- `POST /portal/marketing/settings/instagram/connect/` starts Instagram sign-in.
- `GET /portal/marketing/settings/instagram/callback/` finishes it.
- `POST /portal/marketing/settings/<network>/disconnect/` clears that network.
- `GET /portal/marketing/social/?tab=scheduled` lists posts still waiting, soonest first.
- `GET /portal/marketing/social/?tab=posted` lists posts that already published, newest first.
- `POST /portal/marketing/social/<id>/reschedule/` saves a new Eastern date and time.
- `POST /portal/marketing/social/<id>/cancel/` takes a scheduled post off the queue.

`SocialAuthorization` matches the email `Authorization` row. It stores a hashed
state, the session hash, the super administrator, an encrypted verifier, which
button was clicked, and a ten-minute expiry. The callback rejects a missing,
expired, or already-used state, and it rejects a cancelled sign-in. The state is
marked used before the token is exchanged.

`SocialAccount` is one row per network, so either card can be connected alone.
Fields: network (`facebook` or `instagram`), status (`disconnected`, `connected`,
`reconnect`), the Page or Instagram id, the display name (`ClearCode Reading` or
`@clearcodereading`), the encrypted token, when the token expires, who signed in,
when, the last check, and the last error. The Instagram row records whether it
came from the Facebook sign-in or from its own sign-in. Tokens are encrypted with
the same helper the CRM already uses. They are not written to logs, audit text, or
the page.

Facebook sign-in asks only for permission to list the Pages this person manages,
publish as the chosen Page, and read that Page’s linked Instagram account. On
return, one managed Page is selected automatically. Several Pages produce the
short chooser, then the Page token is exchanged and encrypted. The linked
Instagram account, when present, is saved on the Instagram row in that same
request.

Instagram sign-in asks only for permission to publish as that professional
account. The long-lived token is encrypted. A worker refreshes it before it
expires. A failed refresh sets the card to Reconnect required, the same pill the
mailbox already uses, and Instagram publishing pauses until someone signs in again.

Disconnect deletes that row’s token and sets the status to disconnected. Scheduled
posts that include the network are moved to Needs attention with a plain reason.
An audit entry records connect, reconnect, and disconnect, with the actor and the
network, and without the token.

The queue and the worker read these two rows. A network that is not `connected`
cannot be selected on a new post, and a due post skips that network instead of
sending with a dead token.

Each post stores `scheduled_at` and a status of `draft`, `scheduled`,
`publishing`, `posted`, or `attention`. Changing the date updates `scheduled_at`
only while the status is still `scheduled`. The worker claims a due post by
moving it to `publishing` first, so a date change or a cancel that arrives in
the same moment cannot win. Cancel returns that same post to `draft`, clears
the send time, and records who canceled it. The caption then shows under Drafts.
Posted history reads a per-network record: the network, the time it published,
and the link to the live post. That record is what the Posted tab shows, and it
is not editable. A network that is not connected when the send time arrives is
recorded as failed, and the post moves to Needs attention.

## What has to exist before sign-in works

1. A Meta app owned by the business, with the two callback addresses registered.
2. The ClearCode Reading Facebook Page.
3. An Instagram professional account, linked to that Page when one Facebook
   sign-in should fill both cards.
4. Meta’s approval for the Page posting and Instagram publishing permissions.
   Until that approval is in place, only Meta’s test users can finish sign-in.
5. The app id and secret in the server environment, the same way the Google
   client id and secret are configured for Gmail. They are not committed.

## Not in this version

Carousels, Reels, Stories, ads, inbox messages, comment replies, and a marketing
role below super administrator. The menu and the queue are shaped so those can be
added later without a new header item.
