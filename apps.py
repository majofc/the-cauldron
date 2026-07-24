from django.apps import AppConfig


class TheCauldronConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "the_cauldron"
    verbose_name = "The Cauldron"

    def ready(self):
        from the_cauldron import signals  # noqa: F401  (registers receivers)
