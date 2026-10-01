class SocialError(Exception):
    """A message that is safe to show in the portal.

    Never put access tokens, OAuth codes, or raw provider response bodies in
    this exception.
    """
