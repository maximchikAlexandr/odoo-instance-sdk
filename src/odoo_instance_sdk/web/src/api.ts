export interface EndpointSnapshot {
  host: string;
  port: number;
}

export interface ContainerSnapshot {
  id: string | null;
  name: string | null;
  image: string | null;
  pid: number | null;
  pid_scope: "host" | "docker_vm" | "unavailable";
}

export interface ClusterMetrics {
  cpu_percent: number | null;
  memory_usage_bytes: number | null;
  memory_limit_bytes: number | null;
  volume_usage_bytes: number | null;
  sampled_at: string | null;
}

export type ClusterUnavailabilityReason =
  | "external_not_owned"
  | "stopped"
  | "missing"
  | "docker_unavailable"
  | "inspect_failed"
  | "stats_failed";

export interface ClusterSnapshot {
  mode: string;
  owned: boolean;
  state: string;
  endpoint: EndpointSnapshot | null;
  container: ContainerSnapshot | null;
  metrics: ClusterMetrics | null;
  unavailability_reason: ClusterUnavailabilityReason | null;
  sampled_at: string | null;
}

export interface ProjectSummary {
  id: string;
  name: string;
  display_hint: string;
  environment_count: number;
  cluster: ClusterSnapshot | null;
}

export type RuntimeState = "stopped" | "ready" | "not_ready";

export interface RuntimeMetrics {
  state: RuntimeState;
  root_pid: number | null;
  child_pids: number[];
  process_count: number;
  cpu_percent: number | null;
  rss_bytes: number | null;
  started_at: string | null;
  http_url: string | null;
  http_port: number | null;
  database_name: string | null;
  commit_sha: string | null;
  branch: string | null;
}

export type GitState = "clean" | "ahead" | "behind" | "diverged" | "orphan";

export interface GitDiff {
  added: number;
  deleted: number;
}

export interface GitActivity {
  default_branch: string;
  head_sha: string | null;
  short_sha: string | null;
  branch: string;
  ahead: number | null;
  behind: number | null;
  diff: GitDiff | null;
  state: GitState;
}

export interface PythonEnvironmentFootprint {
  owned: boolean;
  bytes: number | null;
}

export interface DatabaseFootprint {
  owned: boolean;
  postgres_bytes: number | null;
  filestore_bytes: number | null;
  total_bytes: number | null;
}

export interface StorageFootprint {
  total_bytes: number;
  complete: boolean;
  worktree_bytes: number | null;
  python_environment: PythonEnvironmentFootprint;
  database: DatabaseFootprint;
  other_files_bytes: number | null;
}

export type LifecycleState =
  | "creating"
  | "ready"
  | "failed"
  | "removing"
  | "cleanup_failed"
  | "removed";

export interface EnvironmentSnapshot {
  id: string;
  project_id: string;
  name: string;
  branch: string;
  short_sha: string | null;
  db_mode: "shared" | "copy";
  database: string | null;
  lifecycle_state: LifecycleState;
  allocated_http_port: number | null;
  runtime: RuntimeMetrics;
  git: GitActivity;
  storage: StorageFootprint;
}

export interface Snapshot {
  schema_version: number;
  generated_at: string;
  projects: ProjectSummary[];
  environments: EnvironmentSnapshot[];
}

export async function fetchSnapshot(): Promise<Snapshot> {
  const res = await fetch("/api/v1/snapshot");
  if (!res.ok) {
    throw new Error(`snapshot fetch failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as Snapshot;
}
