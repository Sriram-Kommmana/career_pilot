/**
 * Types mirroring the backend's Pydantic response schemas.
 *
 * These are hand-written rather than generated, so they must be kept in sync with
 * `backend/app/schemas/*.py` by hand. Field names and shapes match exactly;
 * enum values are the literal strings the backend actually serialises.
 */

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

export interface ErrorEnvelope {
  error: {
    code: string;
    message: string;
    correlation_id: string;
    details?: unknown;
  };
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_at: string;
}

export interface UserOut {
  id: string;
  email: string;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

export type EducationLevel =
  | "UNKNOWN"
  | "HIGH_SCHOOL"
  | "DIPLOMA"
  | "BACHELORS"
  | "MASTERS"
  | "DOCTORATE";

export type EmploymentType =
  | "FULL_TIME"
  | "PART_TIME"
  | "INTERNSHIP"
  | "CONTRACT"
  | "TEMPORARY"
  | "APPRENTICESHIP"
  | "UNKNOWN";

export type ProvenanceKind =
  | "USER_CONFIRMED_PROFILE"
  | "RESUME_EXTRACTION_CONFIRMED"
  | "USER_APPROVED_EVIDENCE"
  | "PREVIOUSLY_APPROVED_ANSWER"
  | "USER_DIRECT_INPUT"
  | "RESUME_EXTRACTION_UNCONFIRMED"
  | "GENERATED_UNVERIFIED"
  | "UNKNOWN";

export interface EducationRecordOut {
  id: string;
  institution: string;
  degree: string | null;
  field_of_study: string | null;
  level: EducationLevel;
  start_date: string | null;
  end_date: string | null;
  grade: string | null;
  provenance: ProvenanceKind;
}

export interface SkillOut {
  id: string;
  name: string;
  canonical_name: string;
  category: string | null;
  years: number | null;
  has_evidence: boolean;
  provenance: ProvenanceKind;
}

export interface ProjectOut {
  id: string;
  name: string;
  description: string | null;
  role: string | null;
  url: string | null;
  repository_url: string | null;
  skills_used: string[];
  highlights: string[];
  start_date: string | null;
  end_date: string | null;
  provenance: ProvenanceKind;
}

export interface ExperienceOut {
  id: string;
  company: string;
  title: string;
  location: string | null;
  employment_type: EmploymentType;
  description: string | null;
  skills_used: string[];
  highlights: string[];
  start_date: string | null;
  end_date: string | null;
  is_current: boolean;
  provenance: ProvenanceKind;
}

export interface CertificationOut {
  id: string;
  name: string;
  issuer: string | null;
  issued_on: string | null;
  expires_on: string | null;
  credential_url: string | null;
  provenance: ProvenanceKind;
}

export interface ProfileOut {
  id: string;
  user_id: string;
  full_name: string | null;
  headline: string | null;
  phone: string | null;
  location: string | null;
  github_url: string | null;
  linkedin_url: string | null;
  portfolio_url: string | null;
  highest_education: EducationLevel;
  graduation_year: number | null;
  target_roles: string[];
  preferred_locations: string[];
  open_to_remote: boolean | null;
  employment_preferences: string[];
  excluded_companies: string[];
  excluded_roles: string[];
  excluded_locations: string[];
  minimum_salary: number | null;
  salary_currency: string | null;
  work_authorisation: Record<string, unknown>;
  notify_score_threshold: number;
  education_records: EducationRecordOut[];
  skills: SkillOut[];
  projects: ProjectOut[];
  experience_records: ExperienceOut[];
  certifications: CertificationOut[];
  created_at: string;
  updated_at: string;
}

export type ExtractionStatus = "pending" | "succeeded" | "failed";

export interface ResumeOut {
  id: string;
  label: string;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  content_sha256: string;
  is_default: boolean;
  extraction_status: ExtractionStatus;
  extraction_error: string | null;
  extraction_confirmed_at: string | null;
  created_at: string;
}

export type ExtractedItemField =
  | "skill"
  | "education"
  | "project"
  | "experience"
  | "certification"
  | "achievement";

export type ExtractionConfidence = "high" | "medium" | "low";

export interface ExtractionItemOut {
  index: number;
  field: ExtractedItemField;
  data: Record<string, unknown>;
  source_excerpt: string;
  locator: string;
  confidence: ExtractionConfidence;
  needs_review: boolean;
  /** Advisory only. A non-null value never means an automatic merge happened —
   * see ConfirmExtractionRequest's `existing_record_id`. */
  possible_duplicate_of: string | null;
}

export interface ExtractionProposalOut {
  resume_id: string;
  extraction_status: ExtractionStatus;
  items: ExtractionItemOut[];
  unparsed_sections: string[];
}

export interface ExtractionDecision {
  index: number;
  accept: boolean;
  /** Set only to update this specific existing record. Omitting it never
   * replaces anything that already exists — it always adds a new row (or, for
   * a skill that already exists, does nothing). */
  existing_record_id?: string | null;
}

export interface ConfirmExtractionRequest {
  decisions: ExtractionDecision[];
}

export type ConfirmExtractionStatus = "created" | "updated" | "skipped" | "rejected";

export interface ConfirmExtractionResultOut {
  index: number;
  field: ExtractedItemField;
  status: ConfirmExtractionStatus;
  record_id: string | null;
  message: string | null;
}

export interface ConfirmExtractionResponse {
  resume_id: string;
  results: ConfirmExtractionResultOut[];
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------

export type WorkArrangement = "REMOTE" | "HYBRID" | "ONSITE" | "UNKNOWN";
export type PostedAtPrecision = "EXACT" | "DATE_ONLY" | "UNKNOWN";
export type SourceStatus = "OPEN" | "CLOSED" | "DRAFT" | "UNKNOWN";

export interface CompanyOut {
  id: string;
  name: string;
  website: string | null;
  careers_url: string | null;
}

export interface SalaryOut {
  minimum: number | null;
  maximum: number | null;
  currency: string | null;
  period: string | null;
}

export interface JobSummaryOut {
  id: string;
  title: string;
  company: CompanyOut;
  location_raw: string | null;
  work_arrangement: WorkArrangement;
  employment_type: EmploymentType;
  application_url: string;
  source: string;
  source_status: SourceStatus;
  posted_at: string | null;
  posted_at_precision: PostedAtPrecision;
  last_observed_at: string;
  is_demo_data: boolean;
  salary: SalaryOut | null;
  source_count: number;
}

export interface JobRequirementOut {
  id: string;
  position: number;
  text: string;
  is_optional: boolean;
  extracted_skills: string[];
}

export interface MatchComponentOut {
  name: string;
  raw: number | null;
  weight: number;
  weighted: number;
  assessed: boolean;
  rationale: string;
  details: Record<string, unknown>;
}

export type RequirementStatus = "MET" | "MISSING" | "UNCERTAIN";

export interface RequirementResultOut {
  text: string;
  status: RequirementStatus;
  is_optional: boolean;
  matched_skills: string[];
  missing_skills: string[];
  evidence: string[];
  note: string | null;
}

export interface MatchExplanationOut {
  why_it_may_fit: string[];
  evidenced_qualifications: string[];
  missing_requirements: string[];
  uncertain_requirements: string[];
  eligibility_concerns: string[];
  suggested_next_step: string;
}

export interface HardFilterFailureOut {
  code: string;
  message: string;
  detail: string | null;
}

export interface MatchAssessmentOut {
  score: number | null;
  score_kind: "RANKING_HEURISTIC";
  score_meaning: string;
  passed_hard_filters: boolean;
  hard_filter_failures: HardFilterFailureOut[];
  components: MatchComponentOut[];
  requirement_results: RequirementResultOut[];
  explanation: MatchExplanationOut | null;
  evidence_refs: Record<string, unknown>[];
  assessed_weight: number;
  engine_version: string;
  assessed_at: string;
}

export interface JobFeedItemOut {
  job: JobSummaryOut;
  match: MatchAssessmentOut | null;
}

export interface JobDetailOut {
  job: JobSummaryOut;
  description: string | null;
  requirements: JobRequirementOut[];
  responsibilities: string[];
  match: MatchAssessmentOut | null;
  duplicates: JobSummaryOut[];
}

// ---------------------------------------------------------------------------
// Applications
// ---------------------------------------------------------------------------

export type ApplicationState =
  | "DISCOVERED"
  | "MATCHED"
  | "SELECTED"
  | "PREPARING"
  | "READY"
  | "FILLING"
  | "VERIFYING"
  | "WAITING_FOR_USER_INPUT"
  | "WAITING_FOR_APPROVAL"
  | "SUBMITTING"
  | "SUBMITTED"
  | "SUBMITTED_UNCERTAIN"
  | "FAILED"
  | "CANCELLED";

export type ApplicationOutcome =
  | "NONE"
  | "ACKNOWLEDGED"
  | "INTERVIEW"
  | "OFFER"
  | "REJECTED"
  | "WITHDRAWN"
  | "CLOSED";

export type FieldResolution = "RESOLVED" | "NEEDS_HUMAN" | "UNKNOWN_FIELD" | "SKIPPED";
export type Sensitivity = "PUBLIC" | "MASKED" | "NEVER_STORED";
export type VerificationStatus = "PASSED" | "FAILED" | "INCONCLUSIVE" | "NOT_ATTEMPTED";

export interface ApplicationFieldOut {
  id: string;
  field_key: string;
  label: string | null;
  field_type: string;
  is_required: boolean;
  resolution: FieldResolution;
  sensitivity: Sensitivity;
  value_masked: string | null;
  value_provenance: ProvenanceKind;
  sensitive_category: string | null;
  action_result: string | null;
  verification: VerificationStatus;
  verification_note: string | null;
  verified_at: string | null;
  needs_human: boolean;
}

export interface ApplicationAnswerOut {
  id: string;
  question_key: string;
  question_text: string;
  answer_text: string | null;
  provenance: ProvenanceKind;
  evidence_refs: Record<string, unknown>[];
  is_generated: boolean;
  sensitive_category: string | null;
  needs_human: boolean;
  user_edited_at: string | null;
}

export interface ApplicationEventOut {
  id: string;
  occurred_at: string;
  event_type: string;
  from_state: ApplicationState | null;
  to_state: ApplicationState | null;
  intervention_reason: string | null;
  message: string | null;
  correlation_id: string | null;
}

export interface ApprovalOut {
  id: string;
  approved_at: string;
  form_state_hash: string;
  note: string | null;
  revoked_at: string | null;
  revoked_reason: string | null;
}

export interface ApplicationSummaryOut {
  id: string;
  job: JobSummaryOut;
  state: ApplicationState;
  outcome: ApplicationOutcome;
  outcome_note: string | null;
  application_url: string;
  verification_status: VerificationStatus;
  verified_at: string | null;
  unresolved_interventions: string[];
  submitted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApplicationDetailOut extends ApplicationSummaryOut {
  form_state_hash: string | null;
  resume_id: string | null;
  submission_attempted_at: string | null;
  submission_evidence: Record<string, unknown> | null;
  failure_reason: string | null;
  fields: ApplicationFieldOut[];
  answers: ApplicationAnswerOut[];
  events: ApplicationEventOut[];
  approvals: ApprovalOut[];
}

export interface BlockerOut {
  code: string;
  message: string;
  detail: string | null;
}

export interface SubmissionDecisionOut {
  allowed: boolean;
  blockers: BlockerOut[];
}

export interface ApplicationReviewOut {
  application: ApplicationDetailOut;
  decision: SubmissionDecisionOut;
  unresolved_field_labels: string[];
  documents: Record<string, unknown>[];
  last_verified_at: string | null;
}

export interface ResumeGuidanceItemOut {
  kind: string;
  id: string;
  title: string;
  organisation: string | null;
  matched_skills: string[];
  highlights: string[];
  reason: string;
  evidence_ref: Record<string, unknown>;
}

export type ReadinessStatus = "ready" | "needs_attention" | "missing";

export interface ReadinessChecklistItemOut {
  category: string;
  label: string;
  status: ReadinessStatus;
  detail: string;
}

export interface ApplicationPackageOut {
  fit_summary: MatchAssessmentOut;
  resume_guidance: ResumeGuidanceItemOut[];
  readiness_checklist: ReadinessChecklistItemOut[];
  application: ApplicationDetailOut;
}

export interface TrackerBoardOut {
  columns: Record<string, ApplicationSummaryOut[]>;
  column_order: string[];
}

// ---------------------------------------------------------------------------
// Notifications & agent runs
// ---------------------------------------------------------------------------

export interface NotificationOut {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  link: string | null;
  job_id: string | null;
  application_id: string | null;
  read_at: string | null;
  created_at: string;
  payload: Record<string, unknown>;
}

export interface NotificationListOut {
  items: NotificationOut[];
  total: number;
  unread: number;
  limit: number;
  offset: number;
}

export interface AgentRunOut {
  id: string;
  kind: string;
  status: string;
  correlation_id: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  summary: string | null;
  failure_reason: string | null;
  retry_count: number;
  job_id: string | null;
  application_id: string | null;
  context: Record<string, unknown>;
}
