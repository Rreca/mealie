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
)
from mealie.schema.response import ErrorResponse
from mealie.schema.response.pagination import PaginationQuery

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

    @router.put("/{food_id}", response_model=HouseholdFoodInventoryOut)
    def upsert_one(self, food_id: UUID4, data: HouseholdFoodInventoryUpdate):
        """Create or update the stock quantity for a food in the authenticated user's household.

        - group_id and household_id are derived from the authenticated user, never the body.
        - food_id comes from the path and must belong to the user's group.
        - Any authenticated member of the household may modify (no organize/manage permission).
        """
        # Validate the food belongs to the user's group (group-scoped repo -> None if it doesn't).
        food = self.repos.ingredient_foods.get_one(food_id)
        if food is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=ErrorResponse.respond(message="Food not found."),
            )

        existing = self.repo.get_one(food_id, key="food_id")
        if existing is not None:
            return self.repo.update(existing.id, {"quantity": data.quantity})

        save = HouseholdFoodInventorySave(
            group_id=self.group_id,
            household_id=self.household_id,
            food_id=food_id,
            quantity=data.quantity,
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
            return self.repo.update(existing.id, {"quantity": data.quantity})
