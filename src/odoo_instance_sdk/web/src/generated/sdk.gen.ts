// @generated

import type { Client, ClientMeta, Options as Options2, RequestResult, TDataShape } from './client';
import { client } from './client.gen';
import type { GetMonitorSnapshotData, GetMonitorSnapshotErrors, GetMonitorSnapshotResponses, HealthzHealthzGetData, HealthzHealthzGetResponses, OpenPgAdminData, OpenPgAdminErrors, OpenPgAdminResponses } from './types.gen';

export type Options<TData extends TDataShape = TDataShape, ThrowOnError extends boolean = boolean, TResponse = unknown> = Options2<TData, ThrowOnError, TResponse> & {
    /**
     * You can provide a client instance returned by `createClient()` instead of
     * individual options. This might be also useful if you want to implement a
     * custom client.
     */
    client?: Client;
    /**
     * You can pass arbitrary values through the `meta` object. This can be
     * used to access values that aren't defined as part of the SDK function.
     */
    meta?: keyof ClientMeta extends never ? Record<string, unknown> : ClientMeta;
};

/**
 * Open Pgadmin
 */
export const openPgAdmin = <ThrowOnError extends boolean = false>(options: Options<OpenPgAdminData, ThrowOnError>): RequestResult<OpenPgAdminResponses, OpenPgAdminErrors, ThrowOnError> => (options.client ?? client).post<OpenPgAdminResponses, OpenPgAdminErrors, ThrowOnError>({
    url: '/api/v1/pgadmin/open',
    ...options,
    headers: {
        'Content-Type': 'application/json',
        ...options.headers
    }
});

/**
 * Snapshot
 */
export const getMonitorSnapshot = <ThrowOnError extends boolean = false>(options?: Options<GetMonitorSnapshotData, ThrowOnError>): RequestResult<GetMonitorSnapshotResponses, GetMonitorSnapshotErrors, ThrowOnError> => (options?.client ?? client).get<GetMonitorSnapshotResponses, GetMonitorSnapshotErrors, ThrowOnError>({ url: '/api/v1/snapshot', ...options });

/**
 * Healthz
 */
export const healthzHealthzGet = <ThrowOnError extends boolean = false>(options?: Options<HealthzHealthzGetData, ThrowOnError>): RequestResult<HealthzHealthzGetResponses, unknown, ThrowOnError> => (options?.client ?? client).get<HealthzHealthzGetResponses, unknown, ThrowOnError>({ url: '/healthz', ...options });
