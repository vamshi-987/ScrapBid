import pytest

from listings.models import Category, PricingUnit


@pytest.fixture
def categories(db):
    return {
        "paper": Category.objects.create(name="Paper"),
        "fridge": Category.objects.create(name="Appliance", pricing_unit=PricingUnit.PER_ITEM),
        "ewaste": Category.objects.create(name="E-waste", is_restricted=True),
    }
