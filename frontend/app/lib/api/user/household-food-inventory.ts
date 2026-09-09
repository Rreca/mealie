import { BaseAPI } from "../base/base-clients";
import { route } from "~/lib/api/base/route";
import type {
  HouseholdFoodInventoryOut,
  HouseholdFoodInventoryUpdate,
  RecipeStockComparison,
} from "~/lib/api/types/household";
import type { PaginationData } from "~/lib/api/types/non-generated";

const prefix = "/api";

const routes = {
  inventory: `${prefix}/households/self/food-inventory`,
  inventoryFood: (foodId: string) => `${prefix}/households/self/food-inventory/${foodId}`,
  recipeComparison: (recipeId: string) => `${prefix}/households/self/food-inventory/recipe/${recipeId}/comparison`,
};

export class HouseholdFoodInventoryAPI extends BaseAPI {
  /** Fetch the whole household inventory. Called with perPage=-1 by the UI to avoid false zeros. */
  async getAll(page = 1, perPage = -1) {
    return await this.requests.get<PaginationData<HouseholdFoodInventoryOut>>(
      route(routes.inventory, { page, perPage }),
    );
  }

  /** Create or update the stock quantity (and optional unit) for a food in the household. */
  async upsertByFoodId(foodId: string, payload: HouseholdFoodInventoryUpdate) {
    return await this.requests.put<HouseholdFoodInventoryOut, HouseholdFoodInventoryUpdate>(
      routes.inventoryFood(foodId),
      payload,
    );
  }

  /** Compare a recipe's ingredients against the household's stock (needed/have/missing). */
  async getRecipeComparison(recipeId: string, scale = 1) {
    return await this.requests.get<RecipeStockComparison>(
      route(routes.recipeComparison(recipeId), { scale }),
    );
  }
}
