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
    """Phase 1 request body for the upsert endpoint. Intentionally accepts ONLY `quantity`.

    group_id, household_id and food_id are derived from the authenticated user and the path
    parameter, never from the request body. unit_id is not accepted in Phase 1.
    """

    quantity: float

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
