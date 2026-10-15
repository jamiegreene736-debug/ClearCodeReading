from django.db import migrations
from django.db.models import Q


def seed_categories(apps, schema_editor):
    Category = apps.get_model("social", "SocialCategory")
    Post = apps.get_model("social", "SocialPost")
    for name, color, key in [
        ("Free resources", "#197B80", None),
        ("Blog posts", "#7C3AED", "blog"),
        ("Reading tips", "#2563EB", None),
        ("Programs & services", "#B7791F", None),
        ("Community & updates", "#15803D", None),
    ]:
        category, _ = Category.objects.get_or_create(
            name=name, defaults={"color": color, "system_key": key}
        )
        if key == "blog":
            Post.objects.filter(
                Q(blog_post__isnull=False) | Q(source="blog"), category__isnull=True
            ).update(category=category)


class Migration(migrations.Migration):
    dependencies = [("social", "0005_socialaccount_insights_error_and_more")]
    operations = [migrations.RunPython(seed_categories, migrations.RunPython.noop)]
