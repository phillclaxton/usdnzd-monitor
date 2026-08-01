/**
 * Shapes returned by the internal API.
 *
 * Every monetary or rate field is typed `string` on purpose: these are exact
 * decimal strings from the backend and must never be widened to `number`.
 */

export interface Health {
  status: 'ok' | 'degraded';
  version: string;
  arch: string;
  simulation_mode: boolean;
  database: string;
  timestamp: string;
}

export interface GeneralSettings {
  timezone: string;
  source_currency: string;
  target_currency: string;
  rate_convention: 'target_per_source' | 'source_per_target';
  setup_complete: boolean;
  active_strategy_id: number | null;
}

export interface FormattingSettings {
  currency_decimal_places: number;
  rate_decimal_places: number;
  thousands_separator: boolean;
  locale: string;
}

export interface GenericProviderSettings {
  enabled: boolean;
  display_name: string;
  base_url: string;
  rate_path: string;
  history_path: string;
  auth_style: 'header' | 'query' | 'bearer' | 'none';
  auth_name: string;
  source_param: string;
  target_param: string;
  rate_json_path: string;
  timestamp_json_path: string;
  convention: 'target_per_source' | 'source_per_target';
  provider_timezone: string;
  min_seconds_between_calls: number;
  timeout_seconds: number;
  preset: string;
}

export interface WiseProviderSettings {
  enabled: boolean;
  environment: 'live' | 'sandbox';
  profile_id: string;
  source_balance_id: string;
  target_balance_id: string;
  read_only: boolean;
  timeout_seconds: number;
}

export interface ProviderSettings {
  primary: string;
  secondary: string | null;
  manual_fallback: boolean;
  poll_seconds_active: number;
  poll_seconds_idle: number;
  poll_seconds_minimum: number;
  jitter_seconds: number;
  stale_after_seconds: number;
  max_backoff_seconds: number;
  error_notify_after_seconds: number;
  disagreement_threshold: string;
  disagreement_is_relative: boolean;
  market_active_weekdays: number[];
  store_raw_payloads: boolean;
  generic: GenericProviderSettings;
  wise: WiseProviderSettings;
}

export interface QuietHours {
  enabled: boolean;
  start: string;
  end: string;
  allow_critical: boolean;
}

export interface NotificationSettings {
  enabled: boolean;
  services: string[];
  default_cooldown_minutes: number;
  near_threshold: string;
  reset_hysteresis: string;
  confirmation_samples: number;
  confirmation_min_seconds: number;
  repeat_interval_minutes: number;
  quiet_hours: QuietHours;
  reversal_threshold: string;
  deadline_warning_days: number[];
}

export interface HomeAssistantSettings {
  publish_entities: boolean;
  mqtt_discovery_prefix: string;
  device_name: string;
  node_id: string;
  expose_writable_controls: boolean;
  publish_interval_seconds: number;
}

export interface RetentionSettings {
  fine_rate_days: number;
  hourly_aggregate_days: number;
  keep_daily_aggregates_forever: boolean;
  log_days: number;
  store_raw_payloads: boolean;
}

export interface SimulationSettings {
  enabled: boolean;
  simulated_rate: string | null;
  time_acceleration: number;
  force_provider_error: boolean;
  force_disagreement: boolean;
  replay_cursor: number;
}

export interface RateZoneSetting {
  label: string;
  guidance: string;
  lower_bound: string | null;
}

export interface ZoneSettings {
  enabled: boolean;
  zones: RateZoneSetting[];
}

export interface Settings {
  general: GeneralSettings;
  formatting: FormattingSettings;
  providers: ProviderSettings;
  notifications: NotificationSettings;
  home_assistant: HomeAssistantSettings;
  retention: RetentionSettings;
  zones: ZoneSettings;
  simulation: SimulationSettings;
}

export interface AuditEvent {
  id: number;
  event_type: string;
  entity_type: string;
  entity_id: string | null;
  actor: string;
  timestamp: string;
  before_json: string | null;
  after_json: string | null;
  message: string;
  correlation_id: string | null;
}

export interface RateChanges {
  one_hour: string | null;
  twenty_four_hours: string | null;
  seven_days: string | null;
  thirty_days: string | null;
}

export type RateStatus = 'live' | 'delayed' | 'stale' | 'unavailable';

export interface CurrentRate {
  source_currency: string;
  target_currency: string;
  rate: string | null;
  status: RateStatus;
  provider: string;
  quote_type: string | null;
  quote_label: string | null;
  provider_timestamp: string | null;
  retrieved_at: string | null;
  age_seconds: number | null;
  stale_after_seconds: number;
  changes: RateChanges;
  high_24h: string | null;
  low_24h: string | null;
  high_6m: string | null;
  low_6m: string | null;
  disagreement_warning: string | null;
  message: string | null;
}

export interface RatePoint {
  timestamp: string;
  rate: string;
  provider: string;
}

export interface RateHistory {
  source_currency: string;
  target_currency: string;
  start: string;
  end: string;
  resolution: 'sample' | 'hour' | 'day';
  points: RatePoint[];
  high: string | null;
  low: string | null;
  average: string | null;
  truncated: boolean;
}

export interface RefreshResult {
  succeeded: boolean;
  provider: string;
  attempted: string[];
  errors: Record<string, string>;
  rate: string | null;
  disagreement: string | null;
  disagreement_exceeded: boolean;
  comparison: Record<string, string>;
}

export interface ProviderStatus {
  provider: string;
  display_name: string;
  configured: boolean;
  healthy: boolean;
  last_success_at: string | null;
  last_failure_at: string | null;
  consecutive_failures: number;
  last_error: string | null;
  last_latency_ms: number | null;
  retry_after: string | null;
  reason: string;
}

export type AllocationType = 'percentage' | 'fixed_amount' | 'remainder';
export type StrategyStatus =
  | 'draft'
  | 'waiting_for_funds'
  | 'active'
  | 'paused'
  | 'completed'
  | 'cancelled'
  | 'archived';
export type TrancheStatus =
  | 'pending'
  | 'armed'
  | 'target_reached'
  | 'partially_completed'
  | 'completed'
  | 'skipped'
  | 'cancelled';
export type FeeType = 'percentage' | 'fixed_plus_percentage' | 'quote_only' | 'manual';

export interface FeeModel {
  id: number;
  name: string;
  fee_type: FeeType;
  fixed_fee: string;
  percentage_fee: string;
  minimum_fee: string | null;
  maximum_fee: string | null;
  currency: string;
  provider: string;
}

export interface Tranche {
  id: number;
  strategy_id: number;
  sequence: number;
  name: string;
  allocation_type: AllocationType;
  allocation_value: string;
  calculated_source_amount: string;
  target_rate: string;
  minimum_rate: string | null;
  deadline: string | null;
  status: TrancheStatus;
  intended_for_auto_conversion: boolean;
  notifications_enabled: boolean;
  wise_auto_conversion_reference: string | null;
  target_first_reached_at: string | null;
  notification_sent_at: string | null;
  acknowledged_at: string | null;
  completed_at: string | null;
}

export interface TrancheInput {
  sequence: number;
  name?: string;
  allocation_type: AllocationType;
  allocation_value: string;
  target_rate: string;
  minimum_rate?: string | null;
  deadline?: string | null;
  intended_for_auto_conversion?: boolean;
  notifications_enabled?: boolean;
  wise_auto_conversion_reference?: string | null;
}

export interface Requirement {
  id: number;
  strategy_id: number;
  due_date: string;
  required_source_amount: string | null;
  required_percentage: string | null;
  description: string;
}

export interface Strategy {
  id: number;
  name: string;
  status: StrategyStatus;
  source_currency: string;
  target_currency: string;
  initial_source_amount: string;
  funds_available_amount: string;
  funds_arrival_date: string | null;
  strategy_start_date: string | null;
  final_deadline: string | null;
  minimum_acceptable_rate: string | null;
  walk_away_rate: string | null;
  require_targets_in_order: boolean;
  fee_model_id: number | null;
  rate_provider_id: string | null;
  timezone: string;
  notes: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
  completed_at: string | null;
  tranches: Tranche[];
  requirements: Requirement[];
}

export interface StrategyInput {
  name: string;
  source_currency: string;
  target_currency: string;
  initial_source_amount: string;
  funds_available_amount: string;
  funds_arrival_date?: string | null;
  final_deadline?: string | null;
  minimum_acceptable_rate?: string | null;
  walk_away_rate?: string | null;
  require_targets_in_order?: boolean;
  fee_model_id?: number | null;
  timezone?: string;
  notes?: string;
  tranches: TrancheInput[];
}

export interface AllocationIssue {
  severity: 'error' | 'warning';
  message: string;
}

export interface ValidationReport {
  valid: boolean;
  total_allocated: string;
  unallocated: string;
  percentage_total: string;
  issues: AllocationIssue[];
}

export interface Fee {
  available: boolean;
  label: string;
  amount_source_currency: string | null;
  amount_target_currency: string | null;
  basis: string;
}

export interface Outcome {
  source_amount: string;
  rate: string;
  gross_target_amount: string;
  fee: Fee;
  net_target_amount: string | null;
  effective_rate: string | null;
  quality: string;
}

export interface TrancheProgress {
  tranche: Tranche;
  distance_to_target: string | null;
  target_reached_now: boolean;
  estimated_gross: string | null;
  estimated_fee: Fee;
  estimated_net: string | null;
  converted_source_amount: string;
  converted_target_amount: string;
  percent_complete: string | null;
  upside_to_target: string | null;
}

export interface Sensitivity {
  movement: string;
  downside: string;
  upside: string;
}

export interface RequirementProgress {
  requirement: Requirement;
  required_source_amount: string;
  shortfall: string;
  days_remaining: number | null;
  overdue: boolean;
}

export interface WalkAway {
  reached: boolean;
  walk_away_rate: string | null;
  remaining_source_amount: string;
  convert_now: Outcome | null;
  existing_blended_rate: string | null;
  blended_if_converted_now: string | null;
  highest_outstanding_target: string | null;
  difference_versus_waiting: string | null;
  rate_movement_to_next_target: string | null;
  sensitivity: Sensitivity[];
}

export interface Zone {
  label: string;
  guidance: string;
  lower_bound: string | null;
}

export interface Comparisons {
  versus_start_rate: string | null;
  versus_six_month_high: string | null;
  versus_six_month_low: string | null;
  versus_today: string | null;
  versus_equal_schedule: string | null;
}

export interface StrategySummary {
  strategy: Strategy;
  current_rate: string | null;
  rate_status: RateStatus;
  rate_zone: Zone | null;
  initial_source_amount: string;
  available_source_amount: string;
  converted_source_amount: string;
  remaining_source_amount: string;
  percent_converted: string | null;
  gross_target_received: string;
  net_target_received: string;
  total_fees: string | null;
  blended_gross_rate: string | null;
  blended_effective_rate: string | null;
  best_conversion_rate: string | null;
  worst_conversion_rate: string | null;
  average_fee_percentage: string | null;
  convert_all_now: Outcome | null;
  next_target_rate: string | null;
  next_target_source_amount: string | null;
  next_target_upside: string | null;
  one_cent_exposure: string;
  sensitivity: Sensitivity[];
  tranche_progress: TrancheProgress[];
  requirements: RequirementProgress[];
  walk_away: WalkAway;
  comparisons: Comparisons;
  days_to_deadline: number | null;
  deadline_severity: string;
  deadline_message: string;
  fee_model: FeeModel | null;
  warnings: string[];
}

export interface ScenarioLeg {
  source_amount: string;
  rate: string;
  label: string;
}

export interface Scenario {
  key: string;
  name: string;
  description: string;
  total_source_amount: string;
  gross_target_amount: string;
  fee: Fee;
  net_target_amount: string | null;
  blended_rate: string;
  effective_rate: string | null;
  exposed_source_amount: string;
  one_cent_exposure: string;
  rate_required: string | null;
  legs: ScenarioLeg[];
  assumptions: string[];
}

export interface Scenarios {
  scenarios: Scenario[];
  note: string;
}

export interface StrategyTemplate {
  key: string;
  name: string;
  description: string;
  tranches: TrancheInput[];
}

export interface NotificationLogEntry {
  id: number;
  rule_type: string;
  severity: string;
  title: string;
  message: string;
  entity_type: string | null;
  entity_id: string | null;
  delivered: boolean;
  queued: boolean;
  attempts: number;
  last_error: string | null;
  suppressed_reason: string | null;
  created_at: string;
  delivered_at: string | null;
}

export interface Conversion {
  id: number;
  strategy_id: number;
  tranche_id: number | null;
  source_amount: string;
  target_amount: string;
  gross_rate: string;
  effective_rate: string;
  fee_source_currency: string | null;
  fee_target_currency: string | null;
  fee_total_target_equivalent: string | null;
  provider: string;
  provider_transaction_id: string | null;
  executed_at: string;
  record_source: string;
  simulated: boolean;
  notes: string;
  receipt_filename: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversionList {
  conversions: Conversion[];
  total_source_amount: string;
  total_target_amount: string;
  blended_gross_rate: string | null;
  blended_effective_rate: string | null;
  total_fees: string | null;
}

export interface ConversionImportPreview {
  total_rows: number;
  accepted: number;
  rejected: number;
  duplicates: number;
  errors: { row: number; message: string }[];
  sample: Record<string, unknown>[];
  imported: number;
  committed: boolean;
}

export interface WiseStatus {
  configured: boolean;
  connected: boolean;
  read_only: boolean;
  environment: string;
  message: string;
  profile_id: string;
  profiles: { id: string; type: string }[];
  token_hint: string;
  latency_ms: number | null;
  notice: string;
}

export interface WiseBalance {
  balance_id: string;
  currency: string;
  amount: string;
  reserved: string;
  type: string;
}

export interface ReconcileResult {
  dry_run: boolean;
  fetched: number;
  matched: number;
  imported: number;
  skipped_other_pair: number;
  errors: string[];
  imported_references: string[];
  matched_references: string[];
}
