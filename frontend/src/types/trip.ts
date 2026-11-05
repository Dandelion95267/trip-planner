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
  anchor_poi_id?: string | null;
  straight_line_distance_m?: number | null;
  poi_category?: string | null;
  preference_level?:
    "category_supported" | "name_hint" | "unverified" | "not_requested" | null;
  preference_summary?: string | null;
  preference_evidence?: DiningPreferenceEvidence[];
}
export interface DiningPreferenceEvidence {
  preference: string;
  kind: "cuisine" | "taste" | "dietary" | "other";
  target_category: string | null;
  strict: boolean;
  status: "supported" | "hint" | "unknown" | "conflict";
  source: "poi_type" | "poi_name" | "not_available";
  value: string | null;
  explanation: string;
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
  dining_warnings?: string[];
  dining_resolution?: DiningResolution | null;
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
  route_resolution?: RouteResolution | null;
}

export interface UnresolvedRoute {
  day_index: number;
  origin_poi_id: string | null;
  destination_poi_id: string | null;
  origin_name: string;
  destination_name: string;
  reason: string;
  explanation: string;
  observed_walking_m: number | null;
}

export interface RouteAlternative {
  kind:
    "retry" | "change_attractions" | "relax_walking_limit" | "change_transport";
  title: string;
  description: string;
  condition_changes: string[];
  suggested_max_leg_walking_m: number | null;
  requires_confirmation: true;
  verification: "not_verified";
}

export interface RouteResolution {
  status: "verified" | "needs_choice" | "not_applicable";
  scope: "adjacent_attractions";
  requested_transportation: string;
  requested_mode: RouteMode | null;
  max_leg_walking_m: number | null;
  preserve_first_attraction: boolean;
  required_segments: number;
  verified_segments: number;
  summary: string;
  limitations: string[];
  unresolved: UnresolvedRoute[];
  alternatives: RouteAlternative[];
}

export interface TripReviewIssue {
  category: "preference" | "pace" | "consistency";
  day_index: number | null;
  problem: string;
  evidence: string;
  suggestion: string;
  source?: "program" | "model";
  alternative?: {
    proposal: string;
    requires_confirmation: true;
    applied: false;
  } | null;
}

export interface DiningResolution {
  status:
    "candidates_available" | "partial" | "needs_choice" | "not_applicable";
  requested_preference: string;
  preference_verification: "not_requested" | "not_verified";
  based_on_provisional_itinerary: boolean;
  required_slots: number;
  provided_slots: number;
  summary: string;
  unresolved: {
    meal_type: "lunch" | "dinner";
    anchor_poi_id: string | null;
    anchor_name: string;
    reason:
      | "no_coordinates"
      | "no_nearby_candidate"
      | "provider_error"
      | "timeout"
      | "budget_exhausted"
      | "preference_conflict"
      | "preference_unverified";
    explanation: string;
  }[];
  alternatives: {
    kind:
      "retry" | "manual_search" | "adjust_preferences" | "contact_restaurant";
    title: string;
    description: string;
    requires_confirmation: true;
    verification: "not_verified";
  }[];
  preference_notes?: string[];
}

export interface TripReviewResult {
  passed: boolean;
  summary: string;
  issues: TripReviewIssue[];
  time_evidence?: {
    day_index: number;
    suggested_visit_minutes: number;
    expected_route_count: number;
    known_route_count: number;
    known_route_duration_s: number;
    known_total_duration_s: number;
    all_adjacent_routes_available: boolean;
    display_text: string;
  }[];
  meal_consistency?: {
    day_index: number;
    meal_type: "breakfast" | "lunch" | "dinner" | "snack";
    expected_poi_id?: string | null;
    expected_name: string;
    mentioned_name?: string | null;
    source_excerpt?: string | null;
    status: "matched" | "conflict" | "unknown";
    explanation: string;
  }[];
  semantic_status?: "completed" | "not_run" | "unavailable" | null;
}

export interface TripReviewSummary {
  status: "passed" | "needs_revision" | "unavailable";
  review_count: number;
  revisions: number;
  result: TripReviewResult | null;
  message: string;
}
