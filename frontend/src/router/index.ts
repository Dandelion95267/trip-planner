import { createRouter, createWebHashHistory } from "vue-router";
import PlannerView from "../views/PlannerView.vue";

export default createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", component: PlannerView },
    { path: "/:pathMatch(.*)*", redirect: "/" },
  ],
});
