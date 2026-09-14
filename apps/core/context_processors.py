import json

from apps.core.discovery import organization_graph, public_origin, website_graph


def public_site(request):
    origin = public_origin()
    path = getattr(request, "path", "/") or "/"
    return {
        "public_site_origin": origin,
        "public_canonical_url": f"{origin}{path}",
        "organization_json_ld": json.dumps(
            {
                "@context": "https://schema.org",
                "@graph": [organization_graph(), website_graph()],
            },
            ensure_ascii=False,
        ),
    }
