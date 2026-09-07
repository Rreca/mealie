"""Integration tests for the Phase 1 HouseholdFoodInventory feature.

Covers: create/update via PUT upsert, persistence, validation (negative / NaN / infinity,
strict body), food-from-another-group -> 404, cross-household isolation (read + write),
and that a plain household member (no can_organize / can_manage_household) can read and write.
"""

import pytest
from fastapi.testclient import TestClient

from mealie.schema.recipe.recipe_ingredient import SaveIngredientFood
from tests.utils import api_routes
from tests.utils.factories import random_string
from tests.utils.fixture_schemas import TestUser


def _create_food(user: TestUser) -> str:
    food = user.repos.ingredient_foods.create(SaveIngredientFood(name=random_string(10), group_id=user.group_id))
    return str(food.id)


def _set_permissions(user: TestUser, *, can_organize: bool, can_manage_household: bool) -> None:
    db_user = user.repos.users.get_one(user.user_id)
    assert db_user
    db_user.can_organize = can_organize
    db_user.can_manage_household = can_manage_household
    user.repos.users.update(db_user.id, db_user)


def test_upsert_creates_inventory(api_client: TestClient, unique_user: TestUser):
    food_id = _create_food(unique_user)

    response = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 6},
        headers=unique_user.token,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["foodId"] == food_id
    assert body["quantity"] == 6
    assert body["householdId"] == unique_user.household_id


def test_second_upsert_updates_without_duplicate(api_client: TestClient, unique_user: TestUser):
    food_id = _create_food(unique_user)

    first = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 6},
        headers=unique_user.token,
    )
    assert first.status_code == 200
    first_id = first.json()["id"]

    second = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 10},
        headers=unique_user.token,
    )
    assert second.status_code == 200
    assert second.json()["id"] == first_id
    assert second.json()["quantity"] == 10

    # only one row exists for this food in the listing
    listing = api_client.get(
        api_routes.households_self_food_inventory,
        params={"perPage": -1},
        headers=unique_user.token,
    )
    assert listing.status_code == 200
    matching = [i for i in listing.json()["items"] if i["foodId"] == food_id]
    assert len(matching) == 1
    assert matching[0]["quantity"] == 10


def test_put_then_get_persists_value(api_client: TestClient, unique_user: TestUser):
    food_id = _create_food(unique_user)

    api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 6},
        headers=unique_user.token,
    )

    listing = api_client.get(
        api_routes.households_self_food_inventory,
        params={"perPage": -1},
        headers=unique_user.token,
    )
    assert listing.status_code == 200
    matching = [i for i in listing.json()["items"] if i["foodId"] == food_id]
    assert len(matching) == 1
    assert matching[0]["quantity"] == 6


def test_negative_quantity_rejected(api_client: TestClient, unique_user: TestUser):
    food_id = _create_food(unique_user)
    response = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": -1},
        headers=unique_user.token,
    )
    assert response.status_code == 422


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_quantity_rejected(api_client: TestClient, unique_user: TestUser, literal: str):
    food_id = _create_food(unique_user)
    # NaN/Infinity are not valid JSON per spec; send a raw body so the server (not the test client)
    # decides. Python's json accepts these literals, so this exercises the server rejecting them
    # rather than returning 500.
    headers = {**unique_user.token, "Content-Type": "application/json"}
    response = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        content=f'{{"quantity": {literal}}}',
        headers=headers,
    )
    assert response.status_code == 422


def test_extra_fields_rejected(api_client: TestClient, unique_user: TestUser):
    """Phase 1 body accepts only `quantity`. Extra fields must be rejected."""
    food_id = _create_food(unique_user)
    response = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 6, "unitId": None, "householdId": unique_user.household_id},
        headers=unique_user.token,
    )
    assert response.status_code == 422


def test_food_from_another_group_returns_404(api_client: TestClient, unique_user: TestUser, g2_user: TestUser):
    other_group_food_id = _create_food(g2_user)
    response = api_client.put(
        api_routes.households_self_food_inventory_food_id(other_group_food_id),
        json={"quantity": 6},
        headers=unique_user.token,
    )
    assert response.status_code == 404


def test_household_cannot_read_other_household_inventory(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser
):
    # unique_user sets stock for a (group-shared) food
    food_id = _create_food(unique_user)
    api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 6},
        headers=unique_user.token,
    )

    # h2_user (same group, different household) must not see it
    listing = api_client.get(
        api_routes.households_self_food_inventory,
        params={"perPage": -1},
        headers=h2_user.token,
    )
    assert listing.status_code == 200
    assert [i for i in listing.json()["items"] if i["foodId"] == food_id] == []


def test_household_cannot_modify_other_household_inventory(
    api_client: TestClient, unique_user: TestUser, h2_user: TestUser
):
    food_id = _create_food(unique_user)
    api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 6},
        headers=unique_user.token,
    )

    # h2_user writes to the same food: this creates/updates ITS OWN household row, never unique_user's
    resp = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 99},
        headers=h2_user.token,
    )
    assert resp.status_code == 200
    assert resp.json()["householdId"] == h2_user.household_id

    # unique_user's value is unchanged
    listing = api_client.get(
        api_routes.households_self_food_inventory,
        params={"perPage": -1},
        headers=unique_user.token,
    )
    matching = [i for i in listing.json()["items"] if i["foodId"] == food_id]
    assert len(matching) == 1
    assert matching[0]["quantity"] == 6


def test_plain_member_can_read_and_write(api_client: TestClient, unique_user_fn_scoped: TestUser):
    """A user without can_organize / can_manage_household can still read and modify stock."""
    user = unique_user_fn_scoped
    food_id = _create_food(user)
    _set_permissions(user, can_organize=False, can_manage_household=False)

    write = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 3},
        headers=user.token,
    )
    assert write.status_code == 200

    read = api_client.get(
        api_routes.households_self_food_inventory,
        params={"perPage": -1},
        headers=user.token,
    )
    assert read.status_code == 200
