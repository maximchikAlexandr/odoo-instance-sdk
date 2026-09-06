## ADDED Requirements

### Requirement: Streaming remote backup transfer

Remote database backup creation SHALL use the existing HTTPX streaming API and SHALL write response chunks directly to the existing exclusive `.part` artifact without buffering the complete response in memory. During the write it SHALL incrementally count bytes, calculate SHA-256, stop before accepting data beyond the configured size limit, and publish the final file atomically only after a successful complete transfer.

#### Scenario: Large response is not fully buffered

- **WHEN** a remote backup response is larger than the HTTP client's ordinary in-memory response body
- **THEN** the client consumes it as a stream and memory usage does not scale with the complete archive size
- **AND** the final catalogue size and SHA-256 match the published file

#### Scenario: Stream exceeds the limit

- **WHEN** the next response chunk would make received bytes exceed the configured maximum
- **THEN** the transfer fails, no final backup is published, and existing failed/download cleanup policy handles the `.part`

#### Scenario: Stream breaks

- **WHEN** the HTTP response terminates before successful completion
- **THEN** the catalogue does not mark the backup available and no partial file is renamed as final

### Requirement: Observable backup wait and transfer

Backup creation SHALL report waiting for response headers separately from transferring response bytes. Transfer progress SHALL report received bytes; it SHALL report a percentage only for a trustworthy `Content-Length` expressed in the same bytes and SHALL verify the final byte count against that length. Missing, encoded, invalid, or inconsistent length SHALL not be presented as a percentage.

#### Scenario: Reliable content length

- **WHEN** the response supplies a valid trustworthy `Content-Length`
- **THEN** Rich progress may show received bytes and percentage and completion requires the final byte count to match

#### Scenario: Unknown content length

- **WHEN** the response has no trustworthy byte total
- **THEN** progress shows waiting/status and received bytes without percentage

### Requirement: Interrupted backup retains truthful state

Interrupting local backup download SHALL close the HTTP response and file handle, return the known backup UUID and retained-artifact state, and SHALL not claim that closing the client stopped remote archive generation. A successfully published backup SHALL not be deleted merely because a later restore or preparation step is interrupted.

#### Scenario: Interrupt before transfer

- **WHEN** Ctrl-C occurs while the remote server is preparing the response
- **THEN** the local request closes, the catalogue does not report an available archive, and the CLI exits 130 with the known backup UUID

#### Scenario: Interrupt after publication

- **WHEN** Ctrl-C occurs after the archive was atomically published
- **THEN** the available backup remains in the catalogue and on disk and is reported as retained
