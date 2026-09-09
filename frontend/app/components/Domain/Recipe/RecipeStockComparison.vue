<template>
  <div v-if="rows.length > 0">
    <h2 class="mt-4 text-h5 font-weight-medium opacity-80">
      {{ $t("recipe.stock-comparison-title") }}
    </h2>
    <v-list density="compact">
      <v-list-item
        v-for="row in rows"
        :key="row.foodId"
        density="compact"
        class="px-1"
      >
        <v-list-item-title>{{ row.foodName }}</v-list-item-title>
        <v-list-item-subtitle>
          <template v-if="row.unitConflict">
            <span class="text-warning">
              {{ $t("recipe.stock-unit-conflict") }}
            </span>
            &nbsp;
            {{ $t("recipe.stock-needed") }}: {{ formatQty(row.needed) }} {{ row.unitName }}
            &middot;
            {{ $t("recipe.stock-have") }}: {{ formatQty(row.have) }} {{ row.haveUnitName }}
          </template>
          <template v-else>
            {{ $t("recipe.stock-needed") }}: {{ formatQty(row.needed) }} {{ row.unitName }}
            &middot;
            {{ $t("recipe.stock-have") }}: {{ formatQty(row.have) }} {{ row.unitName }}
            &middot;
            <span v-if="row.missing > 0" class="text-error font-weight-medium">
              {{ $t("recipe.stock-missing") }}: {{ formatQty(row.missing) }} {{ row.unitName }}
            </span>
            <span v-else class="text-success font-weight-medium">
              {{ $t("recipe.stock-sufficient") }}
            </span>
          </template>
        </v-list-item-subtitle>
      </v-list-item>
    </v-list>
  </div>
</template>

<script setup lang="ts">
import { useUserApi } from "~/composables/api";
import type { RecipeStockComparisonItem } from "~/lib/api/types/household";

interface Props {
  recipeId: string;
  scale?: number;
}
const props = withDefaults(defineProps<Props>(), {
  scale: 1,
});

interface ComparisonRow {
  foodId: string;
  foodName: string;
  unitName: string;
  haveUnitName: string;
  needed: number;
  have: number;
  missing: number;
  unitConflict: boolean;
}

const api = useUserApi();
const items = ref<RecipeStockComparisonItem[]>([]);

function unitLabel(unit: RecipeStockComparisonItem["unit"]): string {
  return unit?.name || "";
}

// Only foods (with a stock comparison) are shown; foodless ingredients are skipped here.
const rows = computed<ComparisonRow[]>(() =>
  items.value
    .filter(item => item.food && !item.noFood)
    .map(item => ({
      foodId: item.food!.id,
      foodName: item.food!.name,
      unitName: unitLabel(item.unit),
      haveUnitName: unitLabel(item.haveUnit),
      needed: item.needed ?? 0,
      have: item.have ?? 0,
      missing: item.missing ?? 0,
      unitConflict: item.unitConflict ?? false,
    })),
);

function formatQty(value: number): string {
  // Trim trailing zeros for a clean display (e.g. 0.5 not 0.500000).
  return Number.parseFloat(value.toFixed(4)).toString();
}

async function load() {
  if (!props.recipeId) {
    return;
  }
  const { data } = await api.foodInventory.getRecipeComparison(props.recipeId, props.scale);
  items.value = data?.items ?? [];
}

onMounted(load);
watch(() => [props.recipeId, props.scale], load);
</script>
