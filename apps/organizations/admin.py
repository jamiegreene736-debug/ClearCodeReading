from django.contrib import admin

from .models import Membership, Organization


admin.site.register(Organization)
admin.site.register(Membership)
