## ADDED Requirements

### Requirement: Bounded whole-value assignment redaction

Whole-value redaction SHALL scan large key-shaped captured responses without retrying an unbounded secret-bearing pattern at every character. It SHALL preserve ordinary payload bytes and the established assignment secret detection, quoted/unquoted values and multiline whitespace semantics. The existing configured-secret, header, bearer, URI and JWT stages SHALL remain effective.

#### Scenario: Combined translation payload

- **WHEN** a captured response contains a password assignment and several large encoded translation catalogues
- **THEN** the assignment value is redacted and all ordinary catalogue bytes are preserved within the bounded regression timeout

#### Scenario: Adjacent and nested assignments

- **WHEN** secret assignments occur next to one another or inside an ordinary quoted value
- **THEN** each detected secret remains redacted without exposing its value
