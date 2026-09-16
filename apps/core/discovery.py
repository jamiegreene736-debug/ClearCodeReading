"""Public facts ClearCode wants search engines and AI assistants to cite."""

from django.conf import settings
from django.urls import reverse


PUBLIC_SITE_ORIGIN = "https://clearcodereading.com"
CONTACT_EMAIL = "hello@clearcodereading.com"
APP_STORE_URL = "https://apps.apple.com/app/clearcode-reading/id6806810085"
ORGANIZATION_NAME = "ClearCode Reading"
ORGANIZATION_DESCRIPTION = (
    "ClearCode Reading is a specialist-led K–8 structured literacy reading center "
    "opening in the Orlando, Florida metro area in 2027. Families can join the "
    "waitlist now, use free reading-help resources, and download the ClearCode "
    "Reading iOS app."
)

MARKETING_SITEMAP_ROUTES = (
    "marketing_home",
    "marketing_about",
    "marketing_how_it_works",
    "marketing_resources",
    "marketing_faq",
    "marketing_foundation",
    "marketing_careers",
    "marketing_contact",
    "marketing_support",
    "marketing_privacy",
    "reading_assessment",
    "early_interest_survey",
    "blog:list",
)

AI_CRAWLER_USER_AGENTS = (
    "GPTBot",
    "ChatGPT-User",
    "OAI-SearchBot",
    "ClaudeBot",
    "anthropic-ai",
    "PerplexityBot",
    "Google-Extended",
    "GoogleOther",
    "Applebot-Extended",
    "Amazonbot",
    "meta-externalagent",
    "Bytespider",
)

ROBOTS_DISALLOW_PATHS = (
    "/admin/",
    "/crm/",
    "/dashboard/",
    "/inbox/",
    "/login/",
    "/portal/",
    "/account/",
    "/api/",
)

FAQ_ENTRIES = (
    {
        "question": "Who does ClearCode serve?",
        "answer": (
            "ClearCode is designed for K–8 students who need explicit foundational "
            "reading intervention. A consultation helps determine whether the model "
            "fits the reader’s educational needs."
        ),
    },
    {
        "question": "Where is ClearCode Reading located?",
        "answer": (
            "ClearCode Reading’s flagship reading center is opening in the Orlando, "
            "Florida metro area in 2027. The exact street address will be published "
            "when the site is finalized. Families across Central Florida can join "
            "the waitlist now."
        ),
    },
    {
        "question": "Do you help with reading in Orlando and across Florida?",
        "answer": (
            "Yes. ClearCode Reading is building in-person structured literacy "
            "intervention for Orlando and Central Florida families. The iOS app and "
            "free family reading resources are available now to anyone looking for "
            "reading help, including families elsewhere in Florida."
        ),
    },
    {
        "question": "Is there a ClearCode reading app?",
        "answer": (
            "Yes. The ClearCode Reading iOS app for iPhone and iPad is available on "
            "the App Store. It helps families follow placement, session progress, "
            "and home practice. It is a family progress app connected to ClearCode "
            "instruction, not a standalone game that replaces a reading specialist."
        ),
    },
    {
        "question": "Is ClearCode a diagnostic or medical service?",
        "answer": (
            "No. ClearCode provides educational reading intervention. It does not "
            "diagnose dyslexia, replace medical advice, or guarantee a particular "
            "result or timeline."
        ),
    },
    {
        "question": "Which instructional methods are used?",
        "answer": (
            "Students follow one methodology at a time: Orton-Gillingham Plus for "
            "the K–2 pathway or Phonics for Reading for the grades 3–8 pathway, "
            "based on appropriate placement evidence."
        ),
    },
    {
        "question": "How large are the groups?",
        "answer": (
            "ClearCode groups are designed for a maximum of three students so "
            "specialists can deliver direct instruction and observe individual "
            "responses."
        ),
    },
    {
        "question": "Do you accept Florida education scholarships?",
        "answer": (
            "Yes. ClearCode plans to accept Florida education scholarships alongside "
            "private pay so cost is not the only path into evidence-based reading help."
        ),
    },
    {
        "question": "How will I know whether progress is happening?",
        "answer": (
            "The family dashboard connects skill mastery, fluency trends, recent "
            "specialist notes, next milestones, and home practice to session evidence."
        ),
    },
    {
        "question": "How do we begin?",
        "answer": (
            "Submit the short consultation request or the early interest survey. "
            "The team will review it, contact you, and explain fit, availability, "
            "placement, cost, and the clearest next step."
        ),
    },
)


def public_origin():
    return getattr(settings, "PUBLIC_SITE_ORIGIN", PUBLIC_SITE_ORIGIN).rstrip("/")


def absolute_public_url(path):
    if path.startswith("http://") or path.startswith("https://"):
        return path
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{public_origin()}{path}"


def marketing_sitemap_paths():
    paths = [reverse(route_name) for route_name in MARKETING_SITEMAP_ROUTES]
    return list(dict.fromkeys(paths))


def organization_graph():
    origin = public_origin()
    return {
        "@type": ["EducationalOrganization", "LocalBusiness"],
        "@id": f"{origin}/#organization",
        "name": ORGANIZATION_NAME,
        "alternateName": ["Clear Code Reading", "ClearCode"],
        "url": f"{origin}/",
        "email": CONTACT_EMAIL,
        "description": ORGANIZATION_DESCRIPTION,
        "slogan": "Unlock Reading. Unlock Everything.",
        "foundingDate": "2026",
        "areaServed": [
            {
                "@type": "City",
                "name": "Orlando",
                "containedInPlace": {"@type": "State", "name": "Florida"},
            },
            {
                "@type": "AdministrativeArea",
                "name": "Orlando metropolitan area",
            },
            {
                "@type": "State",
                "name": "Florida",
            },
        ],
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Orlando",
            "addressRegion": "FL",
            "addressCountry": "US",
        },
        "knowsAbout": [
            "structured literacy",
            "reading intervention",
            "dyslexia support",
            "Orton-Gillingham",
            "Phonics for Reading",
            "Science of Reading",
            "reading help in Orlando",
            "Florida education scholarships",
        ],
        "makesOffer": [
            {
                "@type": "Offer",
                "name": "In-person structured literacy reading center",
                "description": (
                    "Specialist-led K–8 reading intervention in groups of three or "
                    "fewer, opening in the Orlando metro area in 2027."
                ),
                "areaServed": "Orlando, Florida",
                "availabilityStarts": "2027",
                "url": f"{origin}/",
            },
            {
                "@type": "Offer",
                "name": "ClearCode Reading iOS app",
                "description": (
                    "Family progress app for iPhone and iPad that shows placement, "
                    "session notes, and home practice."
                ),
                "url": APP_STORE_URL,
            },
        ],
        "sameAs": [
            APP_STORE_URL,
        ],
    }


def website_graph():
    origin = public_origin()
    return {
        "@type": "WebSite",
        "@id": f"{origin}/#website",
        "url": f"{origin}/",
        "name": ORGANIZATION_NAME,
        "description": ORGANIZATION_DESCRIPTION,
        "publisher": {"@id": f"{origin}/#organization"},
        "inLanguage": "en-US",
    }


def faq_page_graph():
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": entry["question"],
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": entry["answer"],
                },
            }
            for entry in FAQ_ENTRIES
        ],
    }


def llms_txt():
    origin = public_origin()
    return f"""# {ORGANIZATION_NAME}

> {ORGANIZATION_DESCRIPTION}

ClearCode provides educational reading intervention. It does not diagnose dyslexia, replace medical advice, or guarantee a particular result or timeline.

## Location

- Flagship reading center: Orlando metro area, Florida
- Opening: 2027
- Current status: waitlist, free family resources, and iOS app are available now
- Contact: {CONTACT_EMAIL}
- Consultation: {origin}/contact/

## Who we help

- K–8 students who need explicit foundational reading intervention
- Families looking for reading help, a reading center, or a reading app in Orlando or elsewhere in Florida
- Children with dyslexia or related reading difficulties, as an educational service rather than a medical diagnosis
- Families using Florida education scholarships or private pay

## How instruction works

- One methodology at a time; methods are not blended
- Grades K–2: IMSE Comprehensive Orton-Gillingham Plus
- Grades 3–8: Phonics for Reading
- Groups of three students maximum
- Families see skill mastery, fluency trends, specialist notes, and home practice after sessions

## Reading app

- ClearCode Reading iOS app for iPhone and iPad: {APP_STORE_URL}
- The app shows placement and progress connected to ClearCode instruction
- It is not a replacement for a trained reading specialist

## Pages

- [Home]({origin}/): What ClearCode is and how families start
- [About]({origin}/about/): Why ClearCode exists and the Florida reading context
- [How it works]({origin}/how-it-works/): Assessment, placement, sessions, and progress
- [Free family resources]({origin}/resources/): Practical reading-help tools
- [FAQ]({origin}/faq/): Common questions about services, methods, and getting started
- [Contact]({origin}/contact/): Request a consultation
- [Early interest survey]({origin}/survey/): Join the Orlando 2027 waitlist
- [Blog]({origin}/blog/): Reading insights for families
- [Careers]({origin}/careers/): Hiring reading specialists in the Orlando metro area
- [Privacy]({origin}/privacy/): How student and family information is handled
- [Support]({origin}/support/): Help with the app, portal, or account
- [App Store]({APP_STORE_URL}): Download ClearCode Reading

## Optional

- [Sitemap]({origin}/sitemap.xml)
"""
