"""Unit tests for RecipeStockComparisonService (Phase 2).

Covers the needed/have/missing calculation and unit handling: same unit, convertible units,
incompatible units (conflict), no food, no stock, have >= needed, scaling, and the mandatory
end-to-end example (eggs / flour / milk).
"""

from uuid import UUID

from mealie.schema.household.household_food_inventory import HouseholdFoodInventorySave
from mealie.schema.recipe.recipe import Recipe
from mealie.schema.recipe.recipe_ingredient import (
    RecipeIngredient,
    SaveIngredientFood,
    SaveIngredientUnit,
    StandardizedUnitType,
)
from mealie.services.household_services.recipe_stock_comparison import RecipeStockComparisonService
from tests.utils.factories import random_string
from tests.utils.fixture_schemas import TestUser


def _food(user: TestUser):
    return user.repos.ingredient_foods.create(SaveIngredientFood(name=random_string(10), group_id=user.group_id))


def _unit(user: TestUser, standard_quantity=None, standard_unit=None):
    return user.repos.ingredient_units.create(
        SaveIngredientUnit(
            name=random_string(10),
            group_id=user.group_id,
            standard_quantity=standard_quantity,
            standard_unit=standard_unit,
        )
    )


def _recipe(user: TestUser, ingredients: list[RecipeIngredient]):
    return user.repos.recipes.create(
        Recipe(
            name=random_string(12),
            user_id=user.user_id,
            group_id=UUID(user.group_id),
            recipe_ingredient=ingredients,
        )  # type: ignore
    )


def _set_stock(user: TestUser, food_id, quantity, unit_id=None):
    return user.repos.household_food_inventory.create(
        HouseholdFoodInventorySave(
            group_id=user.group_id,
            household_id=user.household_id,
            food_id=food_id,
            quantity=quantity,
            unit_id=unit_id,
        )
    )


def _item_for_food(result, food_id):
    for item in result.items:
        if item.food and str(item.food.id) == str(food_id):
            return item
    return None


def test_same_unit_missing(unique_user: TestUser):
    """6 needed, 2 have, same (null) unit -> missing 4."""
    db = unique_user.repos
    eggs = _food(unique_user)
    recipe = _recipe(unique_user, [RecipeIngredient(quantity=6, food=eggs)])  # type: ignore
    _set_stock(unique_user, eggs.id, 2)

    result = RecipeStockComparisonService(db).compare(recipe.id)
    item = _item_for_food(result, eggs.id)
    assert item is not None
    assert item.comparable is True
    assert item.needed == 6
    assert item.have == 2
    assert item.missing == 4


def test_have_more_than_needed(unique_user: TestUser):
    db = unique_user.repos
    milk = _food(unique_user)
    liter = _unit(unique_user, standard_quantity=1, standard_unit=StandardizedUnitType.LITER)
    recipe = _recipe(unique_user, [RecipeIngredient(quantity=1, unit=liter, food=milk)])  # type: ignore
    _set_stock(unique_user, milk.id, 2, unit_id=liter.id)

    result = RecipeStockComparisonService(db).compare(recipe.id)
    item = _item_for_food(result, milk.id)
    assert item is not None
    assert item.comparable is True
    assert item.missing == 0


def test_food_without_stock(unique_user: TestUser):
    db = unique_user.repos
    eggs = _food(unique_user)
    recipe = _recipe(unique_user, [RecipeIngredient(quantity=6, food=eggs)])  # type: ignore

    result = RecipeStockComparisonService(db).compare(recipe.id)
    item = _item_for_food(result, eggs.id)
    assert item is not None
    assert item.have == 0
    assert item.missing == 6
    assert item.comparable is True


def test_convertible_units(unique_user: TestUser):
    """Recipe wants 1 kg, stock is 500 g -> missing 0.5 kg (expressed in recipe unit)."""
    db = unique_user.repos
    flour = _food(unique_user)
    gram = _unit(unique_user, standard_quantity=1, standard_unit=StandardizedUnitType.GRAM)
    kilogram = _unit(unique_user, standard_quantity=1000, standard_unit=StandardizedUnitType.GRAM)
    recipe = _recipe(unique_user, [RecipeIngredient(quantity=1, unit=kilogram, food=flour)])  # type: ignore
    _set_stock(unique_user, flour.id, 500, unit_id=gram.id)

    result = RecipeStockComparisonService(db).compare(recipe.id)
    item = _item_for_food(result, flour.id)
    assert item is not None
    assert item.comparable is True
    assert item.needed == 1
    assert abs(item.have - 0.5) < 1e-9  # 500 g == 0.5 kg
    assert abs(item.missing - 0.5) < 1e-9


def test_incompatible_units_conflict(unique_user: TestUser):
    """Recipe wants 500 g, stock is 2 (count/no unit) -> conflict, missing = full needed."""
    db = unique_user.repos
    flour = _food(unique_user)
    gram = _unit(unique_user, standard_quantity=1, standard_unit=StandardizedUnitType.GRAM)
    recipe = _recipe(unique_user, [RecipeIngredient(quantity=500, unit=gram, food=flour)])  # type: ignore
    _set_stock(unique_user, flour.id, 2, unit_id=None)  # count-based stock

    result = RecipeStockComparisonService(db).compare(recipe.id)
    item = _item_for_food(result, flour.id)
    assert item is not None
    assert item.comparable is False
    assert item.unit_conflict is True
    assert item.needed == 500
    assert item.missing == 500  # full needed, no silent subtraction


def test_foodless_ingredient_not_comparable(unique_user: TestUser):
    db = unique_user.repos
    recipe = _recipe(unique_user, [RecipeIngredient(quantity=1, note=random_string(8))])  # type: ignore

    result = RecipeStockComparisonService(db).compare(recipe.id)
    assert len(result.items) == 1
    assert result.items[0].no_food is True
    assert result.items[0].comparable is False


def test_scale_multiplies_needed(unique_user: TestUser):
    db = unique_user.repos
    eggs = _food(unique_user)
    recipe = _recipe(unique_user, [RecipeIngredient(quantity=6, food=eggs)])  # type: ignore
    _set_stock(unique_user, eggs.id, 2)

    result = RecipeStockComparisonService(db).compare(recipe.id, scale=2)
    item = _item_for_food(result, eggs.id)
    assert item is not None
    assert item.needed == 12
    assert item.have == 2
    assert item.missing == 10


def test_same_food_multiple_lines_aggregated(unique_user: TestUser):
    """Same food on two lines -> needs summed, single stock row subtracted once."""
    db = unique_user.repos
    eggs = _food(unique_user)
    recipe = _recipe(
        unique_user,
        [RecipeIngredient(quantity=4, food=eggs), RecipeIngredient(quantity=2, food=eggs)],  # type: ignore
    )
    _set_stock(unique_user, eggs.id, 2)

    result = RecipeStockComparisonService(db).compare(recipe.id)
    egg_items = [i for i in result.items if i.food and str(i.food.id) == str(eggs.id)]
    assert len(egg_items) == 1
    assert egg_items[0].needed == 6
    assert egg_items[0].have == 2
    assert egg_items[0].missing == 4


def test_mandatory_example(unique_user: TestUser):
    """Eggs 6/2 -> 4; Flour 1kg/500g -> 0.5kg; Milk 1L/2L -> sufficient."""
    db = unique_user.repos
    eggs = _food(unique_user)
    flour = _food(unique_user)
    milk = _food(unique_user)

    gram = _unit(unique_user, standard_quantity=1, standard_unit=StandardizedUnitType.GRAM)
    kilogram = _unit(unique_user, standard_quantity=1000, standard_unit=StandardizedUnitType.GRAM)
    liter = _unit(unique_user, standard_quantity=1, standard_unit=StandardizedUnitType.LITER)

    recipe = _recipe(
        unique_user,
        [
            RecipeIngredient(quantity=6, food=eggs),  # type: ignore
            RecipeIngredient(quantity=1, unit=kilogram, food=flour),  # type: ignore
            RecipeIngredient(quantity=1, unit=liter, food=milk),  # type: ignore
        ],
    )
    _set_stock(unique_user, eggs.id, 2)
    _set_stock(unique_user, flour.id, 500, unit_id=gram.id)
    _set_stock(unique_user, milk.id, 2, unit_id=liter.id)

    result = RecipeStockComparisonService(db).compare(recipe.id)

    egg = _item_for_food(result, eggs.id)
    assert egg.needed == 6 and egg.have == 2 and egg.missing == 4

    fl = _item_for_food(result, flour.id)
    assert fl.needed == 1 and abs(fl.have - 0.5) < 1e-9 and abs(fl.missing - 0.5) < 1e-9

    mk = _item_for_food(result, milk.id)
    assert mk.missing == 0  # 2 L covers 1 L
