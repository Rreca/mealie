import datetime
import math

from pydantic import UUID4, ConfigDict, field_validator
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.orm.interfaces import LoaderOption

from mealie.db.models.household.food_inventory import HouseholdFoodInventory
from mealie.schema._mealie import MealieModel
from mealie.schema.recipe.recipe_ingredient import IngredientFood, IngredientUnit
from mealie.schema.response.pagination import PaginationBase


def _validate_quantity(v: float) -> float:
    # Reject NaN and +/-inf, which are valid JSON floats in some parsers but invalid stock values.
    if v is None or not math.isfinite(v):
        raise ValueError("quantity must be a finite number")
    if v < 0:
        raise ValueError("quantity must be greater than or equal to 0")
    return v


class HouseholdFoodInventoryUpdate(MealieModel):
    """Request body for the upsert endpoint.

    Accepts `quantity` (required) and, since Phase 2, an optional `unit_id`. group_id,
    household_id and food_id are derived from the authenticated user and the path parameter,
    never from the request body.

    `unit_id` is optional and distinguishes three cases via `model_fields_set` in the
    controller: omitted (preserve the existing unit), an explicit UUID (set it, after validating
    it belongs to the user's group), or explicit null (clear the unit / count-based stock).
    """

    quantity: float
    unit_id: UUID4 | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("quantity")
    def validate_quantity(cls, v: float) -> float:
        return _validate_quantity(v)


class HouseholdFoodInventorySave(MealieModel):
    """Internal persistence schema. Scoping ids are set by the application layer."""

    group_id: UUID4
    household_id: UUID4
    food_id: UUID4
    quantity: float = 0
    unit_id: UUID4 | None = None

    @field_validator("quantity")
    def validate_quantity(cls, v: float) -> float:
        return _validate_quantity(v)


class HouseholdFoodInventoryOut(MealieModel):
    id: UUID4
    group_id: UUID4
    household_id: UUID4
    food_id: UUID4
    quantity: float
    unit_id: UUID4 | None = None

    food: IngredientFood | None = None
    unit: IngredientUnit | None = None

    created_at: datetime.datetime | None = None
    updated_at: datetime.datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def loader_options(cls) -> list[LoaderOption]:
        return [
            selectinload(HouseholdFoodInventory.food),
            joinedload(HouseholdFoodInventory.unit),
        ]


class HouseholdFoodInventoryPagination(PaginationBase):
    items: list[HouseholdFoodInventoryOut]


# ==================================================================================================================
# Phase 2: recipe vs stock comparison


class RecipeStockComparisonItem(MealieModel):
    """One recipe ingredient compared against the household's stock of its food.

    `needed`/`have`/`missing` are expressed in `unit` (the recipe ingredient's unit) whenever the
    comparison is possible. When the food is missing, quantities can't be compared:

    - `comparable=False` + `no_food=True`: the ingredient has no food, so stock can't be matched.
    - `comparable=False` + `unit_conflict=True`: the food is stocked but in a unit that can't be
      converted to the recipe's unit; `have`/`have_unit` are reported as-is and `missing` is set
      to the full `needed` for safety (do not silently subtract across incompatible units).
    - `comparable=True`: `missing = max(0, needed - have)` in `unit`.
    """

    ingredient_id: int | None = None
    """The RecipeIngredientModel.id this row was computed from, when available."""

    food: IngredientFood | None = None
    unit: IngredientUnit | None = None
    """The recipe ingredient's unit (the unit `needed`/`missing` are expressed in)."""

    needed: float = 0
    have: float = 0
    missing: float = 0

    have_unit: IngredientUnit | None = None
    """The stock's own unit. Differs from `unit` only on a unit conflict."""

    comparable: bool = True
    no_food: bool = False
    unit_conflict: bool = False


class RecipeStockComparison(MealieModel):
    recipe_id: UUID4
    scale: float = 1
    items: list[RecipeStockComparisonItem] = []
