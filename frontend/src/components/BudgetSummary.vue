<script setup lang="ts">
import type { Budget } from "../types/trip";
import { money } from "../utils/format";

defineProps<{ budget: Budget }>();
const estimateMoney = (value: number | null) =>
  value === null ? "未填写" : money(value);
</script>

<template>
  <section class="budget-panel" aria-label="旅行费用估算">
    <div class="budget-heading">
      <div>
        <h3>这趟旅程，大约花多少</h3>
        <p>
          {{ budget.travelers }} 人 · {{ budget.rooms }} 间房 ·
          {{ budget.nights }} 晚
        </p>
      </div>
      <div class="budget-total">
        <span>{{ budget.is_complete ? "四项估算合计" : "可计算项目小计" }}</span
        ><strong>{{ money(budget.total ?? budget.known_subtotal) }}</strong>
      </div>
    </div>
    <p v-if="!budget.is_complete" class="budget-warning">
      另有项目未填写，尚不能给出四项合计；未知费用不代表免费。
    </p>
    <table>
      <thead>
        <tr>
          <th scope="col">项目</th>
          <th scope="col">估算依据</th>
          <th scope="col">金额</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in budget.items" :key="item.category">
          <th scope="row">{{ item.label }}</th>
          <td>
            {{ item.basis
            }}<small>填写单价：{{ estimateMoney(item.unit_price) }}</small>
          </td>
          <td>{{ estimateMoney(item.subtotal) }}</td>
        </tr>
      </tbody>
    </table>
    <p class="budget-scope">
      仅按填写标准估算，不是实时价格；不含往返目的地的大交通、购物等。
    </p>
    <details>
      <summary>查看计算范围与说明</summary>
      <ul>
        <li v-for="note in budget.notes" :key="note">{{ note }}</li>
      </ul>
    </details>
  </section>
</template>

<style scoped>
.budget-panel {
  margin: 24px 28px 0;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 12px;
  color: #52644d;
  background: #f8faf5;
  font-size: 12px;
  line-height: 1.8;
}
.budget-heading {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}
h3 {
  margin: 0;
  font-size: 16px;
  color: #234438;
}
p {
  margin: 6px 0;
}
.budget-total {
  text-align: right;
  flex-shrink: 0;
}
.budget-total span,
.budget-total strong {
  display: block;
}
.budget-total strong {
  font-size: 24px;
  color: #125949;
  line-height: 1.4;
}
.budget-warning {
  color: #8f6d36;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin-top: 12px;
  table-layout: fixed;
}
th,
td {
  text-align: left;
  padding: 10px 8px;
  border-bottom: 1px solid var(--line);
  overflow-wrap: anywhere;
  vertical-align: top;
}
th {
  font-weight: 500;
}
th:first-child {
  width: 20%;
  padding-left: 0;
}
th:last-child {
  width: 24%;
}
td:last-child {
  text-align: right;
  padding-right: 0;
}
thead th:last-child {
  text-align: right;
  padding-right: 0;
}
small {
  display: block;
  color: #78836e;
}
.budget-scope {
  margin-top: 12px;
}
summary {
  cursor: pointer;
}
ul {
  padding-left: 18px;
  margin-top: 8px;
}
@media (max-width: 600px) {
  .budget-panel {
    margin: 20px 16px 0;
    padding: 14px;
    font-size: 11px;
  }
  h3 {
    font-size: 14px;
  }
  .budget-heading {
    flex-wrap: wrap;
    gap: 8px;
  }
  .budget-total {
    text-align: left;
  }
  .budget-total strong {
    font-size: 22px;
  }
}
</style>
