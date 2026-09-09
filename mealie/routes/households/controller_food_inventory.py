from functools import cached_property

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import UUID4
from sqlalchemy.exc import IntegrityError

from mealie.routes._base.base_controllers import BaseUserController
from mealie.routes._base.controller import controller
from mealie.schema.household.household_food_inventory import (
    HouseholdFoodInventoryOut,
    HouseholdFoodInventoryPagination,
    HouseholdFoodInventorySave,
    HouseholdFoodInventoryUpdate,
    RecipeStockComparison,
)
from mealie.schema.response import ErrorResponse
from mealie.schema.response.pagination import PaginationQuery
from mealie.services.household_services.recipe_stock_comparison import RecipeStockComparisonService

router = APIRouter(prefix="/households/self/food-inventory", tags=["Households: Food Inventory"])


@controller(router)
class HouseholdFoodInventoryController(BaseUserController):
    @cached_property
    def repo(self):
        return self.repos.household_food_inventory

    @router.get("", response_model=HouseholdFoodInventoryPagination)
    def get_all(self, q: PaginationQuery = Depends(PaginationQuery)):
        """Return the food inventory for the authenticated user's household.

        Any authenticated member of the household may read. The response is scoped to the
        user's household by the repository; there is no way to read another household's data.
        """
        response = self.repo.page_all(
            pagination=q,
            override=HouseholdFoodInventoryOut,
        )
        response.set_pagination_guides(router.url_path_for("get_all"), q.model_dump())
        return response

    def _validate_unit(self, unit_id: UUID4 | None) -> None:
        """Ensure a non-null unit belongs to the user's group (group-scoped repo -> None if not)."""
        if unit_id is None:
            return
        if self.repos.ingredient_units.get_one(unit_id) is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse.respond(message="Unit not found."),
            )

    @router.put("/{food_id}", response_model=HouseholdFoodInventoryOut)
    def upsert_one(self, food_id: UUID4, data: HouseholdFoodInventoryUpdate):
        """Create or update the stock quantity (and optional unit) for a food in the household.

        - group_id and household_id are derived from the authenticated user, never the body.
        - food_id comes from the path and must belong to the user's group.
        - unit_id is optional: omitted preserves the existing unit, an explicit value sets it
          (validated against the group), and explicit null clears it (count-based stock).
        - Any authenticated member of the household may modify (no organize/manage permission).
        """
        # Validate the food belongs to the user's group (group-scoped repo -> None if it doesn't).
        food = self.repos.ingredient_foods.get_one(food_id)
        if food is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse.respond(message="Food not found."),
            )

        unit_provided = "unit_id" in data.model_fields_set
        if unit_provided:
            self._validate_unit(data.unit_id)

        existing = self.repo.get_one(food_id, key="food_id")
        if existing is not None:
            update_data: dict = {"quantity": data.quantity}
            # Preserve the stored unit when the caller didn't send unit_id.
            update_data["unit_id"] = data.unit_id if unit_provided else existing.unit_id
            return self.repo.update(existing.id, update_data)

        save = HouseholdFoodInventorySave(
            group_id=self.group_id,
            household_id=self.household_id,
            food_id=food_id,
            quantity=data.quantity,
            unit_id=data.unit_id if unit_provided else None,
        )

        try:
            return self.repo.create(save)
        except IntegrityError:
            # A concurrent request created the row between our get_one and create. The session
            # is in an errored state, so it must be rolled back before we can query again.
            # This is compatible with both SQLite and PostgreSQL (no ON CONFLICT).
            self.repo.session.rollback()
            existing = self.repo.get_one(food_id, key="food_id")
            if existing is None:
                # Shouldn't happen: unique violation implies a row exists for (household, food).
                raise
            update_data = {"quantity": data.quantity}
            update_data["unit_id"] = data.unit_id if unit_provided else existing.unit_id
            return self.repo.update(existing.id, update_data)

    @router.get("/recipe/{recipe_id}/comparison", response_model=RecipeStockComparison)
    def recipe_comparison(self, recipe_id: UUID4, scale: float = 1):
        """Compare a recipe's ingredients against the authenticated household's stock.

        Returns needed/have/missing per food, expressed in the recipe ingredient's unit when the
        comparison is possible. `scale` mirrors the shopping-list recipe increment: needs are
        multiplied by it so a scaled recipe requires proportionally more stock.

        Household and group are derived from the authenticated user. Read-only; nothing is mutated.
        """
        if scale <= 0:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ErrorResponse.respond(message="scale must be greater than 0."),
            )
        try:
            return RecipeStockComparisonService(self.repos).compare(recipe_id, scale)
        except ValueError as e:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse.respond(message="Recipe not found."),
            ) from e
