from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .._model_base import BaseMixins, FilterableColumn, SqlAlchemyBase
from .._model_utils.auto_init import auto_init
from .._model_utils.guid import GUID

if TYPE_CHECKING:
    from ..group import Group
    from ..recipe.ingredient import IngredientFoodModel, IngredientUnitModel
    from .household import Household


class HouseholdFoodInventory(SqlAlchemyBase, BaseMixins):
    """Per-household stock quantity for a group-scoped IngredientFood.

    A single row represents the current stock a given household has of a given food.
    There is at most one row per (household_id, food_id) pair.
    """

    __tablename__ = "household_food_inventory"
    __table_args__ = (
        UniqueConstraint("household_id", "food_id", name="household_food_inventory_household_id_food_id_key"),
        CheckConstraint("quantity >= 0", name="household_food_inventory_quantity_non_negative"),
    )

    id: FilterableColumn[GUID] = mapped_column(GUID, primary_key=True, default=GUID.generate)

    # ID Relationships. group_id and household_id are always derived from the authenticated
    # user in the application layer; they are never accepted from the request body.
    group_id: FilterableColumn[GUID] = mapped_column(GUID, ForeignKey("groups.id"), nullable=False, index=True)
    group: Mapped["Group"] = relationship("Group", foreign_keys=[group_id])

    household_id: FilterableColumn[GUID] = mapped_column(
        GUID, ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    household: Mapped["Household"] = relationship("Household", foreign_keys=[household_id])

    food_id: FilterableColumn[GUID] = mapped_column(
        GUID, ForeignKey("ingredient_foods.id", ondelete="CASCADE"), nullable=False, index=True
    )
    food: Mapped["IngredientFoodModel"] = relationship(
        "IngredientFoodModel", foreign_keys=[food_id], back_populates="inventory_items"
    )

    quantity: FilterableColumn[float] = mapped_column(Float, nullable=False, default=0)

    # unit_id exists as a nullable column so a canonical unit can be assigned in a later phase.
    # It is NOT accepted in the Phase 1 request and MUST NOT be interpreted as a convertible unit.
    unit_id: FilterableColumn[GUID | None] = mapped_column(
        GUID, ForeignKey("ingredient_units.id", ondelete="SET NULL"), nullable=True, index=True
    )
    unit: Mapped["IngredientUnitModel | None"] = relationship("IngredientUnitModel", foreign_keys=[unit_id])

    @auto_init()
    def __init__(self, **_) -> None:
        pass
