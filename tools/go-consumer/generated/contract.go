// Code generated from tools/go-consumer/schema.json; DO NOT EDIT.

package consumer

import "encoding/json"

type OperationContractFixture struct {
	Request OperationRequest       `json:"request"`
	Result  FixtureResult          `json:"result"`
	Error   FixtureError           `json:"error"`
	Alias   DepsMissingImport      `json:"alias"`
	Wire    WireOperationDocument  `json:"wire"`
	Plugin  FixturePluginPayload   `json:"plugin"`
}

type OperationRequest struct {
	Project     *string `json:"project,omitempty"`
	Environment *string `json:"environment,omitempty"`
}

type OperationResult struct {
	Status string `json:"status,omitempty"`
}

type FixtureResult struct {
	Status   string                `json:"status"`
	Greeting string                `json:"greeting"`
	Payload  *FixturePluginPayload `json:"payload,omitempty"`
}

type OperationErrorDetails struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type FixtureError struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type DepsMissingImport struct {
	Module string `json:"module"`
	Import string `json:"import"`
}

type WireNestedValue struct {
	Label   string `json:"label"`
	Enabled *bool  `json:"enabled,omitempty"`
}

type WireCreateValue struct {
	Type  string `json:"type"`
	Name  string `json:"name"`
	Count int    `json:"count,omitempty"`
}

type WireDeleteValue struct {
	Type string `json:"type"`
	Name string `json:"name"`
}

type WireOperationDocument struct {
	Nested WireNestedValue `json:"nested"`
	Action json.RawMessage `json:"action"`
	Alias  *string         `json:"alias,omitempty"`
}

type FixturePluginPayload struct {
	Value string `json:"value"`
}
