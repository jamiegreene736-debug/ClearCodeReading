from django.contrib import admin

from .models import CodeFile, Repository


admin.site.register(Repository)
admin.site.register(CodeFile)
