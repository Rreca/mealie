import { BaseAPI } from "../base/base-clients";
import { route } from "~/lib/api/base/route";
import type { HouseholdFoodInventoryOut, HouseholdFoodInventoryUpdate } from "~/lib/api/types/household";
import type { PaginationData } from "~/lib/api/types/non-generated";

const prefix = "/api";

const routes = {
  inventory: `${prefix}/households/self/food-inventory`,
  inventoryFood: (foodId: string) => `${prefix}/households/self/food-inventory/${foodId}`,
};

export class HouseholdFoodInventoryAPI extends BaseAPI {
  /** Fetch the whole household inventory. Called with perPage=-1 by the UI to avoid false zeros. */
  async getAll(page = 1, perPage = -1) {
    return await this.requests.get<PaginationData<HouseholdFoodInventoryOut>>(
      route(routes.inventory, { page, perPage }),
    );
  }

  /** Create or update the stock quantity for a food in the current user's household. */
  async upsertByFoodId(foodId: string, payload: HouseholdFoodInventoryUpdate) {
    return await this.requests.put<HouseholdFoodInventoryOut, HouseholdFoodInventoryUpdate>(
      routes.inventoryFood(foodId),
      payload,
    );
  }
}
