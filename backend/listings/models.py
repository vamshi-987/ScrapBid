from django.db import models
from django.db.models.functions import Lower


class PricingUnit(models.TextChoices):
    PER_KG = "per_kg", "Per kg"
    PER_ITEM = "per_item", "Per item"


class Category(models.Model):
    """Admin-managed scrap category. Deactivate instead of deleting."""

    name = models.CharField(max_length=60)
    pricing_unit = models.CharField(
        max_length=10, choices=PricingUnit.choices, default=PricingUnit.PER_KG
    )
    # Restricted categories (e.g. e-waste, batteries) accept offers only from authorised dealers.
    is_restricted = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(Lower("name"), name="category_name_unique_ci"),
            models.CheckConstraint(
                condition=models.Q(pricing_unit__in=PricingUnit.values),
                name="category_pricing_unit_valid",
            ),
        ]

    def __str__(self):
        return self.name
