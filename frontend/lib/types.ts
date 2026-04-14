export type UploadResponse = {
  session_id: string;
  job_id: string;
  status: string;
};

export type RuntimeMetadata = {
  runtime_profile: string;
  presentation_mode: string;
  low_data_mode: boolean;
  trace_correlation_id: string;
  trace_exporters_effective: string[];
  trace_local_path: string | null;
  orchestrator_backend_requested: string;
  orchestrator_backend_effective: string;
  generation_backend_requested: string;
  generation_backend_effective: string;
  retrieval_backend_requested: string;
  retrieval_backend_effective: string;
  pii_backend_requested: string;
  pii_backend_effective: string;
  sentiment_backend_requested: string;
  sentiment_backend_effective: string;
  sentiment_model_effective: string | null;
  embedding_backend: string;
  embedding_backend_requested?: string | null;
  embedding_backend_effective?: string | null;
  embedding_model_effective?: string | null;
  openai_generation_enabled: boolean;
  mistral_fallback_enabled: boolean;
  anthropic_fallback_enabled: boolean;
  llm_primary_model?: string | null;
  llm_call_count: number;
  embedding_call_count: number;
  prompt_tokens_total: number;
  completion_tokens_total: number;
  embedding_input_tokens_total: number;
  estimated_cost_usd: number;
  provider_usage_summary: Record<
    string,
    {
      llm_calls: number;
      embedding_calls: number;
      models: string[];
      last_status: string;
    }
  >;
  input_filename?: string | null;
  input_content_type?: string | null;
  records_total: number;
  records_kept: number;
  top_cluster_ids: string[];
  weak_signal_cluster_ids: string[];
  weak_signal_count: number;
  mixed_sentiment_cluster_ids: string[];
  mixed_sentiment_cluster_count: number;
  mixed_language_review_count: number;
  data_dir: string;
  embedded_worker: boolean;
  chroma_persist_dir?: string | null;
  chroma_mode_effective?: string | null;
  chroma_endpoint_effective?: string | null;
  agent_usage: Record<string, { used?: boolean; mode?: string; model?: string | null }>;
};

export type SessionSummary = {
  session_id: string;
  status: string;
  latest_job_id: string;
  created_at: string;
  updated_at: string;
  degraded_mode: boolean;
  failure_code?: string | null;
  report_path?: string | null;
  executive_summary?: string | null;
};

export type JobSummary = {
  job_id: string;
  session_id: string;
  status: string;
  stage: string;
  attempt: number;
  failure_code?: string | null;
  degraded_mode: boolean;
  message?: string | null;
  created_at: string;
  updated_at: string;
};

export type PreprocessingSummary = {
  total_records: number;
  kept_records: number;
  duplicate_records: number;
  quarantined_records: number;
  pii_hits: number;
  injection_hits: number;
  low_information_records: number;
  unsupported_language_records: number;
};

export type ClusterRecord = {
  cluster_id: string;
  label: string;
  summary: string;
  review_ids: string[];
  top_quote_ids: string[];
  priority_score: number;
  sentiment_score: number;
  trend_delta: number;
  confidence: string;
  degraded_reason?: string | null;
  keywords: string[];
  sources: string[];
  size: number;
  anomaly_flag: boolean;
};

export type AlertRecord = {
  alert_id: string;
  cluster_id: string;
  type: string;
  severity: string;
  reason: string;
  spike_ratio?: number | null;
  insufficient_history: boolean;
  created_at: string;
};

export type ReportArtifact = {
  report_id: string;
  session_id: string;
  path: string;
  executive_summary: string;
  markdown: string;
  generated_at: string;
  degraded_mode: boolean;
};

export type EventRecord = {
  stage: string;
  event: string;
  level: string;
  message: string;
  correlation_id: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type SessionDetail = {
  session: SessionSummary;
  job: JobSummary;
  preprocessing_summary?: PreprocessingSummary | null;
  clusters: ClusterRecord[];
  top_clusters: ClusterRecord[];
  weak_signals: ClusterRecord[];
  simple_list_reviews: Array<{
    review_id: string;
    source: string;
    created_at: string;
    language: string;
    text: string;
    flags: string[];
    cluster_id?: string | null;
  }>;
  presentation_mode: string;
  warnings: string[];
  alerts: AlertRecord[];
  report?: ReportArtifact | null;
  runtime_metadata?: RuntimeMetadata | null;
  events: EventRecord[];
};

export type ChatResponse = {
  session_id: string;
  correlation_id: string;
  question: string;
  answer: string;
  evidence: {
    query: string;
    cluster_hits: Array<{
      cluster_id: string;
      score: number;
      match_reason: string;
      label: string;
      summary: string;
      priority_score: number;
    }>;
    quotes: Array<{
      review_id: string;
      cluster_id: string;
      text: string;
      source: string;
      created_at: string;
    }>;
    trends: Array<{
      cluster_id: string;
      trend_delta: number;
      baseline?: number | null;
      recent_count: number;
      note: string;
    }>;
    context_tokens_estimate: number;
  };
  tool_trace: Array<{
    tool: string;
    input: Record<string, unknown>;
    output_summary: string;
  }>;
  degraded_mode: boolean;
};
