import os

bind = '0.0.0.0:8000'
workers = int(os.environ.get('WEB_WORKERS', '2'))
worker_class = 'gthread'
threads = 2
timeout = 120
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
accesslog = '-'
errorlog = '-'
# Only the proxy is reachable from the public network. Django trusts its TLS flag.
forwarded_allow_ips = '*'
# Do not record query strings containing customer searches or request bodies.
access_log_format = '%(h)s %(m)s %(U)s %(s)s %(L)s'
