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


def _create_unit(user: TestUser) -> str:
    from mealie.schema.recipe.recipe_ingredient import SaveIngredientUnit

    unit = user.repos.ingredient_units.create(SaveIngredientUnit(name=random_string(10), group_id=user.group_id))
    return str(unit.id)


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


# ============================================================
# Phase 2: unit_id editable on the inventory upsert


def test_upsert_saves_unit_id(api_client: TestClient, unique_user: TestUser):
    food_id = _create_food(unique_user)
    unit_id = _create_unit(unique_user)

    response = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 500, "unitId": unit_id},
        headers=unique_user.token,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["quantity"] == 500
    assert body["unitId"] == unit_id


def test_upsert_omitting_unit_preserves_existing_unit(api_client: TestClient, unique_user: TestUser):
    """A PUT with only quantity must not wipe a previously-saved unit."""
    food_id = _create_food(unique_user)
    unit_id = _create_unit(unique_user)

    # First set quantity + unit
    api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 500, "unitId": unit_id},
        headers=unique_user.token,
    )

    # Now update only the quantity; unit must be preserved
    response = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 750},
        headers=unique_user.token,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["quantity"] == 750
    assert body["unitId"] == unit_id


def test_upsert_explicit_null_unit_clears_it(api_client: TestClient, unique_user: TestUser):
    food_id = _create_food(unique_user)
    unit_id = _create_unit(unique_user)

    api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 500, "unitId": unit_id},
        headers=unique_user.token,
    )

    response = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 6, "unitId": None},
        headers=unique_user.token,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["quantity"] == 6
    assert body["unitId"] is None


def test_upsert_unit_from_another_group_returns_404(api_client: TestClient, unique_user: TestUser, g2_user: TestUser):
    food_id = _create_food(unique_user)
    other_group_unit_id = _create_unit(g2_user)

    response = api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 500, "unitId": other_group_unit_id},
        headers=unique_user.token,
    )
    assert response.status_code == 404


# ============================================================
# Phase 2: recipe vs stock comparison endpoint


def _create_recipe_with_eggs(user: TestUser, egg_food_id, qty=6):
    from uuid import UUID

    from mealie.schema.recipe.recipe import Recipe
    from mealie.schema.recipe.recipe_ingredient import RecipeIngredient

    food = user.repos.ingredient_foods.get_one(egg_food_id)
    recipe = user.repos.recipes.create(
        Recipe(
            name=random_string(12),
            user_id=user.user_id,
            group_id=UUID(user.group_id),
            recipe_ingredient=[RecipeIngredient(quantity=qty, food=food)],  # type: ignore
        )  # type: ignore
    )
    return str(recipe.id)


def test_recipe_comparison_endpoint(api_client: TestClient, unique_user: TestUser):
    food_id = _create_food(unique_user)
    api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 2},
        headers=unique_user.token,
    )
    recipe_id = _create_recipe_with_eggs(unique_user, food_id, qty=6)

    response = api_client.get(
        api_routes.households_self_food_inventory_recipe_comparison(recipe_id),
        headers=unique_user.token,
    )
    assert response.status_code == 200
    body = response.json()
    items = [i for i in body["items"] if i["food"] and i["food"]["id"] == food_id]
    assert len(items) == 1
    assert items[0]["needed"] == 6
    assert items[0]["have"] == 2
    assert items[0]["missing"] == 4


def test_recipe_comparison_scale(api_client: TestClient, unique_user: TestUser):
    food_id = _create_food(unique_user)
    api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 2},
        headers=unique_user.token,
    )
    recipe_id = _create_recipe_with_eggs(unique_user, food_id, qty=6)

    response = api_client.get(
        api_routes.households_self_food_inventory_recipe_comparison(recipe_id),
        params={"scale": 2},
        headers=unique_user.token,
    )
    assert response.status_code == 200
    body = response.json()
    items = [i for i in body["items"] if i["food"] and i["food"]["id"] == food_id]
    assert items[0]["needed"] == 12
    assert items[0]["missing"] == 10


def test_recipe_comparison_recipe_not_found(api_client: TestClient, unique_user: TestUser):
    import uuid

    response = api_client.get(
        api_routes.households_self_food_inventory_recipe_comparison(str(uuid.uuid4())),
        headers=unique_user.token,
    )
    assert response.status_code == 404


def test_recipe_comparison_uses_callers_household(api_client: TestClient, unique_user: TestUser, h2_user: TestUser):
    """Each household sees the comparison against its OWN stock for the same recipe."""
    food_id = _create_food(unique_user)
    recipe_id = _create_recipe_with_eggs(unique_user, food_id, qty=6)

    # unique_user stocks 2
    api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 2},
        headers=unique_user.token,
    )
    # h2_user (same group, different household) stocks 5
    api_client.put(
        api_routes.households_self_food_inventory_food_id(food_id),
        json={"quantity": 5},
        headers=h2_user.token,
    )

    r1 = api_client.get(
        api_routes.households_self_food_inventory_recipe_comparison(recipe_id),
        headers=unique_user.token,
    ).json()
    r2 = api_client.get(
        api_routes.households_self_food_inventory_recipe_comparison(recipe_id),
        headers=h2_user.token,
    ).json()

    i1 = [i for i in r1["items"] if i["food"] and i["food"]["id"] == food_id][0]
    i2 = [i for i in r2["items"] if i["food"] and i["food"]["id"] == food_id][0]
    assert i1["have"] == 2 and i1["missing"] == 4
    assert i2["have"] == 5 and i2["missing"] == 1


def test_recipe_comparison_plain_member_allowed(api_client: TestClient, unique_user_fn_scoped: TestUser):
    user = unique_user_fn_scoped
    _set_permissions(user, can_organize=False, can_manage_household=False)
    food_id = _create_food(user)
    recipe_id = _create_recipe_with_eggs(user, food_id, qty=3)

    response = api_client.get(
        api_routes.households_self_food_inventory_recipe_comparison(recipe_id),
        headers=user.token,
    )
    assert response.status_code == 200


# ============================================================
# Phase 2: mandatory end-to-end example (comparison + add only missing)


def test_mandatory_example_comparison_and_add_only_missing(api_client: TestClient, unique_user: TestUser):
    """Stock: eggs 2, flour 500 g, milk 2 L. Recipe: eggs 6, flour 1 kg, milk 1 L.

    Comparison -> eggs missing 4, flour missing 0.5 kg, milk sufficient.
    "Add only missing" -> shopping list gets 4 eggs + 0.5 kg flour, no milk.
    """
    from uuid import UUID

    from mealie.schema.household.group_shopping_list import ShoppingListSave
    from mealie.schema.recipe.recipe import Recipe
    from mealie.schema.recipe.recipe_ingredient import (
        RecipeIngredient,
        SaveIngredientFood,
        SaveIngredientUnit,
        StandardizedUnitType,
    )

    repos = unique_user.repos

    eggs = repos.ingredient_foods.create(SaveIngredientFood(name=random_string(10), group_id=unique_user.group_id))
    flour = repos.ingredient_foods.create(SaveIngredientFood(name=random_string(10), group_id=unique_user.group_id))
    milk = repos.ingredient_foods.create(SaveIngredientFood(name=random_string(10), group_id=unique_user.group_id))

    gram = repos.ingredient_units.create(
        SaveIngredientUnit(
            name=random_string(10),
            group_id=unique_user.group_id,
            standard_quantity=1,
            standard_unit=StandardizedUnitType.GRAM,
        )
    )
    kilogram = repos.ingredient_units.create(
        SaveIngredientUnit(
            name=random_string(10),
            group_id=unique_user.group_id,
            standard_quantity=1000,
            standard_unit=StandardizedUnitType.GRAM,
        )
    )
    liter = repos.ingredient_units.create(
        SaveIngredientUnit(
            name=random_string(10),
            group_id=unique_user.group_id,
            standard_quantity=1,
            standard_unit=StandardizedUnitType.LITER,
        )
    )

    recipe = repos.recipes.create(
        Recipe(
            name=random_string(12),
            user_id=unique_user.user_id,
            group_id=UUID(unique_user.group_id),
            recipe_ingredient=[
                RecipeIngredient(quantity=6, food=eggs),  # type: ignore
                RecipeIngredient(quantity=1, unit=kilogram, food=flour),  # type: ignore
                RecipeIngredient(quantity=1, unit=liter, food=milk),  # type: ignore
            ],
        )  # type: ignore
    )

    # Set stock via the API (eggs count-based, flour in g, milk in L)
    api_client.put(
        api_routes.households_self_food_inventory_food_id(str(eggs.id)),
        json={"quantity": 2},
        headers=unique_user.token,
    )
    api_client.put(
        api_routes.households_self_food_inventory_food_id(str(flour.id)),
        json={"quantity": 500, "unitId": str(gram.id)},
        headers=unique_user.token,
    )
    api_client.put(
        api_routes.households_self_food_inventory_food_id(str(milk.id)),
        json={"quantity": 2, "unitId": str(liter.id)},
        headers=unique_user.token,
    )

    # --- Comparison ---
    comparison = api_client.get(
        api_routes.households_self_food_inventory_recipe_comparison(str(recipe.id)),
        headers=unique_user.token,
    ).json()
    by_food = {i["food"]["id"]: i for i in comparison["items"] if i["food"]}

    assert by_food[str(eggs.id)]["missing"] == 4
    assert abs(by_food[str(flour.id)]["missing"] - 0.5) < 1e-9
    assert by_food[str(milk.id)]["missing"] == 0

    # --- Add only missing to a shopping list ---
    shopping_list = repos.group_shopping_lists.create(
        ShoppingListSave(name=random_string(10), group_id=unique_user.group_id, user_id=unique_user.user_id)
    )

    # This mirrors what the frontend "add only missing" does: send the FULL RecipeIngredient
    # objects (with their food/unit), keeping only the short foods and setting quantity to the
    # missing amount (scale = 1 here, so no division needed). The backend reads ingredient.food,
    # so we must send complete ingredient objects, exactly like the dialog does.
    full_recipe = api_client.get(api_routes.recipes_slug(recipe.slug), headers=unique_user.token).json()
    ingredients_by_food = {ing["food"]["id"]: ing for ing in full_recipe["recipeIngredient"] if ing.get("food")}

    egg_ing = ingredients_by_food[str(eggs.id)]
    egg_ing["quantity"] = 4
    flour_ing = ingredients_by_food[str(flour.id)]
    flour_ing["quantity"] = 0.5
    # milk omitted (sufficient stock)
    missing_ingredients = [egg_ing, flour_ing]

    payload = [
        {
            "recipeId": str(recipe.id),
            "recipeIncrementQuantity": 1,
            "recipeIngredients": missing_ingredients,
        }
    ]

    resp = api_client.post(
        api_routes.households_shopping_lists_item_id_recipe(str(shopping_list.id)),
        json=payload,
        headers=unique_user.token,
    )
    assert resp.status_code == 200

    updated = repos.group_shopping_lists.get_one(shopping_list.id)
    assert updated is not None
    items_by_food = {str(i.food_id): i for i in updated.list_items if i.food_id}

    # eggs: 4, flour: 0.5 (kg), milk: absent
    assert str(eggs.id) in items_by_food
    assert items_by_food[str(eggs.id)].quantity == 4
    assert str(flour.id) in items_by_food
    assert abs(items_by_food[str(flour.id)].quantity - 0.5) < 1e-9
    assert str(milk.id) not in items_by_food


# ============================================================
# Phase 2 Option D: on-hand legacy preserved, "add only missing" bypasses it


def _mark_food_on_hand(user: TestUser, food_id) -> None:
    """Flag a food as in-possession/on-hand for the user's household (legacy mechanism)."""
    from mealie.schema.recipe.recipe_ingredient import SaveIngredientFood

    household = user.repos.households.get_one(user.household_id)
    assert household
    food = user.repos.ingredient_foods.get_one(food_id)
    assert food
    user.repos.ingredient_foods.update(
        food_id,
        SaveIngredientFood(
            id=food.id,
            name=food.name,
            group_id=user.group_id,
            households_with_ingredient_food=[household.slug],
        ),
    )


def test_option_d_legacy_add_recipe_skips_on_hand(api_client: TestClient, unique_user: TestUser):
    """Legacy 'add recipe' must keep skipping an on-hand food (unchanged Mealie behavior)."""
    from uuid import UUID

    from mealie.schema.household.group_shopping_list import ShoppingListSave
    from mealie.schema.recipe.recipe import Recipe
    from mealie.schema.recipe.recipe_ingredient import RecipeIngredient

    repos = unique_user.repos
    eggs = repos.ingredient_foods.get_one(_create_food(unique_user))
    _mark_food_on_hand(unique_user, eggs.id)

    recipe = repos.recipes.create(
        Recipe(
            name=random_string(12),
            user_id=unique_user.user_id,
            group_id=UUID(unique_user.group_id),
            recipe_ingredient=[RecipeIngredient(quantity=6, food=eggs)],  # type: ignore
        )  # type: ignore
    )
    shopping_list = repos.group_shopping_lists.create(
        ShoppingListSave(name=random_string(10), group_id=unique_user.group_id, user_id=unique_user.user_id)
    )

    full_recipe = api_client.get(api_routes.recipes_slug(recipe.slug), headers=unique_user.token).json()
    payload = [
        {
            "recipeId": str(recipe.id),
            "recipeIncrementQuantity": 1,
            "recipeIngredients": full_recipe["recipeIngredient"],
        }
    ]
    resp = api_client.post(
        api_routes.households_shopping_lists_item_id_recipe(str(shopping_list.id)),
        json=payload,
        headers=unique_user.token,
    )
    assert resp.status_code == 200

    updated = repos.group_shopping_lists.get_one(shopping_list.id)
    # legacy behavior: on-hand food is skipped -> the list has no egg item
    assert [i for i in updated.list_items if str(i.food_id) == str(eggs.id)] == []


def _add_only_missing_via_bulk(user: TestUser, api_client, recipe, shopping_list_id, scale=1):
    """Mirror the frontend "add only missing": compare, then create items directly (bypassing
    the recipe endpoint and its on-hand skip). Returns the created items response."""
    comparison = api_client.get(
        api_routes.households_self_food_inventory_recipe_comparison(str(recipe.id)),
        params={"scale": scale},
        headers=user.token,
    ).json()
    by_food = {i["food"]["id"]: i for i in comparison["items"] if i.get("food")}

    full_recipe = api_client.get(api_routes.recipes_slug(recipe.slug), headers=user.token).json()
    items = []
    for ing in full_recipe["recipeIngredient"]:
        food = ing.get("food")
        if not food:
            continue
        item = by_food.get(food["id"])
        if not item:
            continue
        if item.get("unitConflict") or item.get("comparable") is False:
            qty = (ing.get("quantity") or 0) * scale
        else:
            missing = item.get("missing") or 0
            if missing <= 0:
                continue
            qty = missing
        items.append(
            {
                "shoppingListId": str(shopping_list_id),
                "quantity": qty,
                "note": ing.get("note") or "",
                "foodId": food["id"],
                "unitId": (ing.get("unit") or {}).get("id"),
            }
        )

    return api_client.post(api_routes.households_shopping_items_create_bulk, json=items, headers=user.token)


def test_option_d_add_only_missing_adds_missing_even_when_on_hand(
    api_client: TestClient, unique_user: TestUser
):
    """Recipe 6 eggs, stock 2, on-hand true -> add only missing must add 4 (ignores on-hand)."""
    from uuid import UUID

    from mealie.schema.household.group_shopping_list import ShoppingListSave
    from mealie.schema.recipe.recipe import Recipe
    from mealie.schema.recipe.recipe_ingredient import RecipeIngredient

    repos = unique_user.repos
    eggs = repos.ingredient_foods.get_one(_create_food(unique_user))
    _mark_food_on_hand(unique_user, eggs.id)
    api_client.put(
        api_routes.households_self_food_inventory_food_id(str(eggs.id)),
        json={"quantity": 2}, headers=unique_user.token,
    )
    recipe = repos.recipes.create(
        Recipe(
            name=random_string(12),
            user_id=unique_user.user_id,
            group_id=UUID(unique_user.group_id),
            recipe_ingredient=[RecipeIngredient(quantity=6, food=eggs)],  # type: ignore
        )  # type: ignore
    )
    shopping_list = repos.group_shopping_lists.create(
        ShoppingListSave(name=random_string(10), group_id=unique_user.group_id, user_id=unique_user.user_id)
    )

    resp = _add_only_missing_via_bulk(unique_user, api_client, recipe, shopping_list.id)
    assert resp.status_code == 201

    updated = repos.group_shopping_lists.get_one(shopping_list.id)
    egg_items = [i for i in updated.list_items if str(i.food_id) == str(eggs.id)]
    assert len(egg_items) == 1
    assert egg_items[0].quantity == 4  # on-hand ignored; stock is the source of truth


def test_option_d_add_only_missing_full_stock_adds_nothing(api_client: TestClient, unique_user: TestUser):
    """Stock 6, on-hand true -> add only missing adds 0 (fully covered)."""
    from uuid import UUID

    from mealie.schema.household.group_shopping_list import ShoppingListSave
    from mealie.schema.recipe.recipe import Recipe
    from mealie.schema.recipe.recipe_ingredient import RecipeIngredient

    repos = unique_user.repos
    eggs = repos.ingredient_foods.get_one(_create_food(unique_user))
    _mark_food_on_hand(unique_user, eggs.id)
    api_client.put(
        api_routes.households_self_food_inventory_food_id(str(eggs.id)),
        json={"quantity": 6}, headers=unique_user.token,
    )
    recipe = repos.recipes.create(
        Recipe(
            name=random_string(12),
            user_id=unique_user.user_id,
            group_id=UUID(unique_user.group_id),
            recipe_ingredient=[RecipeIngredient(quantity=6, food=eggs)],  # type: ignore
        )  # type: ignore
    )
    shopping_list = repos.group_shopping_lists.create(
        ShoppingListSave(name=random_string(10), group_id=unique_user.group_id, user_id=unique_user.user_id)
    )

    _add_only_missing_via_bulk(unique_user, api_client, recipe, shopping_list.id)

    updated = repos.group_shopping_lists.get_one(shopping_list.id)
    assert [i for i in updated.list_items if str(i.food_id) == str(eggs.id)] == []


def test_option_d_add_only_missing_zero_stock_adds_all(api_client: TestClient, unique_user: TestUser):
    """Stock 0, on-hand true -> add only missing adds the full 6."""
    from uuid import UUID

    from mealie.schema.household.group_shopping_list import ShoppingListSave
    from mealie.schema.recipe.recipe import Recipe
    from mealie.schema.recipe.recipe_ingredient import RecipeIngredient

    repos = unique_user.repos
    eggs = repos.ingredient_foods.get_one(_create_food(unique_user))
    _mark_food_on_hand(unique_user, eggs.id)
    api_client.put(
        api_routes.households_self_food_inventory_food_id(str(eggs.id)),
        json={"quantity": 0}, headers=unique_user.token,
    )
    recipe = repos.recipes.create(
        Recipe(
            name=random_string(12),
            user_id=unique_user.user_id,
            group_id=UUID(unique_user.group_id),
            recipe_ingredient=[RecipeIngredient(quantity=6, food=eggs)],  # type: ignore
        )  # type: ignore
    )
    shopping_list = repos.group_shopping_lists.create(
        ShoppingListSave(name=random_string(10), group_id=unique_user.group_id, user_id=unique_user.user_id)
    )

    _add_only_missing_via_bulk(unique_user, api_client, recipe, shopping_list.id)

    updated = repos.group_shopping_lists.get_one(shopping_list.id)
    egg_items = [i for i in updated.list_items if str(i.food_id) == str(eggs.id)]
    assert len(egg_items) == 1
    assert egg_items[0].quantity == 6
