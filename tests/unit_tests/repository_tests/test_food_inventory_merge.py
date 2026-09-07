"""Unit tests for HouseholdFoodInventory consolidation during Food merge, plus the
upsert concurrency (IntegrityError) path.

Merge policy per household (no unit conversion, never silently discard a quantity):
- only source has a row            -> repoint to destination
- only destination has a row       -> keep it
- both, source quantity == 0       -> delete source, keep destination
- both, destination quantity == 0  -> copy source quantity+unit into destination, delete source
- both positive, same non-null unit_id -> sum
- both positive, any null unit_id  -> conflict (abort)
- both positive, different unit_id -> conflict (abort)
Conflicts abort the ENTIRE merge (no partial application across households).
"""

import pytest
from sqlalchemy.exc import IntegrityError

from mealie.core.exceptions import FoodInventoryUnitConflict
from mealie.schema.household.household_food_inventory import HouseholdFoodInventorySave
from mealie.schema.recipe.recipe_ingredient import SaveIngredientFood, SaveIngredientUnit
from tests.utils.factories import random_string
from tests.utils.fixture_schemas import TestUser


def _food(user: TestUser):
    return user.repos.ingredient_foods.create(SaveIngredientFood(name=random_string(10), group_id=user.group_id))


def _unit(user: TestUser):
    return user.repos.ingredient_units.create(SaveIngredientUnit(name=random_string(10), group_id=user.group_id))


def _inv(user: TestUser, food_id, quantity, unit_id=None):
    return user.repos.household_food_inventory.create(
        HouseholdFoodInventorySave(
            group_id=user.group_id,
            household_id=user.household_id,
            food_id=food_id,
            quantity=quantity,
            unit_id=unit_id,
        )
    )


def _get_inv_for_food(user: TestUser, food_id):
    return user.repos.household_food_inventory.get_one(food_id, key="food_id")


def test_merge_moves_inventory_when_only_source_has_it(unique_user: TestUser):
    db = unique_user.repos
    src = _food(unique_user)
    dst = _food(unique_user)
    _inv(unique_user, src.id, 5)

    db.ingredient_foods.merge(src.id, dst.id)

    moved = _get_inv_for_food(unique_user, dst.id)
    assert moved is not None
    assert moved.quantity == 5
    assert _get_inv_for_food(unique_user, src.id) is None


def test_merge_keeps_inventory_when_only_destination_has_it(unique_user: TestUser):
    db = unique_user.repos
    src = _food(unique_user)
    dst = _food(unique_user)
    _inv(unique_user, dst.id, 7)

    db.ingredient_foods.merge(src.id, dst.id)

    kept = _get_inv_for_food(unique_user, dst.id)
    assert kept is not None
    assert kept.quantity == 7


def test_merge_sums_when_both_have_same_unit(unique_user: TestUser):
    db = unique_user.repos
    unit = _unit(unique_user)
    src = _food(unique_user)
    dst = _food(unique_user)
    _inv(unique_user, src.id, 4, unit_id=unit.id)
    _inv(unique_user, dst.id, 6, unit_id=unit.id)

    db.ingredient_foods.merge(src.id, dst.id)

    merged = _get_inv_for_food(unique_user, dst.id)
    assert merged is not None
    assert merged.quantity == 10
    assert _get_inv_for_food(unique_user, src.id) is None


def test_merge_sums_when_both_null_unit_but_one_is_zero(unique_user: TestUser):
    """Both null unit but source is 0 -> keep destination positive value (no conflict)."""
    db = unique_user.repos
    src = _food(unique_user)
    dst = _food(unique_user)
    _inv(unique_user, src.id, 0)
    _inv(unique_user, dst.id, 8)

    db.ingredient_foods.merge(src.id, dst.id)

    kept = _get_inv_for_food(unique_user, dst.id)
    assert kept is not None
    assert kept.quantity == 8


def test_merge_copies_source_when_destination_zero(unique_user: TestUser):
    db = unique_user.repos
    unit = _unit(unique_user)
    src = _food(unique_user)
    dst = _food(unique_user)
    _inv(unique_user, src.id, 5, unit_id=unit.id)
    _inv(unique_user, dst.id, 0)

    db.ingredient_foods.merge(src.id, dst.id)

    result = _get_inv_for_food(unique_user, dst.id)
    assert result is not None
    assert result.quantity == 5
    assert result.unit_id == unit.id


def test_merge_conflicts_when_both_positive_null_units(unique_user: TestUser):
    db = unique_user.repos
    src = _food(unique_user)
    dst = _food(unique_user)
    _inv(unique_user, src.id, 3)  # null unit
    _inv(unique_user, dst.id, 2)  # null unit

    with pytest.raises(FoodInventoryUnitConflict):
        db.ingredient_foods.merge(src.id, dst.id)


def test_merge_conflicts_when_both_positive_different_units(unique_user: TestUser):
    db = unique_user.repos
    unit_a = _unit(unique_user)
    unit_b = _unit(unique_user)
    src = _food(unique_user)
    dst = _food(unique_user)
    _inv(unique_user, src.id, 3, unit_id=unit_a.id)
    _inv(unique_user, dst.id, 2, unit_id=unit_b.id)

    with pytest.raises(FoodInventoryUnitConflict):
        db.ingredient_foods.merge(src.id, dst.id)


def test_conflict_leaves_everything_unchanged(unique_user: TestUser):
    """If ANY household conflicts, no food and no inventory row is modified for any household.

    Uses a second household created in the same group (via the repo layer, same session) so we can
    seed inventory for two households and assert nothing changed after an aborted merge.
    """
    from uuid import UUID

    from mealie.repos.all_repositories import get_repositories

    db = unique_user.repos
    src = _food(unique_user)
    dst = _food(unique_user)

    # household 1 (unique_user): a clean, mergeable case (same-unit sum)
    unit = _unit(unique_user)
    _inv(unique_user, src.id, 4, unit_id=unit.id)
    _inv(unique_user, dst.id, 1, unit_id=unit.id)

    # Second household in the same group, scoped repo on the same session.
    household_2 = db.households.create({"name": random_string(), "group_id": UUID(unique_user.group_id)})
    h2_repos = get_repositories(db.session, group_id=UUID(unique_user.group_id), household_id=household_2.id)

    def _inv2(food_id, quantity, unit_id=None):
        return h2_repos.household_food_inventory.create(
            HouseholdFoodInventorySave(
                group_id=unique_user.group_id,
                household_id=household_2.id,
                food_id=food_id,
                quantity=quantity,
                unit_id=unit_id,
            )
        )

    # household 2: a conflicting case (both positive, null units)
    _inv2(src.id, 3)
    _inv2(dst.id, 2)

    with pytest.raises(FoodInventoryUnitConflict):
        db.ingredient_foods.merge(src.id, dst.id)

    # both foods still exist
    assert db.ingredient_foods.get_one(src.id) is not None
    assert db.ingredient_foods.get_one(dst.id) is not None

    # household 1 rows are untouched (not summed)
    h1_src = _get_inv_for_food(unique_user, src.id)
    h1_dst = _get_inv_for_food(unique_user, dst.id)
    assert h1_src is not None and h1_src.quantity == 4
    assert h1_dst is not None and h1_dst.quantity == 1

    # household 2 rows are untouched
    h2_src = h2_repos.household_food_inventory.get_one(src.id, key="food_id")
    h2_dst = h2_repos.household_food_inventory.get_one(dst.id, key="food_id")
    assert h2_src is not None and h2_src.quantity == 3
    assert h2_dst is not None and h2_dst.quantity == 2


def test_deleting_food_cascades_inventory(unique_user: TestUser):
    """Deleting a Food removes its HouseholdFoodInventory rows (CASCADE); no orphans remain."""
    db = unique_user.repos
    food = _food(unique_user)
    row = _inv(unique_user, food.id, 5)
    assert row is not None

    db.ingredient_foods.delete(food.id)

    assert _get_inv_for_food(unique_user, food.id) is None
    assert db.household_food_inventory.get_one(row.id) is None


def test_upsert_race_does_not_500(unique_user: TestUser):
    """Simulate the concurrent-create race: a row already exists, a second create raises
    IntegrityError, the session is rolled back and we recover by updating (no crash)."""
    db = unique_user.repos
    food = _food(unique_user)

    _inv(unique_user, food.id, 2)

    # A naive second create violates the unique (household_id, food_id) constraint.
    with pytest.raises(IntegrityError):
        _inv(unique_user, food.id, 9)

    # The session must be rolled back before it can be reused (mirrors controller behaviour).
    db.session.rollback()

    existing = _get_inv_for_food(unique_user, food.id)
    assert existing is not None
    updated = db.household_food_inventory.update(existing.id, {"quantity": 9})
    assert updated.quantity == 9
