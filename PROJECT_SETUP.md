# Clear Code Reading Project Setup

## `manage.py`
```python
#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    """Run administrative tasks."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clearcodereading.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Is it installed and available on your "
            "PYTHONPATH environment variable? Did you forget to activate a "
            "virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()

```

## `requirements.txt`
```text
Django==5.1.15
djangorestframework==3.16.1
django-tenants==3.10.1
djangorestframework-simplejwt==5.5.1
django-guardian==3.3.1
django-cors-headers==4.9.0
celery==5.6.3
redis==7.4.0
drf-spectacular==0.29.0
psycopg2-binary==2.9.12
django-storages==1.14.6
boto3==1.41.2
gunicorn==26.0.0

```

## `Dockerfile`
```text
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev netcat-traditional \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY . .
RUN chmod +x /app/scripts/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["gunicorn", "clearcodereading.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]

```

## `docker-compose.yml`
```yaml
services:
  web:
    build: .
    command: gunicorn clearcodereading.wsgi:application --bind 0.0.0.0:8000 --workers 3
    env_file:
      - .env
    volumes:
      - .:/app
    ports:
      - "8000:8000"
    depends_on:
      - db
      - redis

  celery:
    build: .
    command: celery -A clearcodereading worker -l info
    env_file:
      - .env
    volumes:
      - .:/app
    depends_on:
      - db
      - redis

  celery-beat:
    build: .
    command: celery -A clearcodereading beat -l info
    env_file:
      - .env
    volumes:
      - .:/app
    depends_on:
      - db
      - redis

  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: clearcodereading
      POSTGRES_USER: clearcode
      POSTGRES_PASSWORD: clearcode
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:

```

## `.env.example`
```dotenv
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,0.0.0.0
DJANGO_CSRF_TRUSTED_ORIGINS=http://localhost:8000

POSTGRES_DB=clearcodereading
POSTGRES_USER=clearcode
POSTGRES_PASSWORD=clearcode
POSTGRES_HOST=db
POSTGRES_PORT=5432

CORS_ALLOW_ALL_ORIGINS=0
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1

USE_S3=0
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_STORAGE_BUCKET_NAME=
AWS_S3_REGION_NAME=us-east-1
AWS_S3_CUSTOM_DOMAIN=

```

## `.gitignore`
```gitignore
__pycache__/
*.py[cod]
*.sqlite3
.Python
.env
.venv/
venv/
env/
.coverage
htmlcov/
staticfiles/
media/
.DS_Store
.pytest_cache/

```

## `README.md`
```markdown
# Clear Code Reading

Tenant-aware Django 5.1 API backend for Clear Code Reading.

## Quickstart

```bash
cp .env.example .env
docker compose up --build
```

Run migrations once the containers are up:

```bash
docker compose run --rm web python manage.py migrate_schemas --shared
```

API docs are exposed at:

- `http://localhost:8000/api/docs/`
- `http://localhost:8000/api/redoc/`
- `http://localhost:8000/api/schema/`

## Structure

```text
clearcodereading/
apps/
  accounts/
  api/
  billing/
  core/
  documents/
  organizations/
  readings/
  repositories/
  tenants/
scripts/
```

```

## `scripts/entrypoint.sh`
```sh
#!/bin/sh
set -e

if [ -n "$POSTGRES_HOST" ]; then
  until nc -z "$POSTGRES_HOST" "${POSTGRES_PORT:-5432}"; do
    echo "Waiting for PostgreSQL at $POSTGRES_HOST:${POSTGRES_PORT:-5432}..."
    sleep 1
  done
fi

exec "$@"

```

## `clearcodereading/__init__.py`
```python
from .celery import app as celery_app

__all__ = ("celery_app",)

```

## `clearcodereading/asgi.py`
```python
"""ASGI config for clearcodereading project."""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clearcodereading.settings")

application = get_asgi_application()

```

## `clearcodereading/celery.py`
```python
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clearcodereading.settings")

app = Celery("clearcodereading")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

```

## `clearcodereading/settings.py`
```python
from datetime import timedelta
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-only-change-me")
DEBUG = os.getenv("DJANGO_DEBUG", "0") == "1"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,0.0.0.0").split(",")
    if host.strip()
]

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

SHARED_APPS = [
    "django_tenants",
    "apps.tenants",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "guardian",
    "corsheaders",
    "drf_spectacular",
    "apps.core",
    "apps.accounts",
]

TENANT_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "guardian",
    "apps.accounts",
    "apps.organizations",
    "apps.repositories",
    "apps.readings",
    "apps.documents",
    "apps.billing",
    "apps.api",
]

INSTALLED_APPS = list(dict.fromkeys(SHARED_APPS + TENANT_APPS))

MIDDLEWARE = [
    "django_tenants.middleware.main.TenantMainMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "clearcodereading.urls"
PUBLIC_SCHEMA_URLCONF = "clearcodereading.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "clearcodereading.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django_tenants.postgresql_backend",
        "NAME": os.getenv("POSTGRES_DB", "clearcodereading"),
        "USER": os.getenv("POSTGRES_USER", "clearcode"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "clearcode"),
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.getenv("POSTGRES_CONN_MAX_AGE", "60")),
    }
}

DATABASE_ROUTERS = ("django_tenants.routers.TenantSyncRouter",)
TENANT_MODEL = "tenants.Client"
TENANT_DOMAIN_MODEL = "tenants.Domain"
SHOW_PUBLIC_IF_NO_TENANT_FOUND = os.getenv("SHOW_PUBLIC_IF_NO_TENANT_FOUND", "1") == "1"

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = (
    "django.contrib.auth.backends.ModelBackend",
    "guardian.backends.ObjectPermissionBackend",
)
ANONYMOUS_USER_NAME = "anonymous"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = os.getenv("STATIC_URL", "/static/")
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = os.getenv("MEDIA_URL", "/media/")
MEDIA_ROOT = BASE_DIR / "media"

USE_S3 = os.getenv("USE_S3", "0") == "1"
if USE_S3:
    INSTALLED_APPS.append("storages")
    AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = os.getenv("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "us-east-1")
    AWS_S3_CUSTOM_DOMAIN = os.getenv("AWS_S3_CUSTOM_DOMAIN")
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = False
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3boto3.S3Boto3Storage"},
        "staticfiles": {"BACKEND": "storages.backends.s3boto3.S3StaticStorage"},
    }
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(os.getenv("JWT_ACCESS_MINUTES", "15"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=int(os.getenv("JWT_REFRESH_DAYS", "7"))),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

CORS_ALLOW_ALL_ORIGINS = os.getenv("CORS_ALLOW_ALL_ORIGINS", "0") == "1"
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
    if origin.strip()
]
CORS_ALLOW_CREDENTIALS = True

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

SPECTACULAR_SETTINGS = {
    "TITLE": "Clear Code Reading API",
    "DESCRIPTION": "Tenant-aware API for guided code reading, repositories, notes, documents, and billing.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": r"/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "AUTHENTICATION_WHITELIST": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
CSRF_COOKIE_SECURE = os.getenv("CSRF_COOKIE_SECURE", "0") == "1"
X_FRAME_OPTIONS = "DENY"

```

## `clearcodereading/urls.py`
```python
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/v1/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/auth/token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    path("api/v1/", include("apps.api.urls")),
]

```

## `clearcodereading/wsgi.py`
```python
"""WSGI config for clearcodereading project."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "clearcodereading.settings")

application = get_wsgi_application()

```

## `apps/__init__.py`
```python


```

## `apps/accounts/__init__.py`
```python


```

## `apps/accounts/admin.py`
```python
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    pass

```

## `apps/accounts/apps.py`
```python
from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"

```

## `apps/accounts/migrations/__init__.py`
```python


```

## `apps/accounts/models.py`
```python
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    pass

```

## `apps/accounts/urls.py`
```python
from django.urls import path

urlpatterns = []

```

## `apps/api/__init__.py`
```python


```

## `apps/api/apps.py`
```python
from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api"

```

## `apps/api/urls.py`
```python
from django.urls import include, path

urlpatterns = [
    path("", include("apps.core.urls")),
    path("accounts/", include("apps.accounts.urls")),
    path("organizations/", include("apps.organizations.urls")),
    path("repositories/", include("apps.repositories.urls")),
    path("readings/", include("apps.readings.urls")),
    path("documents/", include("apps.documents.urls")),
    path("billing/", include("apps.billing.urls")),
]

```

## `apps/billing/__init__.py`
```python


```

## `apps/billing/admin.py`
```python
from django.contrib import admin

from .models import Subscription


admin.site.register(Subscription)

```

## `apps/billing/apps.py`
```python
from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.billing"

```

## `apps/billing/migrations/__init__.py`
```python


```

## `apps/billing/models.py`
```python
from django.db import models

from apps.core.models import TimeStampedModel
from apps.organizations.models import Organization


class Subscription(TimeStampedModel):
    class Status(models.TextChoices):
        TRIALING = "trialing", "Trialing"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        CANCELED = "canceled", "Canceled"

    organization = models.OneToOneField(Organization, on_delete=models.CASCADE, related_name="subscription")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.TRIALING)
    plan = models.CharField(max_length=80, default="starter")
    external_customer_id = models.CharField(max_length=120, blank=True)
    external_subscription_id = models.CharField(max_length=120, blank=True)

    def __str__(self):
        return f"{self.organization} - {self.plan}"

```

## `apps/billing/urls.py`
```python
from django.urls import path

urlpatterns = []

```

## `apps/core/__init__.py`
```python


```

## `apps/core/apps.py`
```python
from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.core"

```

## `apps/core/models.py`
```python
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

```

## `apps/core/urls.py`
```python
from django.urls import path
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthCheckView(APIView):
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        return Response({"status": "ok"})


urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health"),
]

```

## `apps/documents/__init__.py`
```python


```

## `apps/documents/admin.py`
```python
from django.contrib import admin

from .models import Document


admin.site.register(Document)

```

## `apps/documents/apps.py`
```python
from django.apps import AppConfig


class DocumentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.documents"

```

## `apps/documents/migrations/__init__.py`
```python


```

## `apps/documents/models.py`
```python
from django.db import models

from apps.core.models import TimeStampedModel
from apps.readings.models import ReadingSession


class Document(TimeStampedModel):
    session = models.ForeignKey(ReadingSession, on_delete=models.CASCADE, related_name="documents")
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to="documents/%Y/%m/%d/", blank=True)
    content = models.TextField(blank=True)

    def __str__(self):
        return self.title

```

## `apps/documents/urls.py`
```python
from django.urls import path

urlpatterns = []

```

## `apps/organizations/__init__.py`
```python


```

## `apps/organizations/admin.py`
```python
from django.contrib import admin

from .models import Membership, Organization


admin.site.register(Organization)
admin.site.register(Membership)

```

## `apps/organizations/apps.py`
```python
from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.organizations"

```

## `apps/organizations/migrations/__init__.py`
```python


```

## `apps/organizations/models.py`
```python
from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel


class Organization(TimeStampedModel):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=80, unique=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="Membership", related_name="organizations")

    def __str__(self):
        return self.name


class Membership(TimeStampedModel):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)

    class Meta:
        unique_together = ("organization", "user")

    def __str__(self):
        return f"{self.user} in {self.organization}"

```

## `apps/organizations/urls.py`
```python
from django.urls import path

urlpatterns = []

```

## `apps/readings/__init__.py`
```python


```

## `apps/readings/admin.py`
```python
from django.contrib import admin

from .models import Annotation, ReadingSession


admin.site.register(ReadingSession)
admin.site.register(Annotation)

```

## `apps/readings/apps.py`
```python
from django.apps import AppConfig


class ReadingsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.readings"

```

## `apps/readings/migrations/__init__.py`
```python


```

## `apps/readings/models.py`
```python
from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel
from apps.repositories.models import CodeFile, Repository


class ReadingSession(TimeStampedModel):
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name="reading_sessions")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True)

    def __str__(self):
        return self.title


class Annotation(TimeStampedModel):
    session = models.ForeignKey(ReadingSession, on_delete=models.CASCADE, related_name="annotations")
    file = models.ForeignKey(CodeFile, on_delete=models.CASCADE, related_name="annotations")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    start_line = models.PositiveIntegerField()
    end_line = models.PositiveIntegerField()
    body = models.TextField()

    def __str__(self):
        return f"{self.file.path}:{self.start_line}"

```

## `apps/readings/urls.py`
```python
from django.urls import path

urlpatterns = []

```

## `apps/repositories/__init__.py`
```python


```

## `apps/repositories/admin.py`
```python
from django.contrib import admin

from .models import CodeFile, Repository


admin.site.register(Repository)
admin.site.register(CodeFile)

```

## `apps/repositories/apps.py`
```python
from django.apps import AppConfig


class RepositoriesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.repositories"

```

## `apps/repositories/migrations/__init__.py`
```python


```

## `apps/repositories/models.py`
```python
from django.db import models

from apps.core.models import TimeStampedModel
from apps.organizations.models import Organization


class Repository(TimeStampedModel):
    class Provider(models.TextChoices):
        GITHUB = "github", "GitHub"
        GITLAB = "gitlab", "GitLab"
        BITBUCKET = "bitbucket", "Bitbucket"
        OTHER = "other", "Other"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="repositories")
    provider = models.CharField(max_length=30, choices=Provider.choices, default=Provider.GITHUB)
    name = models.CharField(max_length=255)
    full_name = models.CharField(max_length=255)
    clone_url = models.URLField()
    default_branch = models.CharField(max_length=120, default="main")
    external_id = models.CharField(max_length=120, blank=True)

    class Meta:
        unique_together = ("organization", "provider", "full_name")

    def __str__(self):
        return self.full_name


class CodeFile(TimeStampedModel):
    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name="files")
    path = models.CharField(max_length=1024)
    language = models.CharField(max_length=80, blank=True)
    checksum = models.CharField(max_length=128, blank=True)

    class Meta:
        unique_together = ("repository", "path")

    def __str__(self):
        return self.path

```

## `apps/repositories/urls.py`
```python
from django.urls import path

urlpatterns = []

```

## `apps/tenants/__init__.py`
```python


```

## `apps/tenants/admin.py`
```python
from django.contrib import admin

from .models import Client, Domain


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "schema_name", "paid_until", "on_trial", "created_on")
    search_fields = ("name", "slug", "schema_name")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Domain)
class DomainAdmin(admin.ModelAdmin):
    list_display = ("domain", "tenant", "is_primary")
    search_fields = ("domain",)

```

## `apps/tenants/apps.py`
```python
from django.apps import AppConfig


class TenantsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.tenants"

```

## `apps/tenants/migrations/__init__.py`
```python


```

## `apps/tenants/models.py`
```python
from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Client(TenantMixin):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=80, unique=True)
    paid_until = models.DateField(null=True, blank=True)
    on_trial = models.BooleanField(default=True)
    created_on = models.DateField(auto_now_add=True)
    auto_create_schema = True
    auto_drop_schema = False

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    pass

```
