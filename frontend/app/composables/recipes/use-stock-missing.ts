import type { RecipeStockComparisonItem } from "~/lib/api/types/household";

export interface MissingItemResult {
  /** Whether this ingredient should be added to the shopping list at all. */
  include: boolean;
  /**
   * The quantity to add, in the recipe's unit. Only meaningful when `include` is true. This is
   * the final amount (the "add only missing" flow creates items directly, so it is NOT
   * re-multiplied by the recipe scale).
   */
  quantity: number;
}

/**
 * Decide, for one recipe ingredient, whether "add only missing" should add it and how much,
 * given the backend comparison row for its food and the recipe scale.
 *
 * Rules (HouseholdFoodInventory is the source of truth here; on-hand is intentionally ignored
 * because this flow creates items directly and bypasses the legacy on-hand skip):
 * - no food id / no comparison row / unit conflict / not comparable
 *     -> include at the full scaled recipe quantity (can't safely subtract).
 * - fully covered (missing <= 0) -> do not include.
 * - short (missing > 0)          -> include exactly the missing amount (already scaled).
 */
/**
 * Whether "add only missing" should skip an ingredient outright, before consulting stock.
 *
 * For foods, we must NOT skip based on `checked`: on-hand foods are initialised as unchecked for
 * the legacy flow, and honoring that here would drop exactly the on-hand-but-short foods this
 * button exists to add (the Option D bug). Stock is the source of truth for foods.
 *
 * For foodless ingredients there is no stock to compare against, so the user's manual unchecking
 * is respected.
 */
export function shouldSkipForMissing(foodId: string | null | undefined, checked: boolean): boolean {
  return !foodId && !checked;
}

export function computeMissingItem(
  foodId: string | null | undefined,
  item: RecipeStockComparisonItem | undefined,
  recipeQuantity: number,
  scale: number,
): MissingItemResult {
  const fullQuantity = (recipeQuantity || 0) * (scale || 1);

  if (!foodId || !item || item.unitConflict || item.comparable === false) {
    return { include: true, quantity: fullQuantity };
  }

  const missing = item.missing ?? 0;
  if (missing <= 0) {
    return { include: false, quantity: 0 };
  }

  return { include: true, quantity: missing };
}
