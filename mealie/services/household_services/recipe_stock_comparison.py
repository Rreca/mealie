from __future__ import annotations

from pydantic import UUID4

from mealie.repos.repository_factory import AllRepositories
from mealie.schema.household.household_food_inventory import (
    RecipeStockComparison,
    RecipeStockComparisonItem,
)
from mealie.schema.recipe.recipe_ingredient import IngredientUnit
from mealie.services.parser_services.parser_utils.unit_utils import UnitConverter


def _units_convertible(from_unit: IngredientUnit | None, to_unit: IngredientUnit | None) -> bool:
    """Whether stock in `from_unit` can be safely converted into `to_unit`.

    Mirrors the shopping-list merge rule: both units must carry standardized data and pint must
    consider their standard units compatible. Two null units (count-based) are trivially the same
    "unit"; a null on only one side is a conflict (never guess an equivalence).
    """
    if from_unit is None and to_unit is None:
        return True
    if from_unit is None or to_unit is None:
        return False
    if from_unit.id == to_unit.id:
        return True
    if not (from_unit.standard_unit and from_unit.standard_quantity):
        return False
    if not (to_unit.standard_unit and to_unit.standard_quantity):
        return False
    return UnitConverter().can_convert(from_unit.standard_unit, to_unit.standard_unit)


def _convert_quantity(quantity: float, from_unit: IngredientUnit | None, to_unit: IngredientUnit | None) -> float:
    """Convert `quantity` from `from_unit` into `to_unit`. Assumes `_units_convertible` is True.

    Same-unit (or both-null) conversions are the identity. Otherwise both units are standardized,
    so we build pint units from their standard data and convert -- the same technique the
    shopping-list merge uses, but converting instead of summing.
    """
    if from_unit is None or to_unit is None or from_unit.id == to_unit.id:
        return quantity

    uc = UnitConverter()
    PINT_FROM = "_mealie_stock_from"
    PINT_TO = "_mealie_stock_to"

    from_standard = uc.parse(from_unit.standard_unit, strict=True)
    to_standard = uc.parse(to_unit.standard_unit, strict=True)
    from_standard, to_standard = uc._resolve_ounce(from_standard, to_standard)

    uc.ureg.define(f"{PINT_FROM} = {from_unit.standard_quantity} * {from_standard}")
    uc.ureg.define(f"{PINT_TO} = {to_unit.standard_quantity} * {to_standard}")

    converted, _ = uc.convert(quantity, uc.parse(PINT_FROM), uc.parse(PINT_TO))
    return converted


class RecipeStockComparisonService:
    """Compares a recipe's ingredients against the authenticated household's stock.

    Read-only: it never mutates stock, records movements, or touches substitutions. Foods are
    matched strictly by RecipeIngredient.food_id == HouseholdFoodInventory.food_id.
    """

    def __init__(self, repos: AllRepositories):
        self.repos = repos

    def compare(self, recipe_id: UUID4, scale: float = 1) -> RecipeStockComparison:
        # Recipes are group-visible across households; load with the group-scoped repo.
        from mealie.repos.all_repositories import get_repositories
        from mealie.schema.response.pagination import PaginationQuery

        group_recipes = get_repositories(self.repos.session, group_id=self.repos.group_id, household_id=None).recipes
        recipe = group_recipes.get_one(recipe_id, "id")
        if recipe is None:
            recipe = group_recipes.get_one(recipe_id, "slug")
        if recipe is None:
            raise ValueError("Recipe not found")

        # Load the whole household inventory once, indexed by food id.
        inventory_page = self.repos.household_food_inventory.page_all(
            pagination=PaginationQuery(per_page=-1, page=1),
        )
        stock_by_food: dict[str, tuple[float, IngredientUnit | None]] = {}
        for row in inventory_page.items:
            stock_by_food[str(row.food_id)] = (row.quantity, row.unit)

        # Aggregate recipe needs per food so a food appearing on multiple lines is compared once
        # (subtracting the single stock row only once). Foodless ingredients each get their own
        # non-comparable row.
        per_food: dict[str, RecipeStockComparisonItem] = {}
        foodless: list[RecipeStockComparisonItem] = []

        for ingredient in recipe.recipe_ingredient or []:
            needed = (ingredient.quantity or 0) * scale
            # The recipe schema's ingredient rows don't expose the integer PK; treat it as optional.
            ingredient_id = getattr(ingredient, "id", None)
            if ingredient.food is None:
                foodless.append(
                    RecipeStockComparisonItem(
                        ingredient_id=ingredient_id,
                        food=None,
                        unit=ingredient.unit,
                        needed=needed,
                        have=0,
                        missing=needed,
                        comparable=False,
                        no_food=True,
                    )
                )
                continue

            food_key = str(ingredient.food.id)
            if food_key not in per_food:
                per_food[food_key] = RecipeStockComparisonItem(
                    ingredient_id=ingredient_id,
                    food=ingredient.food,
                    unit=ingredient.unit,
                    needed=0,
                )

            row = per_food[food_key]
            # Sum needs in the row's unit when the new line's unit is convertible to it.
            if _units_convertible(ingredient.unit, row.unit):
                row.needed += _convert_quantity(needed, ingredient.unit, row.unit)
            else:
                # Mixed units for the same food within one recipe: fall back to raw sum and flag
                # a conflict so we don't fabricate a conversion.
                row.needed += needed
                row.unit_conflict = True

        # Resolve stock for each aggregated food row.
        for food_key, row in per_food.items():
            have_qty, have_unit = stock_by_food.get(food_key, (0.0, None))
            row.have_unit = have_unit

            if row.unit_conflict:
                # Already conflicted while aggregating recipe lines; report have as-is, missing full.
                row.have = have_qty
                row.comparable = False
                row.missing = row.needed
                continue

            if food_key not in stock_by_food:
                # No stock row -> have 0, missing everything, still a valid numeric comparison.
                row.have = 0
                row.missing = row.needed
                continue

            if _units_convertible(have_unit, row.unit):
                converted_have = _convert_quantity(have_qty, have_unit, row.unit)
                row.have = converted_have
                row.missing = max(0.0, row.needed - converted_have)
            else:
                # Stock unit can't convert to the recipe unit: don't subtract. Report both, and set
                # missing to the full needed for safety.
                row.have = have_qty
                row.comparable = False
                row.unit_conflict = True
                row.missing = row.needed

        items = list(per_food.values()) + foodless
        return RecipeStockComparison(recipe_id=recipe.id, scale=scale, items=items)
