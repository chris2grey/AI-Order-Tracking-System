import hashlib
from django.core.cache import cache


def login_limited(request, username):
    # REMOTE_ADDR is overwritten by the trusted reverse proxy in deployment.
    values = [('ip', request.META.get('REMOTE_ADDR', ''), 40), ('user', username.casefold(), 8)]
    limited = False
    for scope, value, limit in values:
        key = 'login:v2:' + scope + ':' + hashlib.sha256(value.encode()).hexdigest()
        if cache.add(key, 1, 600):
            attempts = 1
        else:
            try:
                attempts = cache.incr(key)
            except ValueError:
                cache.set(key, 1, 600)
                attempts = 1
        limited = limited or attempts > limit
    return limited
