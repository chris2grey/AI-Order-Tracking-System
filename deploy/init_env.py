"""Generate private runtime configuration, never overwriting an existing .env."""
import argparse
import os
from pathlib import Path
import re
import secrets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--domain', required=True, help='Domain name, without https:// or path')
    parser.add_argument('--email', required=True, help='Certificate renewal contact')
    args = parser.parse_args()
    domain = args.domain.lower()
    if len(domain) > 253 or not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?', domain):
        parser.error('请填写有效域名，例如 orders.example.com')
    if not re.fullmatch(r'[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}', args.email):
        parser.error('证书邮箱格式不正确')
    root = Path(__file__).resolve().parent.parent
    config = {
        'SITE_DOMAIN': domain, 'ACME_EMAIL': args.email,
        'DJANGO_SECRET_KEY': secrets.token_urlsafe(64),
        'DJANGO_ALLOWED_HOSTS': domain,
        'CSRF_TRUSTED_ORIGINS': 'https://' + domain,
        'POSTGRES_DB': 'order_tracker', 'POSTGRES_USER': 'order_tracker',
        'POSTGRES_PASSWORD': secrets.token_urlsafe(36),
        'POSTGRES_ADMIN_PASSWORD': secrets.token_urlsafe(36), 'WEB_WORKERS': '2',
    }
    try:
        fd = os.open(root / '.env', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error('.env 已存在，已停止以保护原有密钥。')
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write('\n'.join(f'{k}={v}' for k, v in config.items()) + '\n')
    (root / 'backups').mkdir(mode=0o700, exist_ok=True)
    print('已生成 .env，密钥未输出。Linux 上执行 sudo chown 10001:10001 backups。')


if __name__ == '__main__':
    main()
