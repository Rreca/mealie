import { computeMissingItem, shouldSkipForMissing } from "../use-stock-missing";
import type { RecipeStockComparisonItem } from "~/lib/api/types/household";

function item(partial: Partial<RecipeStockComparisonItem>): RecipeStockComparisonItem {
  return {
    needed: 0,
    have: 0,
    missing: 0,
    comparable: true,
    noFood: false,
    unitConflict: false,
    ...partial,
  };
}

describe("computeMissingItem (add only missing)", () => {
  test("no food id -> include full scaled quantity", () => {
    expect(computeMissingItem(null, undefined, 6, 1)).toEqual({ include: true, quantity: 6 });
  });

  test("no comparison row -> include full scaled quantity", () => {
    expect(computeMissingItem("food-1", undefined, 6, 2)).toEqual({ include: true, quantity: 12 });
  });

  test("unit conflict -> include full scaled quantity (no subtraction)", () => {
    const r = computeMissingItem("food-1", item({ unitConflict: true, missing: 500 }), 500, 1);
    expect(r).toEqual({ include: true, quantity: 500 });
  });

  test("not comparable -> include full scaled quantity", () => {
    const r = computeMissingItem("food-1", item({ comparable: false, missing: 500 }), 500, 1);
    expect(r).toEqual({ include: true, quantity: 500 });
  });

  test("fully covered (missing 0) -> not included", () => {
    const r = computeMissingItem("food-1", item({ missing: 0 }), 6, 1);
    expect(r).toEqual({ include: false, quantity: 0 });
  });

  test("short -> include the missing amount (already scaled)", () => {
    const r = computeMissingItem("food-1", item({ missing: 4 }), 6, 1);
    expect(r).toEqual({ include: true, quantity: 4 });
  });

  // --- Mandatory cases from the Option D spec (on-hand is irrelevant to this flow) ---

  test("eggs: recipe 6, stock 2 -> add 4", () => {
    const r = computeMissingItem("eggs", item({ needed: 6, have: 2, missing: 4 }), 6, 1);
    expect(r).toEqual({ include: true, quantity: 4 });
  });

  test("eggs: recipe 6, stock 6 -> add 0 (not included)", () => {
    const r = computeMissingItem("eggs", item({ needed: 6, have: 6, missing: 0 }), 6, 1);
    expect(r).toEqual({ include: false, quantity: 0 });
  });

  test("eggs: recipe 6, stock 0 -> add 6", () => {
    const r = computeMissingItem("eggs", item({ needed: 6, have: 0, missing: 6 }), 6, 1);
    expect(r).toEqual({ include: true, quantity: 6 });
  });
});

describe("shouldSkipForMissing (Option D bug: on-hand foods must not be skipped)", () => {
  test("food that is on-hand (checked=false) is NOT skipped -> stock decides", () => {
    // This is the exact bug: on-hand foods arrive unchecked; the missing flow must still consider
    // them (and add the shortfall). Must be false (do not skip).
    expect(shouldSkipForMissing("eggs", false)).toBe(false);
  });

  test("food that is checked is NOT skipped", () => {
    expect(shouldSkipForMissing("eggs", true)).toBe(false);
  });

  test("foodless ingredient that is unchecked IS skipped (respect manual uncheck)", () => {
    expect(shouldSkipForMissing(null, false)).toBe(true);
    expect(shouldSkipForMissing(undefined, false)).toBe(true);
  });

  test("foodless ingredient that is checked is NOT skipped", () => {
    expect(shouldSkipForMissing(null, true)).toBe(false);
  });
});
