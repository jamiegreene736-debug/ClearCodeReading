# Marketing menu and social media

Plan for a Marketing item in the administrator portal, with one section: Social media.
This covers Facebook and Instagram only. It is a plan for the menu, screens, and behavior.
It does not add the feature yet.

## Where it sits

Super administrators already see Dashboard, Free resources, Program, Manage, and CRM
in `templates/portal/_header.html`. Marketing is a new disclosure in that same row,
after Manage and before CRM. It uses the same panel pattern as Program and Manage.

The panel has one destination:

- **Social media** — draft, schedule, and publish posts on the ClearCode Reading
  Facebook Page and Instagram account.

Do not add empty items for later ideas. The newsletter stays in the CRM.

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

## Screens

1. **Marketing menu.** The header item opens a panel whose only section is Social media.
2. **Connect.** Before anything can publish, one button starts a Meta sign-in. The
   person signs in with the account that manages the Page, chooses the ClearCode
   Reading Page, and returns to the portal. Instagram is the professional account
   already linked to that Page in Meta. A personal Instagram account cannot receive
   posts. The connection is stored the same way Gmail credentials are stored:
   encrypted, and it can be reconnected or disconnected. Stories, Reels, ads,
   messages, and other people’s pages are out of this connection.
3. **Queue.** After connecting, the page shows both accounts as connected, then the
   posts in tabs: Upcoming, Drafts, Published, and Needs attention. Each row shows
   the photo, the networks, the Eastern time, and whether it came from a brief or
   was written by hand. Upcoming posts can be edited or canceled until they send.
4. **New post.** A switch at the top chooses “From a brief” or “Write it yourself.”
   The brief screen shows the notes on the left and a Facebook preview plus an
   Instagram preview on the right. The manual screen shows the caption, networks,
   photo, schedule, and one preview.

A line on the composer states the rule for this brand: do not include a child’s
name, photo, school, or reading scores.

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

## What has to exist before Connect works

1. A Meta app owned by the business.
2. The ClearCode Reading Facebook Page.
3. An Instagram professional account linked to that Page.
4. Meta’s approval for the Page posting and Instagram publishing permissions.
   Until that approval is in place, only Meta’s test users can complete Connect.
5. One connection made from this screen by a super administrator.

## Not in this version

Carousels, Reels, Stories, ads, inbox messages, comment replies, and a marketing
role below super administrator. The menu and the queue are shaped so those can be
added later without a new header item.
