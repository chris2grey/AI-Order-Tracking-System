import os
import secrets
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
PRODUCTION = os.environ.get('DJANGO_ENV') == 'production'
if PRODUCTION:
    required = ['DJANGO_SECRET_KEY', 'DJANGO_ALLOWED_HOSTS', 'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD', 'REDIS_URL']
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise ImproperlyConfigured('生产环境缺少配置：' + ', '.join(missing))
    if len(os.environ['DJANGO_SECRET_KEY']) < 50 or os.environ.get('DJANGO_DEBUG') == '1' or '*' in os.environ['DJANGO_ALLOWED_HOSTS']:
        raise ImproperlyConfigured('生产环境要求至少50位随机密钥、关闭 DEBUG 并指定明确域名。')
DATA_DIR = Path(os.environ.get('DATA_DIR', BASE_DIR / 'data'))
DATA_DIR.mkdir(parents=True, exist_ok=True)
secret_file = DATA_DIR / '.secret'
if not os.environ.get('DJANGO_SECRET_KEY') and not secret_file.exists():
    secret_file.write_text(secrets.token_urlsafe(64), encoding='utf-8')
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY') or secret_file.read_text().strip()
DEBUG = os.environ.get('DJANGO_DEBUG', '0') == '1'
ALLOWED_HOSTS = [v.strip() for v in os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',') if v.strip()]
CSRF_TRUSTED_ORIGINS = [v.strip() for v in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if v.strip()]
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'tracker']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'tracker.middleware.PrivateResponseMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
TRUST_PROXY = os.environ.get('TRUST_PROXY') == '1'
MIDDLEWARE.insert(0, 'tracker.middleware.TrustedProxyMiddleware')
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request', 'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
if os.environ.get('POSTGRES_DB'):
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': os.environ['POSTGRES_DB'], 'USER': os.environ['POSTGRES_USER'], 'PASSWORD': os.environ['POSTGRES_PASSWORD'], 'HOST': os.environ.get('POSTGRES_HOST', '127.0.0.1'), 'PORT': os.environ.get('POSTGRES_PORT', '5432')}}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': DATA_DIR / 'db.sqlite3', 'OPTIONS': {'timeout': 20}}}
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}, {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'}, {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'}, {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_TZ = True
STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATIC_ROOT = Path(os.environ.get('STATIC_ROOT', STATIC_ROOT))
MEDIA_ROOT = DATA_DIR / 'private'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 3600
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
CSRF_COOKIE_HTTPONLY = True
CSRF_FAILURE_VIEW = 'tracker.views.csrf_failure'
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE = 15 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
if PRODUCTION or os.environ.get('HTTPS_ONLY') == '1':
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False
if os.environ.get('TRUST_PROXY') == '1':
    # Only enable behind our proxy; the application port must not be public.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
if os.environ.get('REDIS_URL'):
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': os.environ['REDIS_URL']}}
DATABASES['default']['CONN_MAX_AGE'] = 60 if PRODUCTION else 0
if DATABASES['default']['ENGINE'].endswith('postgresql'):
    DATABASES['default']['CONN_HEALTH_CHECKS'] = True
    DATABASES['default']['OPTIONS'] = {'connect_timeout': 5, 'sslmode': os.environ.get('POSTGRES_SSLMODE', 'prefer')}
TEMPLATES[0]['OPTIONS']['context_processors'].append('tracker.context_processors.deployment')
LOGGING = {'version': 1, 'disable_existing_loggers': False,
           'handlers': {'console': {'class': 'logging.StreamHandler'}},
           'root': {'handlers': ['console'], 'level': 'INFO'}}
