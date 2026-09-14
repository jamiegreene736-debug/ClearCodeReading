# Appearing in ChatGPT and other AI search results

Parents now ask ChatGPT, Gemini, Perplexity, and Google AI Overviews questions like
“reading centers in Orlando,” “best reading app for a struggling 3rd grader in
Florida,” or “dyslexia help near me.” Those systems do not show ten blue links.
They name a few sources they trust. This file is the ClearCode playbook for being
one of those names.

The website work in this repository makes ClearCode easier to find, parse, and cite.
The off-site work below is what actually gets a local business recommended.

## What AI engines look for

1. **A clear entity.** The same name, place, and description everywhere:
   ClearCode Reading, Orlando / Central Florida, K–8 structured literacy.
2. **An extractable answer.** A short, factual paragraph that answers the user’s
   question in plain language, then supporting detail.
3. **Agreement across sources.** The website, Google Business Profile, App Store,
   directories, news, and reviews should say the same things.
4. **Third-party mentions.** Models overweight reviews, local news, school
   newsletters, and “best of” lists more than a brand’s own homepage.
5. **Honesty about status.** The center opens in 2027. Say that every time. Invented
   addresses or “open now” claims will get ignored or contradicted.

## What the site now publishes for crawlers

| URL | Purpose |
| --- | --- |
| `/orlando/` | The page built for “reading center / reading app / reading help in Orlando or Florida.” |
| `/florida/` | Permanent redirect to `/orlando/`. |
| `/faq/` | Question-and-answer copy with FAQ structured data. |
| `/llms.txt` | A plain-language brief for AI crawlers. |
| `/robots.txt` | Allows GPTBot, OAI-SearchBot, ClaudeBot, PerplexityBot, and related crawlers. |
| `/sitemap.xml` | Public marketing pages and published blog posts. |

Public pages also include Organization / LocalBusiness structured data, a canonical
`https://clearcodereading.com` URL, and Orlando in titles and descriptions.

Do not add a street address to schema or listings until a real site is leased.

## Off-site work that matters more than the website

### 1. Google Business Profile

Create and verify **ClearCode Reading** as soon as Google will accept a coming-soon
or service-area listing.

- Primary category: something families actually search, such as **Tutoring service**
  or **Educational consultant**, plus reading / literacy if available.
- Service area: Orlando metro and Central Florida.
- Description: the same facts as `/orlando/` and `/llms.txt`.
- Services: structured literacy, Orton-Gillingham, Phonics for Reading, dyslexia
  educational support, family progress app.
- Photos of real instruction, specialists, and materials. No stock-only profile.
- Weekly posts: waitlist, hiring, Florida reading data, family resources.
- Ask every founding family and specialist for a review once they have a real
  experience to share.

Google’s local graph is one of the strongest sources ChatGPT and Gemini use for
“near me” and city queries.

### 2. Keep the name, place, and contact identical

Use the same NAP-style facts everywhere, even before there is a street address:

- Name: ClearCode Reading
- Place: Orlando, Florida (metro / Central Florida)
- Email: hello@clearcodereading.com
- Site: https://clearcodereading.com
- App: ClearCode Reading on the App Store

Create listings only when they can stay accurate:

- Apple Business Connect / Apple Maps
- Bing Places
- Florida education scholarship provider directories, once eligible
- International Dyslexia Association provider lists
- Understood.org, Reading Rockets, and similar parent resources when they accept
  provider submissions
- Orlando / Central Florida parenting sites and school-community guides

Inconsistent phone numbers or a fabricated suite number will suppress citations.

### 3. Earn mentions, do not only publish them

AI answers lean on sources other people wrote. Priority targets:

- Orlando-area parenting newsletters and magazines
- Local TV / radio education segments
- PTA, pediatric, and school-counselor referrals
- University and IMSE / OG training-community mentions
- Guest posts that cite Florida FAST reading data and then name ClearCode as a
  2027 Orlando option
- Thoughtful answers on Reddit, Nextdoor, and local Facebook groups that link to
  `/orlando/` or a resource, not a hard sell

A single accurate article in an Orlando family publication is worth more than
another homepage rewrite.

### 4. Write pages and posts that match the questions people ask AI

Publish blog and resource pieces whose titles are the query:

- Reading centers in Orlando: how to tell tutoring from structured literacy
- Reading apps vs. a reading specialist for a struggling Florida 3rd grader
- What Orton-Gillingham looks like for a K–2 child in Central Florida
- Florida education scholarships and reading intervention
- Signs a child needs reading help, and what to do this week

Open with a two-sentence answer. Add one or two sourced facts. Link to `/orlando/`,
`/how-it-works/`, and `/contact/`. Models extract that shape more reliably than
brand copy.

### 5. Distinguish the three products in every answer

People ask for three different things. ClearCode can appear for all three if the
copy stays specific:

| Query | Honest ClearCode answer |
| --- | --- |
| Reading center in Orlando | Opening 2027; waitlist now; groups of three; OG or Phonics for Reading |
| Reading app | iOS app available now; family progress, not a game that replaces a specialist |
| Reading help in Florida | Free resources now; scholarships planned; educational service, not a diagnosis |

If those facts blur together, models will skip ClearCode or invent details.

## Monthly check

Ask the same prompts in ChatGPT, Gemini, Perplexity, and Google:

- reading centers in Orlando
- reading help for a 2nd grader in Orlando
- dyslexia tutoring Orlando
- best reading app for struggling readers in Florida
- Orton-Gillingham near me Orlando
- Florida scholarship reading intervention

Record whether ClearCode is named, whether 2027 is correct, and which URL is cited.
Fix the source that is wrong or missing before changing brand language.

## What not to do

- Do not claim the center is open, licensed as a clinic, or able to diagnose
  dyslexia.
- Do not buy “we will submit you to ChatGPT” services. There is no submission form.
- Do not stuff every page with “Orlando Orlando Orlando.” One clear location page
  plus consistent facts is enough.
- Do not block GPTBot, OAI-SearchBot, ClaudeBot, or PerplexityBot in `robots.txt`.
