package consumer

import (
	"encoding/json"
	"testing"
)

func TestExportedContractFixturesCompileAndDecode(t *testing.T) {
	request := OperationRequest{}
	result := OperationResult{Status: "ok"}
	err := FixtureError{Code: "invalid", Message: "safe"}
	alias := DepsMissingImport{Module: "base", Import: "odoo"}
	document := WireOperationDocument{
		Nested: WireNestedValue{Label: "nested"},
		Action: json.RawMessage(`{"type":"create","name":"new","count":0}`),
	}
	payload := OperationContractFixture{
		Request: request,
		Result:  result,
		Error:   err,
		Alias:   alias,
		Wire:    document,
		Plugin:  FixturePluginPayload{Value: "fixture"},
	}
	encoded, encodeErr := json.Marshal(payload)
	if encodeErr != nil {
		t.Fatal(encodeErr)
	}
	var decoded OperationContractFixture
	if decodeErr := json.Unmarshal(encoded, &decoded); decodeErr != nil {
		t.Fatal(decodeErr)
	}
	if decoded.Alias.Import != "odoo" || decoded.Plugin.Value != "fixture" {
		t.Fatalf("decoded contract fixture lost wire aliases or plugin payload: %#v", decoded)
	}
}
