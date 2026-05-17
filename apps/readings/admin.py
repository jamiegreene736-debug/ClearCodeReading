from django.contrib import admin

from .models import Annotation, ReadingSession


admin.site.register(ReadingSession)
admin.site.register(Annotation)
