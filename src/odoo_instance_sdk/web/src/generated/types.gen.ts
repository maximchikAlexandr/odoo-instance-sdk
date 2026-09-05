// @generated

export type ClientOptions = {
    baseUrl: `${string}://${string}` | (string & {});
};

/**
 * ClusterContainer
 */
export type ClusterContainer = {
    id: string | null;
    image: string | null;
    name: string | null;
    pid: number | null;
    pid_scope: PidScope;
};

/**
 * ClusterEndpoint
 */
export type ClusterEndpoint = {
    host: string;
    port: number;
};

/**
 * ClusterMetrics
 */
export type ClusterMetrics = {
    cpu_percent: number | null;
    memory_limit_bytes: number | null;
    memory_usage_bytes: number | null;
    sampled_at: string | null;
    volume_usage_bytes: number | null;
};

/**
 * ClusterSnapshot
 */
export type ClusterSnapshot = {
    container: null | ClusterContainer;
    endpoint: null | ClusterEndpoint;
    metrics: null | ClusterMetrics;
    mode: 'compose' | 'external';
    owned: boolean;
    sampled_at: string | null;
    server?: null | PostgresServerInfo;
    server_unavailability_reason?: 'authentication_failed' | 'credentials_missing' | 'invalid_response' | 'maintenance_database_unavailable' | 'privilege_denied' | 'psql_missing' | 'query_failed' | 'server_unreachable' | 'timeout' | null;
    state: PostgresClusterState;
    unavailability_reason: 'docker_unavailable' | 'external_not_owned' | 'inspect_failed' | 'missing' | 'stats_failed' | 'stopped' | null;
};

/**
 * DatabaseFootprint
 */
export type DatabaseFootprint = {
    filestore_bytes: number | null;
    owned: boolean;
    postgres_bytes: number | null;
    total_bytes: number | null;
};

/**
 * EnvironmentArtifacts
 */
export type EnvironmentArtifacts = {
    backup_exists: boolean | null;
    config_exists: boolean;
    dependency_lock_exists: boolean;
    python_contained: boolean;
    python_exists: boolean;
    worktree_exists: boolean;
    worktree_registered: boolean;
};

/**
 * EnvironmentSnapshot
 */
export type EnvironmentSnapshot = {
    allocated_http_port: number | null;
    artifacts: EnvironmentArtifacts;
    branch: string;
    database: string | null;
    db_mode: 'copy' | 'shared';
    git: GitActivity;
    id: string;
    lifecycle_state: EnvironmentState;
    name: string;
    observed_port: PortObservation | null;
    pgadmin: PgAdminEligibility;
    project_id: string;
    runtime: RuntimeMetrics;
    short_sha: string | null;
    storage: StorageFootprint;
};

/**
 * EnvironmentState
 *
 * Persisted lifecycle state shared by catalog and monitor contracts.
 */
export enum EnvironmentState {
    CLEANUP_FAILED = 'cleanup_failed',
    CREATING = 'creating',
    FAILED = 'failed',
    READY = 'ready',
    REMOVED = 'removed',
    REMOVING = 'removing'
}

/**
 * GitActivity
 */
export type GitActivity = {
    ahead: number | null;
    behind: number | null;
    branch: string;
    default_branch: string;
    diff: null | GitDiff;
    head_sha: string | null;
    short_sha: string | null;
    state: GitActivityState;
};

/**
 * GitActivityState
 */
export enum GitActivityState {
    AHEAD = 'ahead',
    BEHIND = 'behind',
    CLEAN = 'clean',
    DIVERGED = 'diverged',
    ORPHAN = 'orphan'
}

/**
 * GitDiff
 */
export type GitDiff = {
    added: number;
    deleted: number;
};

/**
 * HTTPValidationError
 */
export type HttpValidationError = {
    /**
     * Detail
     */
    detail?: Array<ValidationError>;
};

/**
 * HttpError
 */
export type HttpError = {
    code: HttpErrorCode;
    message: string;
};

/**
 * HttpErrorCode
 */
export enum HttpErrorCode {
    DATABASE_NOT_FOUND = 'database_not_found',
    ENVIRONMENT_NOT_FOUND = 'environment_not_found',
    INVALID_REQUEST = 'invalid_request',
    MONITOR_SNAPSHOT_FAILED = 'monitor_snapshot_failed',
    PGADMIN_NOT_ELIGIBLE = 'pgadmin_not_eligible',
    PGADMIN_UNAVAILABLE = 'pgadmin_unavailable'
}

/**
 * PgAdminEligibility
 */
export type PgAdminEligibility = {
    state: PgAdminEligibilityState;
};

/**
 * PgAdminEligibilityState
 */
export enum PgAdminEligibilityState {
    CLUSTER_NOT_OWNED = 'cluster_not_owned',
    CLUSTER_UNHEALTHY = 'cluster_unhealthy',
    DATABASE_UNRESOLVED = 'database_unresolved',
    ELIGIBLE = 'eligible',
    ENVIRONMENT_NOT_READY = 'environment_not_ready'
}

/**
 * PgAdminOpenRequest
 */
export type PgAdminOpenRequest = {
    environment_id: string;
};

/**
 * PgAdminOpenResult
 */
export type PgAdminOpenResult = {
    state: PgAdminOpenState;
    url: string;
};

/**
 * PgAdminOpenState
 */
export enum PgAdminOpenState {
    RECONFIGURED = 'reconfigured',
    REUSED = 'reused',
    STARTED = 'started'
}

/**
 * PidScope
 */
export enum PidScope {
    DOCKER_VM = 'docker_vm',
    HOST = 'host',
    UNAVAILABLE = 'unavailable'
}

/**
 * PortObservation
 */
export enum PortObservation {
    FREE = 'free',
    OCCUPIED = 'occupied',
    UNKNOWN = 'unknown'
}

/**
 * PostgresClusterState
 *
 * Lifecycle state of a project-level PostgreSQL cluster.
 *
 * UNKNOWN    — not probed (initial).
 * UNREACHABLE — endpoint not reachable.
 * STARTING   — compose up issued, not yet healthy.
 * HEALTHY    — ready.
 * STOPPED    — compose stopped.
 * UNHEALTHY  — running but healthcheck failing.
 */
export enum PostgresClusterState {
    HEALTHY = 'healthy',
    STARTING = 'starting',
    STOPPED = 'stopped',
    UNHEALTHY = 'unhealthy',
    UNKNOWN = 'unknown',
    UNREACHABLE = 'unreachable'
}

/**
 * PostgresServerInfo
 */
export type PostgresServerInfo = {
    connectable_databases: number;
    connections_active: number;
    connections_idle: number;
    connections_total: number;
    max_connections: number;
    postmaster_started_at: string;
    uptime_seconds: number;
    version: string;
};

/**
 * ProjectSummary
 */
export type ProjectSummary = {
    cluster: null | ClusterSnapshot;
    display_hint: string;
    environment_count: number;
    id: string;
    name: string;
    runtime: null | RuntimeMetrics;
};

/**
 * PythonEnvFootprint
 */
export type PythonEnvFootprint = {
    bytes: number | null;
    owned: boolean;
};

/**
 * RuntimeMetrics
 */
export type RuntimeMetrics = {
    branch: string | null;
    child_pids: Array<number>;
    commit_sha: string | null;
    cpu_percent: number | null;
    database_name: string | null;
    http_port: number | null;
    http_url: string | null;
    process_count: number;
    root_pid: number | null;
    rss_bytes: number | null;
    started_at: string | null;
    state: RuntimeState;
};

/**
 * RuntimeState
 */
export enum RuntimeState {
    NOT_READY = 'not_ready',
    READY = 'ready',
    STOPPED = 'stopped'
}

/**
 * Snapshot
 */
export type Snapshot = {
    environments: Array<EnvironmentSnapshot>;
    generated_at: string;
    projects: Array<ProjectSummary>;
    schema_version: number;
};

/**
 * StorageFootprint
 */
export type StorageFootprint = {
    complete: boolean;
    database: DatabaseFootprint;
    other_files_bytes: number | null;
    python_environment: PythonEnvFootprint;
    total_bytes: number;
    worktree_bytes: number | null;
};

/**
 * ValidationError
 */
export type ValidationError = {
    /**
     * Context
     */
    ctx?: {
        [key: string]: unknown;
    };
    /**
     * Input
     */
    input?: unknown;
    /**
     * Location
     */
    loc: Array<string | number>;
    /**
     * Message
     */
    msg: string;
    /**
     * Error Type
     */
    type: string;
};

export type OpenPgAdminData = {
    body: PgAdminOpenRequest;
    path?: never;
    query?: never;
    url: '/api/v1/pgadmin/open';
};

export type OpenPgAdminErrors = {
    /**
     * Environment not found
     */
    404: HttpError;
    /**
     * pgAdmin operation rejected
     */
    409: HttpError;
    /**
     * Invalid request
     */
    422: HttpError;
    /**
     * pgAdmin unavailable
     */
    503: HttpError;
};

export type OpenPgAdminError = OpenPgAdminErrors[keyof OpenPgAdminErrors];

export type OpenPgAdminResponses = {
    /**
     * pgAdmin opened
     */
    200: PgAdminOpenResult;
};

export type OpenPgAdminResponse = OpenPgAdminResponses[keyof OpenPgAdminResponses];

export type GetMonitorSnapshotData = {
    body?: never;
    path?: never;
    query?: {
        /**
         * Project Id
         */
        project_id?: string | null;
    };
    url: '/api/v1/snapshot';
};

export type GetMonitorSnapshotErrors = {
    /**
     * Validation Error
     */
    422: HttpValidationError;
    /**
     * Monitor snapshot failed
     */
    500: HttpError;
};

export type GetMonitorSnapshotError = GetMonitorSnapshotErrors[keyof GetMonitorSnapshotErrors];

export type GetMonitorSnapshotResponses = {
    /**
     * Successful Response
     */
    200: Snapshot;
};

export type GetMonitorSnapshotResponse = GetMonitorSnapshotResponses[keyof GetMonitorSnapshotResponses];

export type HealthzHealthzGetData = {
    body?: never;
    path?: never;
    query?: never;
    url: '/healthz';
};

export type HealthzHealthzGetResponses = {
    /**
     * Response Healthz Healthz Get
     *
     * Successful Response
     */
    200: {
        [key: string]: string;
    };
};

export type HealthzHealthzGetResponse = HealthzHealthzGetResponses[keyof HealthzHealthzGetResponses];
