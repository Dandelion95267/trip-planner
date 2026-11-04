export type RouteMode = "auto" | "walking" | "driving" | "transit";
export interface TripRequest {
  budget_options?: BudgetOptions | null;
  enable_review?: boolean;
  city: string;
  start_date: string;
  end_date: string;
  travel_days: number;
  transportation: string;
  route_mode: RouteMode;
  max_leg_walking_m?: number | null;
  preserve_first_attraction?: boolean;
  optimize_order: boolean;
  accommodation: string;
  dining_preference: string;
  preferences: string[];
  free_text_input: string;
}
export interface Location {
  longitude: number;
  latitude: number;
}
export interface POIInfo {
  id: string;
  name: string;
  type: string;
  address: string;
  location: Location | null;
}
export interface Attraction {
  poi_id: string | null;
  name: string;
  address: string;
  location: Location | null;
  visit_duration: number;
  description: string;
  category: string | null;
  rating: number | null;
  image_url: string | null;
  ticket_price: number | null;
}
export interface Meal {
  poi_id: string | null;
  type: "breakfast" | "lunch" | "dinner" | "snack";
  name: string;
  address: string | null;
  location: Location | null;
  description: string | null;
  estimated_cost: number | null;
}
export interface Hotel {
  poi_id: string | null;
  name: string;
  address: string;
  location: Location | null;
  price_range: string | null;
  rating: string | null;
  distance: string | null;
  type: string | null;
  estimated_cost: number | null;
}
export interface WeatherInfo {
  date: string;
  day_weather: string;
  night_weather: string;
  day_temp: number;
  night_temp: number;
  wind_direction: string;
  wind_power: string;
}
export interface RoutePlan {
  origin: POIInfo;
  destination: POIInfo;
  mode: Exclude<RouteMode, "auto">;
  distance_m: number | null;
  duration_s: number;
  walking_distance_m: number | null;
  steps: string[];
  selection_reason: string | null;
  warnings: string[];
}
export interface OrderOptimization {
  status: "optimized" | "unchanged" | "skipped";
  distance_basis: "straight_line";
  original_order: string[];
  optimized_order: string[];
  before_distance_m: number | null;
  after_distance_m: number | null;
  reason: string;
}
export interface DayPlan {
  date: string;
  day_index: number;
  description: string;
  transportation: string;
  accommodation: string;
  hotel: Hotel | null;
  attractions: Attraction[];
  meals: Meal[];
  routes: RoutePlan[];
  route_warnings: string[];
  order_optimization: OrderOptimization | null;
}
export interface BudgetOptions {
  travelers: number;
  rooms: number;
  hotel_per_room_night: number | null;
  meals_per_person_day: number | null;
  transport_per_person_day: number | null;
  ticket_per_person_visit: number | null;
}
export interface BudgetItem {
  category: "attractions" | "hotels" | "meals" | "transportation";
  label: string;
  quantity: number;
  unit_price: number | null;
  subtotal: number | null;
  basis: string;
}
export interface Budget {
  source: "user_estimate";
  currency: "CNY";
  travelers: number;
  rooms: number;
  nights: number;
  total_attractions: number | null;
  total_hotels: number | null;
  total_meals: number | null;
  total_transportation: number | null;
  known_subtotal: number;
  is_complete: boolean;
  total: number | null;
  items: BudgetItem[];
  notes: string[];
}
export interface TripPlan {
  city: string;
  start_date: string;
  end_date: string;
  days: DayPlan[];
  weather_info: WeatherInfo[];
  overall_suggestions: string;
  budget: Budget | null;
  review?: TripReviewSummary | null;
}

export interface TripReviewIssue {
  category: "preference" | "pace" | "consistency";
  day_index: number | null;
  problem: string;
  evidence: string;
  suggestion: string;
}

export interface TripReviewResult {
  passed: boolean;
  summary: string;
  issues: TripReviewIssue[];
}

export interface TripReviewSummary {
  status: "passed" | "needs_revision" | "unavailable";
  review_count: number;
  revisions: number;
  result: TripReviewResult | null;
  message: string;
}
