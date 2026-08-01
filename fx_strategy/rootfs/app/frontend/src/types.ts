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

export interface Settings {
  general: GeneralSettings;
  formatting: FormattingSettings;
  providers: ProviderSettings;
  notifications: NotificationSettings;
  home_assistant: HomeAssistantSettings;
  retention: RetentionSettings;
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
